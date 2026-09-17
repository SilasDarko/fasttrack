# FastTrack

FastTrack is a FastAPI incident-analysis service that correlates telemetry, deployments, source changes, runbooks, and prior incidents to produce evidence-backed diagnoses.

Investigations run through a bounded reasoning loop with five read-only diagnostic tools. Every evidence reference in a diagnosis is validated against records actually returned during that investigation before the result can be accepted.

A human approval gate separates diagnosis from any suggested action. Approved investigations produce a deterministic postmortem draft derived from the validated diagnosis.

FastTrack supports both deterministic local providers and optional OpenAI-backed providers. The deterministic providers are used by default for local development, testing, CI, seeding, and benchmarks so the system remains reproducible and does not require network access or an API key.

## Stack

- Python
- FastAPI
- SQLAlchemy Async
- PostgreSQL
- pgvector
- Alembic
- OpenAI SDK
- Prometheus
- Grafana
- pytest
- Docker Compose

## Architecture

```text
Alert
  |
  v
Investigation (pending)
  |
  v
Bounded reasoning loop
  |
  +--> search_telemetry
  +--> search_deployments
  +--> retrieve_runbook
  +--> search_prior_incidents
  +--> inspect_change
  |
  v
Diagnosis
  |
  +--> evidence references validated against
       records actually retrieved during
       this investigation
  |
  v
Approve / Reject
  |
  v
Postmortem draft on approval
```

The five diagnostic tools are read-only, schema-validated, result-bounded, and executed through a shared tool executor that records latency, structured errors, and Prometheus metrics.

See:

- [ARCHITECTURE.md](ARCHITECTURE.md) for system design
- [DESIGN_DECISIONS.md](DESIGN_DECISIONS.md) for implementation tradeoffs
- [BENCHMARKS.md](BENCHMARKS.md) for measured performance and methodology

## Setup

Create the environment:

```bash
cp .env.example .env
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

Start PostgreSQL with pgvector:

```bash
docker compose up -d db
```

Apply the schema and seed the database:

```bash
alembic upgrade head
python scripts/seed.py
```

The deterministic seed currently produces 1,200+ operational records across eight services, including telemetry, deployments, source changes, runbooks, and prior incidents.

Run the API locally:

```bash
uvicorn app.main:app --reload
```

Or start the complete stack:

```bash
docker compose up -d --build
```

Services:

```text
API         http://localhost:8000
API docs    http://localhost:8000/docs
Prometheus  http://localhost:9090
Grafana     http://localhost:3000
```

Grafana is configured for local anonymous viewing. The default local administrator credentials are defined by the Docker Compose configuration.

## Example workflow

Create an alert:

```bash
curl -X POST http://localhost:8000/alerts \
  -H 'Content-Type: application/json' \
  -d '{
    "service": "checkout",
    "severity": "high",
    "message": "connection pool exhausted",
    "fingerprint": "demo-1"
  }'
```

Example response:

```json
{
  "id": 1,
  "investigation_id": 1
}
```

Run the investigation:

```bash
curl -X POST http://localhost:8000/investigations/1/run
```

Inspect the result:

```bash
curl http://localhost:8000/investigations/1
```

Approve the investigation:

```bash
curl -X POST http://localhost:8000/investigations/1/approve
```

Approval is idempotent and generates a postmortem draft from the validated diagnosis.

## Diagnostic tools

FastTrack exposes exactly five diagnostic tools to the reasoning layer.

| Tool | Purpose |
|---|---|
| `search_telemetry` | Retrieve recent telemetry for a service |
| `search_deployments` | Find recent deployments for a service |
| `retrieve_runbook` | Search runbooks using pgvector similarity and report staleness |
| `search_prior_incidents` | Search historical incidents using pgvector similarity |
| `inspect_change` | Inspect a source change and any deployment associated with it |

Each tool has:

- a Pydantic input schema
- a Pydantic output schema
- bounded query results
- deterministic ordering
- read-only behavior
- execution logging
- structured failure handling
- Prometheus instrumentation

The same tool schemas can also be exposed to the OpenAI reasoning provider through function calling.

## Providers

### Deterministic providers

The default providers require no external service.

`DeterministicEmbeddingProvider` generates stable 1536-dimensional embeddings locally and stores them in pgvector.

`DeterministicReasoningProvider` chooses its next diagnostic action based on evidence accumulated during the investigation rather than returning a fixed response.

These providers are used for:

- automated tests
- CI
- seed generation
- benchmarks
- reproducible local development

### OpenAI providers

FastTrack also includes:

```text
OpenAIEmbeddingProvider
OpenAIProvider
```

They can be selected through environment configuration:

```text
EMBEDDING_PROVIDER=openai
LLM_PROVIDER=openai
OPENAI_API_KEY=...
```

Selecting an OpenAI-backed provider without a configured key fails explicitly rather than silently falling back to the deterministic implementation.

## Evidence validation

A diagnosis cannot cite arbitrary database records.

Every `evidence_ref` is checked against the records actually returned by tool calls during the current investigation.

This means a reference must satisfy more than:

```text
Does this row exist?
```

It must satisfy:

```text
Was this row actually retrieved during this investigation?
```

This prevents diagnoses from citing valid-looking records that were never observed by the reasoning layer.

## Testing

Run the functional and integration suite:

```bash
pytest -q
```

Current suite:

```text
49 functional/integration tests
```

Run the fault-injection matrix separately:

```bash
pytest tests/test_fault_injection.py -q
```

Current matrix:

```text
250 parametrized fault-injection cases
```

Run coverage:

```bash
pytest -q --cov=app --cov-report=term-missing
```

Current measured line coverage is approximately 90%.

The automated test suite uses the deterministic providers and requires no OpenAI API key or external model calls.

## Benchmarks

Run:

```bash
python scripts/benchmark.py --n 200 --warmup 20
```

The benchmark measures FastTrack's local backend path with the deterministic providers.

Across three recorded 200-request runs, full incident-analysis p95 ranged from:

```text
13.0 ms to 17.3 ms
```

A conservative single summary value is:

```text
17.3 ms p95
```

This measurement excludes live OpenAI network latency.

See [BENCHMARKS.md](BENCHMARKS.md) for the full methodology, raw ranges, benchmark paths, and limitations.

## Monitoring

FastTrack exposes eight Prometheus metrics covering:

- HTTP latency
- tool execution duration
- tool execution failures
- retrieval latency
- ingestion volume
- investigation counts
- reasoning-provider latency
- reasoning-provider calls

Grafana is provisioned automatically with eight panels for request, retrieval, tool, ingestion, investigation, and provider behavior.

Configuration lives under:

```text
monitoring/
```

## Repository layout

```text
app/
  main.py
  config.py
  db.py
  models.py
  schemas.py
  errors.py
  metrics.py

  embeddings/
    base.py
    deterministic.py
    openai_provider.py
    factory.py

  reasoning/
    base.py
    deterministic.py
    openai_provider.py
    factory.py

  tools/
    base.py
    executor.py
    search_telemetry.py
    search_deployments.py
    retrieve_runbook.py
    search_prior_incidents.py
    inspect_change.py

  pipeline/
    ingestion.py
    investigation.py
    diagnosis_validation.py
    postmortem.py

  routers/
    ingestion.py
    alerts.py
    investigations.py
    health.py

alembic/
  versions/
    0001_initial.py

scripts/
  seed.py
  benchmark.py

tests/
  faults/
  test_*.py

monitoring/
  prometheus.yml
  grafana/

.github/
  workflows/
    ci.yml
```

## CI

The GitHub Actions workflow runs with deterministic providers and no model-provider credentials.

CI performs:

```text
lint
→ database migration
→ automated tests
→ coverage
→ deterministic seed verification
```

PostgreSQL with pgvector is used during CI so database and vector-retrieval behavior are exercised against the same database technology used by the application.
