# Benchmarks

## Scope

All numbers below were produced by `scripts/benchmark.py` against the deterministic provider (`EMBEDDING_PROVIDER=deterministic`, `LLM_PROVIDER=deterministic`) -- they measure FastTrack's own backend path:

**HTTP routing -> PostgreSQL -> pgvector -> tool dispatch -> deterministic reasoning -> serialization.**

They do **not** include live OpenAI network latency. That is a deliberate choice, not an oversight: OpenAI call latency is dominated by network and model-serving time outside this codebase, and including it would make the number non-reproducible and would not measure anything this project controls. If you configure `LLM_PROVIDER=openai`, expect the `incident_analysis` path to be dominated by the OpenAI round-trip(s) instead -- that is a separate, environment-dependent measurement and is not reported here.

## How to reproduce

```bash
docker compose up -d db
alembic upgrade head
python scripts/seed.py
uvicorn app.main:app &          # or: docker compose up -d --build
python scripts/benchmark.py --n 200 --warmup 20
```

## Measured results

Run on a local machine (Apple Silicon, Docker Desktop for the Postgres/pgvector container, API in the same Docker Compose stack), against a database seeded with 1,248 evidence records, 200 timed requests per path after 20 warmup requests. Latency varies run to run with system load and what's already in the database, so rather than report one run as if it were universally representative, each path below was measured across **three independent runs**:

| path | run 1 p95 | run 2 p95 | run 3 p95 | p95 range |
|---|---|---|---|---|
| retrieval (`retrieve_runbook`, in-process) | 7.2 ms | 8.5 ms | 7.7 ms | 7.2 ms – 8.5 ms |
| ingestion (`POST /telemetry`) | 9.9 ms | 10.1 ms | 12.3 ms | 9.9 ms – 12.3 ms |
| incident analysis (`POST /investigations/{id}/run`, full 5-tool agent loop) | 15.83 ms | 17.27 ms | 12.97 ms | 13.0 ms – 17.3 ms |

Across three independent 200-request runs, incident-analysis p95 ranged from 13.0 ms to 17.3 ms. Where a single summary number is needed elsewhere in this repo, use the conservative end of that range: **17.3 ms p95**.

What each path actually does:

- **retrieval** -- one `retrieve_runbook` tool call (embed the query, run the pgvector HNSW cosine-distance query, validate + serialize the output), called directly in-process (no HTTP/ASGI layer) so it isolates the DB/vector-search cost specifically.
- **ingestion** -- one `POST /telemetry` request: request parsing, Pydantic validation, embedding the record, one INSERT, response serialization -- the full HTTP path for the cheapest write.
- **incident analysis** -- one `POST /investigations/{id}/run` request: the full bounded agent loop, which in the benchmarked case runs all 5 tool calls (telemetry, deployments, runbook, prior incidents, plus whatever the deterministic policy adds) before finalizing and validating the diagnosis. This is the most expensive path by design -- it is doing five times the tool work of a single retrieval call, plus the diagnosis-validation pass.

## Reading these numbers

Incident-analysis p95 (13.0 ms – 17.3 ms across three runs) is well within typical interactive-latency budgets for an operator-facing tool, and is dominated by the number of sequential tool calls the deterministic policy makes (each is a real round-trip to Postgres), not by any single slow operation. Reducing it further would mean either parallelizing independent tool calls (telemetry and runbook lookups don't depend on each other) or reducing the default agent bound -- not something this benchmark implies is currently necessary, but the lever is there in `app/pipeline/investigation.py` if the workload changes.

These are single-machine, single-process numbers meant to characterize the code path, not a load test -- there is no concurrency or connection-pool contention modeled here. p50 and max also vary run to run in the same way; re-run `scripts/benchmark.py` for current figures rather than treating any one run (including the ones in the table above) as fixed.
