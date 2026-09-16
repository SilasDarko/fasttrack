# Architecture

## Data model

Six evidence/domain tables hold the raw operational record; three workflow/state tables hold the investigation lifecycle.

**Evidence/domain** (each with an `embedding VECTOR(1536)` column):
- `telemetry_events` -- service, level, message, host, tags, timestamp
- `deployments` -- service, version, commit_sha, environment, status, deployed_at
- `source_changes` -- repo, commit_sha (unique), author, message, files_changed, diff_stat, timestamp
- `runbooks` -- title, service, content, tags, updated_at (staleness is derived from this at read time, not stored)
- `prior_incidents` -- title, service, summary, root_cause, resolution, occurred_at
- `alerts` -- service, severity, message, fingerprint (unique -- the ingestion idempotency key), status

**Workflow/state**:
- `investigations` -- alert_id, status (`pending` -> `completed`|`failed` -> `approved`|`rejected`), diagnosis (JSON), suggested_action, postmortem_draft
- `tool_execution_logs` -- one row per tool call: investigation_id, tool_name, input/output JSON, status, error_code, latency_ms
- `approvals` -- investigation_id, action, decision (`approved`|`rejected`), decided_at -- the source of truth for the approval gate; re-approving looks up an existing row here rather than trusting a status enum, which is what makes approval idempotent under retries

## pgvector indexing

Each evidence table's `embedding` column has a real `HNSW` index with the cosine-distance operator class, created explicitly in the Alembic migration:

```sql
CREATE INDEX ix_<table>_embedding_hnsw ON <table> USING hnsw (embedding vector_cosine_ops);
```

This is approximate nearest-neighbor search, not exact/brute-force scan -- the tradeoff is standard for pgvector: HNSW gives sub-linear query time at the cost of a small amount of recall versus exact search, which is the right tradeoff once a table has more than a few thousand rows. At the current seed size (a few hundred rows per table) the two are indistinguishable in practice; the index is still real and exercised by every `retrieve_runbook` / `search_prior_incidents` call, and by the fault-injection matrix's `vector_query_failure` cases.

A real, separately useful discovery from building this: pgvector's Python client validates the embedding's dimensionality *before* the query ever reaches Postgres, raising `ValueError` (wrapped by SQLAlchemy as `StatementError`) rather than a server-side error. The tool executor treats that identically to a genuine server-side pgvector failure (`DBAPIError`) -- both surface as `vector_search_failed`. See `app/tools/retrieve_runbook.py` / `search_prior_incidents.py`.

## Provider abstractions

Two seams make the AI layer swappable and the test suite network-free:

**`EmbeddingProvider.embed(text) -> list[float]`** (`app/embeddings/`)
- `DeterministicEmbeddingProvider` (default): a hashing-trick / random-projection embedding. Each distinct token maps to a fixed pseudo-random unit vector seeded from its SHA-256 hash; a text's embedding is the term-frequency-weighted sum of its tokens' vectors, L2-normalized. Same text -> identical vector, always; shared vocabulary between two texts pulls their vectors closer, which is what makes similarity ranking meaningful without any model or network call. It is not semantically meaningful the way a trained embedding is -- see DESIGN_DECISIONS.md.
- `OpenAIEmbeddingProvider`: real `text-embedding-3-small` calls, used only when `EMBEDDING_PROVIDER=openai` and `OPENAI_API_KEY` is set.
- Selecting `openai` without a key raises `ConfigurationError` at startup -- it never silently falls back to the deterministic provider.

**`ReasoningProvider.decide_next_action(context) -> ToolCallAction | FinalizeAction`** (`app/reasoning/`)
- `DeterministicReasoningProvider` (default): a fixed but context-sensitive policy, not a single hardcoded response. It inspects the tool outputs accumulated so far and branches: telemetry -> (deployments, if elevated severity) -> (inspect_change, if a deployment correlates) -> runbook -> prior incidents -> finalize, bounded to 5 tool calls. Finalizing builds the diagnosis's `evidence_refs` **only** from records actually present in tool outputs already in the conversation, computes confidence from a deterministic formula, and detects+flags conflicting prior-incident evidence.
- `OpenAIProvider`: a real function-calling loop against the same 5 tool JSON schemas, finalized via a structured JSON response validated against the `Diagnosis` schema, with one retry on invalid output.
- Same fail-fast rule on a missing API key.

## The 5 diagnostic tools

`app/tools/{search_telemetry,search_deployments,retrieve_runbook,search_prior_incidents,inspect_change}.py`, registered in `app/tools/base.py`. Every tool: has a Pydantic input schema (also emitted as an OpenAI function-calling JSON schema) and output schema; runs one explicitly-ordered, `LIMIT`-clamped query (clamped in code, not just validated, so a caller requesting 999 results still gets at most `tool_max_limit`); is read-only; and is invoked exclusively through `app/tools/executor.py`, which:

1. validates input against the tool's input model,
2. runs the handler, translating `VectorSearchError`, `(OperationalError, DBAPIError, StatementError, TimeoutError)` into structured `error_code`s instead of letting them propagate,
3. validates the handler's output against the tool's output model (`tool_output_invalid` on failure),
4. logs a `ToolExecutionLog` row with latency, and
5. records Prometheus metrics.

## Investigation pipeline

`app/pipeline/investigation.py` runs the bounded agent loop (`app/config.py: agent_max_iterations`, default 6): each iteration asks the reasoning provider for the next action, executes a tool call or breaks on `FinalizeAction`. If the reasoning provider itself is unavailable (`LLMUnavailableError`/`LLMOutputInvalidError`) the investigation is marked `failed` with a structured reason rather than crashing the request.

Before a diagnosis is accepted, `app/pipeline/diagnosis_validation.py` checks every `evidence_ref` against the set of (type, id) pairs actually returned by this investigation's tool calls -- **not** just against "does this id exist in the database" -- which is what prevents a hallucinated-but-real-looking id from passing. A failing diagnosis gets exactly one retry (asking the provider to finalize again); a second failure marks the investigation `failed` with `diagnosis_invalid`.

## Approval gate and postmortem

`POST /investigations/{id}/approve|reject` requires the investigation to be `completed` (409 otherwise) and is idempotent: it looks for an existing `Approval` row for that investigation before writing a new one, so retried or duplicate approval requests return the same result without side effects. Approval generates a postmortem draft via `app/pipeline/postmortem.py`, a deterministic template over the already-validated `Diagnosis` -- not a fresh LLM call -- so the document is auditable and provider-independent.

## Errors

All expected failures are typed (`app/errors.py`, `FastTrackError` subclasses) and rendered as a consistent `{error_code, message, detail}` JSON body by a single exception handler, plus dedicated handlers for FastAPI's own `RequestValidationError` and for `SQLAlchemyError` (so a DB failure outside a tool call -- e.g. during ingestion or approval -- is still a structured 503, never a bare 500).

## Metrics and dashboards

8 Prometheus metrics (`app/metrics.py`): `http_request_duration_seconds`, `tool_execution_duration_seconds`, `tool_execution_failures_total`, `retrieval_duration_seconds`, `ingestion_records_total`, `investigations_total`, `llm_call_duration_seconds`, `llm_calls_total`. `monitoring/grafana/dashboards/fasttrack.json` provisions an 8-panel dashboard (request rate, latency percentiles, tool duration/failure by tool, retrieval latency by evidence type, ingestion throughput, investigations by status, LLM latency/error rate) automatically on `docker compose up`.

## Testing strategy

`tests/*.py` (49 functional/integration tests) exercise the application through real Postgres+pgvector -- no mocking of the database, ever. The OpenAI *reasoning* adapter's request/response mapping (`app/reasoning/openai_provider.py`, 86% covered) is tested separately (`tests/test_openai_adapter.py`) against a fake transport, verifying tool-call and finalize-response parsing without any live API call. The OpenAI *embedding* adapter (`app/embeddings/openai_provider.py`) is implemented behind the same `EmbeddingProvider` interface but is not exercised by the automated suite (0% coverage) -- there is no fake-transport test for it yet, and it is only ever run manually with a real `OPENAI_API_KEY`.

`tests/faults/` is a **test-only** fault-injection harness (nothing here ships in `app/`): it monkeypatches the ordinary dependency seams application code already exposes (a `session` parameter, provider factories, the tool registry) to reproduce 10 realistic failure modes, each applied across 5 targets x 5 input variations = 250 parametrized cases in `tests/test_fault_injection.py`. See DESIGN_DECISIONS.md for what "target" means per fault type and why the matrix is shaped this way.
