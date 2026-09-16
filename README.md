# FastTrack

FastTrack is a FastAPI service that investigates production incidents by correlating telemetry, deployments, source changes, runbooks, and prior incidents, then produces an evidence-backed diagnosis through a bounded, tool-using reasoning agent. A human approval gate stands between the diagnosis and any suggested action, and an approved investigation generates a postmortem draft automatically.

The reasoning and embedding layers are pluggable: a deterministic, network-free implementation is used by default (and by all tests, CI, and benchmarks), with a real OpenAI-backed implementation available behind the same interface for interactive use.

## Stack

Python, FastAPI, SQLAlchemy (async), PostgreSQL + pgvector, Alembic, OpenAI SDK (optional), Prometheus, Grafana, pytest, Docker Compose.

## Architecture at a glance

```
alert --> Investigation(pending) --> agent loop (<=5 tool calls + finalize)
                                         |
                     +-------------------+-------------------+
                     |    search_telemetry, search_deployments,   |
                     |    retrieve_runbook, search_prior_incidents,|
                     |    inspect_change  (read-only, bounded,     |
                     |    schema-validated, logged)                |
                     +-------------------+-------------------+
                                         v
                        Diagnosis (validated: every evidence_ref
                        must have actually been returned by a tool
                        call this investigation made)
                                         v
                              approve / reject (idempotent)
                                         v
                              postmortem draft (on approval)
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full design, [DESIGN_DECISIONS.md](DESIGN_DECISIONS.md) for the reasoning behind the non-obvious choices, and [BENCHMARKS.md](BENCHMARKS.md) for measured performance.

## Setup

```bash
cp .env.example .env
docker compose up -d db
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
alembic upgrade head
python scripts/seed.py
```

The seed script deterministically generates 1,200+ records (deployments, source changes, telemetry, runbooks, prior incidents) across 8 services.

Run the API:

```bash
uvicorn app.main:app --reload
```

Or run the full stack (API + Postgres/pgvector + Prometheus + Grafana):

```bash
docker compose up -d --build
```

- API: http://localhost:8000 (docs at `/docs`)
- Prometheus: http://localhost:9090
- Grafana: http://localhost:3000 (anonymous viewer access enabled; admin/admin)

## Try it

```bash
curl -X POST localhost:8000/alerts -H 'Content-Type: application/json' -d '{
  "service": "checkout", "severity": "high",
  "message": "connection pool exhausted", "fingerprint": "demo-1"
}'
# -> {"id":1,...,"investigation_id":1}

curl -X POST localhost:8000/investigations/1/run     # runs the agent loop
curl localhost:8000/investigations/1                 # inspect the diagnosis
curl -X POST localhost:8000/investigations/1/approve  # approve -> postmortem
```

## Testing

```bash
pytest -q                                   # 49 functional/integration tests
pytest tests/test_fault_injection.py -q     # 250 parametrized fault-injection cases
pytest -q --cov=app --cov-report=term-missing
```

Both suites run entirely on the deterministic provider -- no network access or API key required. See [BENCHMARKS.md](BENCHMARKS.md) for `scripts/benchmark.py`.

## Repository layout

```
app/
  main.py, config.py, db.py, models.py, schemas.py, errors.py, metrics.py
  embeddings/    EmbeddingProvider: deterministic (default) + OpenAI
  reasoning/      ReasoningProvider (the agent): deterministic (default) + OpenAI
  tools/           the 5 diagnostic tools + registry + executor
  pipeline/         ingestion, investigation orchestration, diagnosis validation, postmortem
  routers/           ingestion, alerts, investigations, health
alembic/             one initial migration: schema + pgvector extension + HNSW indexes
scripts/             seed.py, benchmark.py
tests/               49 functional/integration tests, plus tests/faults/ (250-case fault-injection matrix)
monitoring/          prometheus.yml, Grafana provisioning + the 8-panel dashboard
.github/workflows/    CI: lint, migrate, test with coverage, seed
```

## Diagnostic tools

Five read-only, schema-validated, bounded, logged tools, callable only by the reasoning agent:

| tool | purpose |
|---|---|
| `search_telemetry` | recent telemetry events for a service |
| `search_deployments` | recent deployments for a service |
| `retrieve_runbook` | pgvector similarity search over runbooks, flags staleness |
| `search_prior_incidents` | pgvector similarity search over prior incidents |
| `inspect_change` | a specific source change and any deployment that shipped it |

## Monitoring

8 Prometheus metrics (request latency, tool execution duration/failures, retrieval duration, ingestion count, investigation count, LLM call duration/count) feed an 8-panel Grafana dashboard provisioned automatically on `docker compose up`.
