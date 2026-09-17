# Architecture

## Data model

FastTrack uses six domain tables for operational data and three workflow/state tables for the investigation lifecycle.

### Domain tables

The five retrievable evidence tables — `telemetry_events`, `deployments`, `source_changes`, `runbooks`, and `prior_incidents` — include an `embedding VECTOR(1536)` column for similarity search.

- `telemetry_events` — service, level, message, host, tags, timestamp
- `deployments` — service, version, commit_sha, environment, status, deployed_at
- `source_changes` — repo, commit_sha, author, message, files_changed, diff_stat, timestamp
- `runbooks` — title, service, content, tags, updated_at
- `prior_incidents` — title, service, summary, root_cause, resolution, occurred_at
- `alerts` — service, severity, message, fingerprint, status

`alerts` are workflow inputs rather than retrievable evidence records, so they do not require an embedding column.

The `fingerprint` field on `alerts` is unique and acts as the ingestion idempotency key. Runbook staleness is derived from `updated_at` at read time rather than stored as a separate field.

### Workflow/state tables

- `investigations` — alert_id, status, diagnosis JSON, suggested_action, postmortem_draft
- `tool_execution_logs` — investigation_id, tool_name, input/output JSON, status, error_code, latency_ms
- `approvals` — investigation_id, action, decision, decided_at

`approvals` is the source of truth for the approval gate. Repeated approval requests first check for an existing row instead of relying only on the investigation status, which makes approval idempotent under retries.

## pgvector indexing

Each retrievable evidence table has an HNSW index on its embedding column using cosine distance. The indexes are created explicitly in the Alembic migration:

```sql
CREATE INDEX ix_<table>_embedding_hnsw
ON <table>
USING hnsw (embedding vector_cosine_ops);
```

HNSW provides approximate nearest-neighbor search rather than an exact brute-force scan. The tradeoff is lower search cost as the corpus grows in exchange for approximate rather than exact nearest-neighbor retrieval.

At the current seed size, PostgreSQL may still choose a sequential scan depending on planner cost estimates. The HNSW indexes are present in the schema and become increasingly useful as the evidence corpus grows.

A useful implementation detail surfaced while testing vector failures: pgvector's Python client validates embedding dimensionality before the query reaches PostgreSQL. A malformed vector can therefore raise `ValueError`, commonly wrapped by SQLAlchemy as `StatementError`, rather than a database-side error.

The vector-search tools normalize both client-side validation failures and database-side pgvector failures into the same structured `vector_search_failed` error path.

Relevant implementations:

- `app/tools/retrieve_runbook.py`
- `app/tools/search_prior_incidents.py`

## Provider abstractions

FastTrack separates application logic from the model and embedding providers so the system can run deterministically in tests, CI, and benchmarks while still supporting a real OpenAI-backed path.

### Embedding provider

```python
EmbeddingProvider.embed(text) -> list[float]
```

The implementations live under `app/embeddings/`.

#### DeterministicEmbeddingProvider

The default provider uses a deterministic hashing-based embedding.

Each normalized token maps to a fixed pseudo-random vector derived from its SHA-256 hash. Token vectors are combined using term-frequency weighting and then L2-normalized.

This gives several useful properties:

- identical input produces the same vector across runs
- shared vocabulary increases vector similarity
- no network access or API key is required
- pgvector integration remains real even when the embedding provider is local

The deterministic embedding is intentionally not equivalent to a trained semantic model. Texts with similar meaning but different vocabulary may not be placed near each other.

#### OpenAIEmbeddingProvider

The OpenAI-backed implementation uses `text-embedding-3-small`.

It is selected only when:

```text
EMBEDDING_PROVIDER=openai
```

and a valid:

```text
OPENAI_API_KEY
```

is available.

Selecting the OpenAI provider without a key raises `ConfigurationError`. FastTrack never silently falls back to the deterministic provider.

## Reasoning provider

```python
ReasoningProvider.decide_next_action(context)
    -> ToolCallAction | FinalizeAction
```

The implementations live under `app/reasoning/`.

### DeterministicReasoningProvider

The default reasoning provider is deterministic but context-sensitive.

It does not return one hardcoded response. Instead, it examines the evidence collected so far and decides which diagnostic tool to call next.

A typical path is:

```text
telemetry
    ↓
deployments, if elevated evidence is present
    ↓
inspect_change, if a deployment correlates
    ↓
runbook
    ↓
prior incidents
    ↓
finalize
```

The loop is bounded to at most five diagnostic tool calls.

When finalizing, the provider:

- builds `evidence_refs` only from records actually returned during the current investigation
- computes confidence from a deterministic formula
- lowers confidence when evidence conflicts
- identifies disagreement among prior incidents instead of hiding it

### OpenAIProvider

The OpenAI reasoning implementation uses the same five diagnostic tool schemas through function calling.

The final model response is validated against the `Diagnosis` schema. Invalid structured output receives one retry before the investigation fails with a typed error.

Like the embedding provider, selecting OpenAI without a configured API key fails explicitly.

## Diagnostic tools

FastTrack exposes exactly five read-only diagnostic tools:

1. `search_telemetry`
2. `search_deployments`
3. `retrieve_runbook`
4. `search_prior_incidents`
5. `inspect_change`

They are implemented under `app/tools/` and registered through the shared tool registry.

Each tool has:

- a Pydantic input schema
- a Pydantic output schema
- a bounded SQL query
- explicit ordering
- a server-side result limit
- read-only behavior

The same input schemas can also be emitted as JSON schemas for OpenAI function calling.

All tool execution passes through `app/tools/executor.py`, which performs the following steps:

1. validate input against the tool's Pydantic input model
2. execute the handler
3. translate expected database, timeout, and vector failures into structured error codes
4. validate the handler output against the tool's output model
5. record a `ToolExecutionLog` row with latency and status
6. update Prometheus metrics

Result limits are enforced in application code rather than relying only on validation. A caller requesting an excessive number of records still receives at most the configured maximum.

## Investigation pipeline

`app/pipeline/investigation.py` runs the bounded diagnostic loop.

The maximum number of reasoning iterations is configured through:

```text
agent_max_iterations
```

with a default of 6.

Each iteration asks the reasoning provider for its next action. The provider may either:

- request one of the five diagnostic tools, or
- return a `FinalizeAction`

If the reasoning provider becomes unavailable or returns invalid output, the investigation is marked `failed` with a structured reason rather than causing an unhandled server error.

## Evidence validation

Before a diagnosis is accepted, `app/pipeline/diagnosis_validation.py` validates every `evidence_ref`.

The important rule is that an evidence reference must have been returned by a tool during the current investigation.

FastTrack does not merely ask:

```text
Does this database record exist?
```

It asks:

```text
Was this exact record actually retrieved during this investigation?
```

This prevents a diagnosis from citing a real database row that the reasoning provider never observed.

If validation fails, the provider receives one opportunity to finalize again. A second failure marks the investigation as:

```text
diagnosis_invalid
```

## Approval gate and postmortem

The approval flow is exposed through:

```text
POST /investigations/{id}/approve
POST /investigations/{id}/reject
```

An investigation must be in the `completed` state before either action is accepted.

Approval and rejection are idempotent. Before writing a new decision, the application checks whether an `Approval` row already exists for the investigation.

Repeated requests therefore return the existing result without creating duplicate side effects.

An approved investigation generates a postmortem draft through:

```text
app/pipeline/postmortem.py
```

Postmortem generation is deterministic and uses the already-validated diagnosis. It does not make a new LLM call, which keeps the generated document auditable and independent of the active reasoning provider.

## Error handling

Expected failures are represented by typed exceptions in:

```text
app/errors.py
```

All `FastTrackError` subclasses are converted into a consistent JSON response:

```json
{
  "error_code": "...",
  "message": "...",
  "detail": {}
}
```

FastAPI request-validation errors and SQLAlchemy failures also have dedicated handlers.

This means database failures outside tool execution — such as ingestion or approval failures — still return structured responses instead of unhandled HTTP 500 errors.

## Metrics and dashboards

FastTrack exposes eight Prometheus metrics:

- `http_request_duration_seconds`
- `tool_execution_duration_seconds`
- `tool_execution_failures_total`
- `retrieval_duration_seconds`
- `ingestion_records_total`
- `investigations_total`
- `llm_call_duration_seconds`
- `llm_calls_total`

The Grafana configuration provisions eight panels automatically through Docker Compose:

1. API request rate
2. API latency percentiles
3. tool execution duration by tool
4. tool failure rate by tool and error code
5. retrieval latency
6. ingestion throughput
7. investigations by status
8. reasoning-provider latency and error rate

The dashboard configuration lives at:

```text
monitoring/grafana/dashboards/fasttrack.json
```

## Testing strategy

FastTrack currently has 49 functional and integration tests.

The tests exercise the application against real PostgreSQL and pgvector rather than replacing the database with an in-memory mock.

The OpenAI reasoning adapter's request/response mapping is tested separately through a fake transport. This verifies tool-call parsing and structured finalization without requiring a live OpenAI request.

The OpenAI embedding adapter is implemented behind the same `EmbeddingProvider` interface but is not exercised by the automated suite. Exercising it requires selecting the OpenAI provider and supplying a real `OPENAI_API_KEY`.

### Fault injection

The fault-injection framework lives entirely under:

```text
tests/faults/
```

No fault-injection logic is shipped inside `app/`.

The test harness overrides ordinary application seams such as:

- database sessions
- provider factories
- tool registry behavior

The suite contains 250 parametrized fault-injection cases:

```text
10 fault types
× 5 targets
× 5 input variations
= 250 cases
```

These cases remain separate from the 49 functional/integration tests rather than being presented as one combined test-count headline.

The matrix covers failures such as:

- database unavailability
- malformed telemetry
- missing deployments
- stale runbooks
- conflicting evidence
- reasoning-provider timeouts
- invalid tool output
- duplicate alerts
- vector-query failures
- unavailable dependencies

The goal is to verify that expected failures are converted into structured application behavior rather than unhandled exceptions.
