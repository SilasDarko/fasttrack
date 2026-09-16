import re

METRIC_NAMES = [
    "http_request_duration_seconds",
    "tool_execution_duration_seconds",
    "tool_execution_failures_total",
    "retrieval_duration_seconds",
    "ingestion_records_total",
    "investigations_total",
    "llm_call_duration_seconds",
    "llm_calls_total",
]


def _counter_value(text: str, metric: str, **labels) -> float:
    label_str = ",".join(f'{k}="{v}"' for k, v in labels.items())
    pattern = re.compile(rf'^{re.escape(metric)}\{{[^}}]*{re.escape(label_str)}[^}}]*\}} ([0-9.e+-]+)$', re.M)
    match = pattern.search(text)
    return float(match.group(1)) if match else 0.0


async def test_metrics_endpoint_exposes_all_eight_metrics(client):
    resp = await client.get("/metrics")
    assert resp.status_code == 200
    text = resp.text
    for name in METRIC_NAMES:
        assert name in text, f"missing metric {name}"


async def test_ingestion_increments_ingestion_records_counter(client):
    before = _counter_value((await client.get("/metrics")).text, "ingestion_records_total",
                             record_type="deployment")

    await client.post(
        "/deployments",
        json={
            "service": "checkout",
            "version": "v9.9.9",
            "commit_sha": "f" * 40,
            "environment": "production",
            "status": "success",
            "deployed_at": "2026-01-01T00:00:00Z",
        },
    )

    after = _counter_value((await client.get("/metrics")).text, "ingestion_records_total",
                            record_type="deployment")
    assert after == before + 1


async def test_alert_increments_investigations_pending_counter(client):
    before = _counter_value((await client.get("/metrics")).text, "investigations_total",
                             status="pending")

    await client.post(
        "/alerts",
        json={
            "service": "checkout",
            "severity": "high",
            "message": "pool exhausted",
            "fingerprint": "fp-metrics-1",
        },
    )

    after = _counter_value((await client.get("/metrics")).text, "investigations_total",
                            status="pending")
    assert after == before + 1
