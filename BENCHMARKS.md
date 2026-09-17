# Benchmarks

## Scope

The measurements in this document were produced by `scripts/benchmark.py` using FastTrack's deterministic providers:

```text
EMBEDDING_PROVIDER=deterministic
LLM_PROVIDER=deterministic
```

The benchmark is intended to characterize FastTrack's own backend execution path rather than external model-serving latency.

Depending on the benchmarked path, that includes:

```text
HTTP / ASGI handling
→ validation
→ PostgreSQL
→ pgvector retrieval
→ diagnostic tool execution
→ deterministic reasoning
→ diagnosis validation
→ serialization
```

Live OpenAI latency is intentionally excluded.

When `LLM_PROVIDER=openai` or `EMBEDDING_PROVIDER=openai` is selected, request latency also includes network and provider-serving time. Those measurements are environment-dependent and are not reported here.

## Reproducing the benchmark

Start PostgreSQL and apply the schema:

```bash
docker compose up -d db
alembic upgrade head
python scripts/seed.py
```

Start the API:

```bash
uvicorn app.main:app
```

or run the full stack:

```bash
docker compose up -d --build
```

Then execute:

```bash
python scripts/benchmark.py --n 200 --warmup 20
```

The benchmark performs 20 warmup operations before recording 200 timed operations for each path.

## Test environment

The recorded runs were performed on an Apple Silicon development machine with PostgreSQL and pgvector running through Docker Desktop.

The database contained:

```text
1,248 deterministically seeded evidence records
```

Latency varies with machine load, database state, Docker scheduling, caching, and other local conditions. For that reason, results below show three independent runs instead of presenting a single run as universally representative.

## Results

| Path | Run 1 p95 | Run 2 p95 | Run 3 p95 | Observed p95 range |
|---|---:|---:|---:|---:|
| Retrieval | 7.2 ms | 8.5 ms | 7.7 ms | 7.2–8.5 ms |
| Ingestion | 9.9 ms | 10.1 ms | 12.3 ms | 9.9–12.3 ms |
| Incident analysis | 15.83 ms | 17.27 ms | 12.97 ms | 13.0–17.3 ms |

Across the three recorded 200-request runs, full incident-analysis p95 ranged from:

```text
13.0 ms to 17.3 ms
```

When a single conservative summary value is useful, this repository uses:

```text
17.3 ms p95
```

This is the highest incident-analysis p95 observed across the three recorded runs rather than the fastest result.

## Benchmark paths

### Retrieval

The retrieval benchmark executes one `retrieve_runbook` operation directly in-process.

The measured path includes:

```text
query embedding
→ pgvector cosine-distance query
→ result validation
→ serialization
```

It does not pass through the HTTP/ASGI layer, which makes it useful for isolating the vector-retrieval and database portion of the system.

The schema contains HNSW indexes using `vector_cosine_ops`, but PostgreSQL remains free to choose the execution plan it considers cheapest. At the current dataset size, the benchmark does not assume that every retrieval operation necessarily uses the HNSW index.

### Ingestion

The ingestion benchmark sends:

```text
POST /telemetry
```

The measured path includes:

```text
HTTP request handling
→ Pydantic validation
→ deterministic embedding generation
→ PostgreSQL INSERT
→ response serialization
```

This represents a relatively small write path in FastTrack.

### Incident analysis

The incident-analysis benchmark sends:

```text
POST /investigations/{id}/run
```

For the benchmarked fixture, the deterministic reasoning policy exercises all five diagnostic tools before finalization.

The path includes:

```text
HTTP request handling
→ bounded reasoning loop
→ telemetry lookup
→ deployment lookup
→ source-change inspection
→ runbook retrieval
→ prior-incident retrieval
→ tool-output validation
→ evidence-reference validation
→ diagnosis finalization
→ response serialization
```

This is the broadest measured request path in the benchmark suite.

## Interpreting the results

The incident-analysis benchmark involves several sequential database-backed diagnostic operations, so its latency is higher than either a single retrieval or a telemetry write.

The benchmarked reasoning provider is deterministic and runs locally. It therefore does not model the latency of a remotely hosted language model.

Potential ways to reduce backend latency further include:

- parallelizing diagnostic operations that do not depend on one another
- reducing unnecessary database round-trips
- batching compatible retrieval operations
- tuning PostgreSQL and pgvector as the evidence corpus grows
- revisiting the maximum reasoning-iteration bound for different workloads

Those are architectural options rather than conclusions drawn from the current benchmark alone.

## Limitations

These results are intended to characterize local backend execution, not production capacity.

The benchmark does **not** measure:

- concurrent user load
- sustained throughput
- connection-pool saturation
- multi-process API deployment
- distributed database latency
- cross-region networking
- live OpenAI latency
- production-scale evidence corpora
- HNSW recall under large-scale approximate search

The benchmark runs on a single development machine against a locally hosted Dockerized database.

Results should therefore be interpreted as reproducible development measurements of FastTrack's code paths, not as production service-level guarantees.

To obtain current numbers on another machine or database state, rerun:

```bash
python scripts/benchmark.py --n 200 --warmup 20
```
