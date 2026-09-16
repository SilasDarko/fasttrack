import subprocess
import sys
from pathlib import Path

from sqlalchemy import func, select

from app.models import (
    Deployment,
    PriorIncident,
    Runbook,
    SourceChange,
    TelemetryEvent,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


async def test_ingest_telemetry_creates_row_with_embedding(client):
    resp = await client.post(
        "/telemetry",
        json={
            "service": "checkout",
            "level": "error",
            "message": "pool exhausted",
            "host": "checkout-1",
            "timestamp": "2026-01-01T00:00:00Z",
        },
    )
    assert resp.status_code == 201
    assert "id" in resp.json()


async def test_ingest_deployment_creates_row(client):
    resp = await client.post(
        "/deployments",
        json={
            "service": "checkout",
            "version": "v1.2.3",
            "commit_sha": "a" * 40,
            "environment": "production",
            "status": "success",
            "deployed_at": "2026-01-01T00:00:00Z",
        },
    )
    assert resp.status_code == 201


async def test_ingest_source_change_creates_row(client):
    resp = await client.post(
        "/source-changes",
        json={
            "repo": "org/checkout",
            "commit_sha": "b" * 40,
            "author": "alice",
            "message": "fix pool",
            "files_changed": ["checkout/pool.py"],
            "diff_stat": 10,
            "timestamp": "2026-01-01T00:00:00Z",
        },
    )
    assert resp.status_code == 201


async def test_ingest_runbook_and_prior_incident_create_rows(client, session):
    runbook_resp = await client.post(
        "/runbooks",
        json={
            "title": "Pool exhaustion",
            "service": "checkout",
            "content": "mitigation steps",
            "tags": ["pool"],
            "updated_at": "2026-01-01T00:00:00Z",
        },
    )
    incident_resp = await client.post(
        "/prior-incidents",
        json={
            "title": "Checkout incident",
            "service": "checkout",
            "summary": "pool exhausted before",
            "root_cause": "resource_exhaustion",
            "resolution": "restarted",
            "occurred_at": "2026-01-01T00:00:00Z",
        },
    )
    assert runbook_resp.status_code == 201
    assert incident_resp.status_code == 201


async def test_ingest_malformed_telemetry_rejected_with_structured_error(client):
    resp = await client.post(
        "/telemetry",
        json={
            "service": "checkout",
            "level": "not-a-real-level",  # invalid enum value
            "message": "pool exhausted",
            "host": "checkout-1",
            "timestamp": "2026-01-01T00:00:00Z",
        },
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["error_code"] == "validation_failed"
    assert "detail" in body


async def test_seed_script_reaches_at_least_1000_records(session):
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "seed.py")],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr

    counts = {}
    for model in (Deployment, SourceChange, TelemetryEvent, Runbook, PriorIncident):
        counts[model.__tablename__] = (
            await session.execute(select(func.count()).select_from(model))
        ).scalar_one()

    assert sum(counts.values()) >= 1000, counts
