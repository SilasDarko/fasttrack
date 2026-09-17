# Design Decisions

This document records implementation choices that are not obvious from the code alone, along with the tradeoffs behind them.

## Deterministic providers are first-class implementations

FastTrack defaults to `DeterministicEmbeddingProvider` and `DeterministicReasoningProvider` rather than treating deterministic behavior as a test-only mock.

Both implementations use the same interfaces as the OpenAI-backed providers.

The deterministic reasoning provider examines accumulated tool results and changes its next action based on the evidence available. For example, elevated telemetry can lead to deployment inspection, a correlated deployment can lead to source-change inspection, and conflicting prior incidents affect the final diagnosis.

The deterministic embedding provider produces stable 1536-dimensional vectors that are stored and queried through the same PostgreSQL + pgvector path used by the OpenAI embedding provider.

This design allows:

- CI to run without external network access
- benchmarks to measure FastTrack rather than model-provider latency
- seed generation to remain reproducible
- tool orchestration and evidence validation to run end-to-end
- provider-specific behavior to remain isolated behind common interfaces

The OpenAI-backed implementations remain available through:

```text
EMBEDDING_PROVIDER=openai
LLM_PROVIDER=openai
OPENAI_API_KEY=...
```

Selecting an OpenAI provider without a configured API key fails explicitly rather than silently changing providers.

### Tradeoff

The deterministic embedding is based on token hashing rather than a trained semantic model.

Shared vocabulary tends to increase similarity, but semantically equivalent phrases with different vocabulary may still be far apart. For example:

```text
connection pool exhausted
```

and:

```text
too many open connections
```

may describe the same operational condition without producing the similarity a trained embedding model would.

Retrieval tests therefore use controlled phrasing or hand-constructed vectors when the test is intended to verify ranking behavior independently of embedding quality.

## Evidence validation uses investigation history

A diagnosis may reference only evidence that was actually returned during the current investigation.

`app/pipeline/diagnosis_validation.py` checks each `evidence_ref` against the `(type, id)` pairs collected from tool outputs.

This is intentionally stricter than checking only whether a referenced row exists in the database.

A database-only check could accept a valid row that the reasoning provider never retrieved. That would allow a diagnosis to cite information outside its observed evidence.

The validation therefore asks two separate questions:

```text
Was this record returned during this investigation?
Does the referenced record still resolve correctly?
```

The second check provides defense in depth against stale or invalid tool output.

## Postmortem generation does not invoke the reasoning provider again

Postmortems are generated deterministically from the validated `Diagnosis` object in:

```text
app/pipeline/postmortem.py
```

The approval path does not make another LLM call.

This keeps the generated document tied to evidence that has already passed diagnosis validation and avoids introducing a new opportunity for unsupported content during postmortem generation.

It also keeps approval behavior reproducible regardless of which reasoning provider produced the original diagnosis.

## Approval state has its own table

Approval state is stored in `approvals` instead of being represented only by `Investigation.status`.

These concepts describe different things:

```text
Investigation.status
    → where the investigation is in its lifecycle

Approval
    → the recorded decision associated with that investigation
```

The approval endpoint checks for an existing decision before creating another one.

This makes repeated approval or rejection requests idempotent and avoids repeating side effects such as postmortem generation or metric updates during client retries.

## Alert deduplication handles concurrent inserts

Alert fingerprints have a database uniqueness constraint.

A simple implementation could:

```text
SELECT by fingerprint
→ if missing
→ INSERT
```

but that is not enough under concurrency.

Two requests can perform the initial lookup at nearly the same time, both observe no row, and then both attempt the insert.

FastTrack lets the database uniqueness constraint resolve the race. If the insert loses with an `IntegrityError`, `ingest_alert`:

1. rolls back the failed transaction
2. queries the fingerprint again
3. returns the alert created by the competing request

The losing request therefore receives the existing alert instead of an internal server error.

This behavior is covered by the duplicate-alert tests and fault-injection cases.

## The initial migration is intentionally consolidated

`alembic/versions/0001_initial.py` represents the first complete FastTrack schema.

It:

- creates the pgvector extension
- creates the application tables
- creates the vector indexes
- establishes the initial database structure in one migration

Because this is the first schema version, there is no earlier migration history to preserve.

Future schema changes should be represented as normal incremental Alembic revisions rather than modifying `0001_initial.py` after the schema has become shared history.

## Tool result limits are clamped in the handlers

Diagnostic tool inputs require a positive `limit`, but the caller is not trusted to choose an appropriate maximum.

The actual query limit is clamped against FastTrack configuration such as:

```text
tool_max_limit
retrieval_max_limit
```

This means a request such as:

```text
limit=999
```

does not cause an unbounded query.

The handler executes using the configured maximum instead.

Clamping was chosen instead of rejecting the request because an excessive result request from a reasoning provider is recoverable. The tool can still return a useful bounded result without turning the interaction into a schema-validation failure.

## Fault injection remains outside production code

Fault-injection utilities live under:

```text
tests/faults/
```

Production code does not import them.

The application exposes ordinary seams that are useful independently of testing:

- database-session dependencies
- provider factories
- tool registration and execution boundaries

The fault harness overrides those seams from the test side.

This keeps production modules free of test-specific switches while still allowing failures to be reproduced deterministically.

## The fault matrix uses scenario-specific targets

The fault matrix contains:

```text
10 fault types
× 5 targets
× 5 input variations
= 250 parametrized cases
```

The meaning of `target` is intentionally specific to the failure being tested rather than being forced to mean “one of the five tools” in every case.

For failures that genuinely apply to all tools, such as database timeouts or invalid tool outputs, the five targets correspond to the five diagnostic tools.

For scenario-specific failures, the target dimension instead represents five meaningful variants of that failure.

Examples include:

- database timeout at different tool boundaries
- missing-deployment variants
- stale-runbook variants
- conflicting-evidence variants
- reasoning-provider timeout variants
- duplicate-alert race variants
- dependency failures at different application boundaries

### Vector-query failures

Vector-search failures deserve separate handling because they can originate at different layers.

One case discovered during implementation was an embedding dimension mismatch.

The pgvector Python integration validates vector dimensionality before the SQL query reaches PostgreSQL. Depending on where the exception is surfaced, this can appear as a Python `ValueError` or as a SQLAlchemy `StatementError`.

Other failures may originate from PostgreSQL or the pgvector operator path and surface as database exceptions.

The retrieval tools normalize these expected vector-search failures into:

```text
vector_search_failed
```

rather than leaking library-specific exceptions through the API.

`variation_idx` changes the input associated with each target so the suite does not prove behavior using only one fixed payload.

The detailed scenario definitions live in:

```text
tests/faults/scenarios.py
```

## Alert ingestion and investigation execution are separate operations

Creating an alert and analyzing an incident are exposed as separate HTTP operations.

```text
POST /alerts
```

creates the alert and its pending investigation.

```text
POST /investigations/{id}/run
```

executes the diagnostic pipeline.

The two operations have very different cost profiles.

Alert ingestion is primarily a small database write, while investigation execution can involve:

- several diagnostic tool calls
- multiple PostgreSQL queries
- vector retrieval
- reasoning-provider decisions
- diagnosis validation

Keeping them separate makes retries, observability, error handling, and latency measurement easier to reason about.

It also prevents the inexpensive alert-ingestion path from being coupled to the much larger analysis path.

The two operations are benchmarked separately in `BENCHMARKS.md`.
