"""One scenario function per fault type. Each takes (session, monkeypatch,
target_idx, variation_idx) -- both in range(5) -- and returns nothing on
success; it raises an AssertionError if the system did not degrade
gracefully. See tests/faults/matrix.py for how the 10 x 5 x 5 = 250 cases are
built, and ARCHITECTURE.md for what "target" means for each fault type.
"""

import asyncio

from app.errors import DependencyUnavailableError
from app.pipeline.ingestion import ingest_alert, ingest_telemetry
from app.pipeline.investigation import run_investigation
from app.schemas import AlertIn, TelemetryIn
from app.tools.executor import execute_tool
from tests.conftest import (
    EMBEDDING_PROVIDER,
    make_alert,
    make_investigation,
    make_prior_incident,
    make_runbook,
    make_source_change,
    make_telemetry,
)
from tests.faults.harness import (
    TimesOutReasoningProvider,
    UnavailableDependencyReasoningProvider,
    corrupt_query_embedding_dimension,
    patch_session_execute,
    patch_session_flush,
    patch_tool_output,
    vector_operator_unavailable_error,
)

SERVICES_5 = ["checkout", "payments", "search", "auth", "inventory"]
MESSAGES_5 = [
    "connection pool exhausted",
    "latency spike detected",
    "memory usage critical",
    "downstream dependency timeout",
    "cache invalidation failure",
]
TOOLS_5 = [
    "search_telemetry",
    "search_deployments",
    "retrieve_runbook",
    "search_prior_incidents",
    "inspect_change",
]


def _args_for_tool(tool_name: str, service: str) -> dict:
    if tool_name == "inspect_change":
        return {"commit_sha": "a" * 40}
    if tool_name in ("retrieve_runbook", "search_prior_incidents"):
        return {"service": service, "query": "pool exhausted"}
    return {"service": service}


async def _fresh_investigation(session, fingerprint: str, service: str):
    alert = await make_alert(session, service=service, message="pool exhausted",
                              fingerprint=fingerprint)
    investigation = await make_investigation(session, alert)
    await session.commit()
    return alert, investigation


# ---------------------------------------------------------------------------
# 1. db_timeout -- every tool's DB query can time out; the executor must
#    catch it uniformly and log a structured "database_unavailable" failure.
# ---------------------------------------------------------------------------
async def db_timeout(session, monkeypatch, target_idx: int, variation_idx: int) -> None:
    tool_name = TOOLS_5[target_idx]
    service = SERVICES_5[variation_idx]
    _, investigation = await _fresh_investigation(session, f"db-timeout-{target_idx}-{variation_idx}", service)

    patch_session_execute(monkeypatch, session, TimeoutError("simulated statement timeout"))

    record = await execute_tool(session, investigation.id, tool_name, _args_for_tool(tool_name, service))
    assert record.status == "error"
    assert record.error_code == "database_unavailable"


# ---------------------------------------------------------------------------
# 2. malformed_telemetry -- 5 distinct malformed payload shapes, posted to the
#    real ingestion pipeline (no mocking: Pydantic validation is the real
#    boundary being tested).
# ---------------------------------------------------------------------------
_MALFORMED_TELEMETRY_PAYLOADS = [
    {"service": "checkout", "level": "not-a-level", "message": "x", "host": "h",
     "timestamp": "2026-01-01T00:00:00Z"},
    {"service": "checkout", "level": "error", "message": "x", "host": "h"},  # missing timestamp
    {"service": "checkout", "level": "error", "message": 12345, "host": "h",
     "timestamp": "2026-01-01T00:00:00Z"},  # message wrong type
    {"service": "", "level": "error", "message": "x", "host": "h",
     "timestamp": "2026-01-01T00:00:00Z"},  # empty service is still schema-valid but exercises edge input
    {"level": "error", "message": "x", "host": "h", "timestamp": "2026-01-01T00:00:00Z"},  # missing service
]


async def malformed_telemetry(session, monkeypatch, target_idx: int, variation_idx: int) -> None:
    payload = dict(_MALFORMED_TELEMETRY_PAYLOADS[target_idx])
    payload["service"] = payload.get("service", "checkout") + f"-{SERVICES_5[variation_idx]}"

    try:
        data = TelemetryIn.model_validate(payload)
    except Exception:
        return  # rejected before reaching the pipeline -- graceful by construction
    # The one payload shape that IS schema-valid (empty/suffixed service) should
    # ingest cleanly rather than raise.
    row = await ingest_telemetry(session, EMBEDDING_PROVIDER, data)
    await session.commit()
    assert row.id is not None


# ---------------------------------------------------------------------------
# 3. missing_deployment -- no deployment exists to correlate with; every tool
#    and the full pipeline must handle "not found" as data, not an error.
# ---------------------------------------------------------------------------
async def missing_deployment(session, monkeypatch, target_idx: int, variation_idx: int) -> None:
    service = SERVICES_5[variation_idx]
    _, investigation = await _fresh_investigation(session, f"missing-dep-{target_idx}-{variation_idx}", service)

    if target_idx == 0:
        record = await execute_tool(session, investigation.id, "search_deployments", {"service": service})
        assert record.status == "success"
        assert record.output["results"] == []
    elif target_idx == 1:
        record = await execute_tool(session, investigation.id, "inspect_change", {"commit_sha": "b" * 40})
        assert record.status == "success"
        assert record.output["change"] is None
        assert record.output["correlated_deployment"] is None
    elif target_idx == 2:
        await make_telemetry(session, service=service, level="error")
        await session.commit()
        alert, investigation = await _fresh_investigation(
            session, f"missing-dep-full-{target_idx}-{variation_idx}", service
        )
        result = await run_investigation(session, investigation, alert)
        assert result.status == "completed"
        assert not any(r["type"] == "deployment" for r in result.diagnosis["evidence_refs"])
    elif target_idx == 3:
        record = await execute_tool(
            session, investigation.id, "search_deployments", {"service": service, "within_minutes": 5}
        )
        assert record.status == "success" and record.output["results"] == []
    else:
        sha = "c" * 40
        await make_source_change(session, commit_sha=sha, service=service)
        await session.commit()
        record = await execute_tool(session, investigation.id, "inspect_change", {"commit_sha": sha})
        assert record.status == "success"
        assert record.output["change"] is not None
        assert record.output["correlated_deployment"] is None


# ---------------------------------------------------------------------------
# 4. stale_runbook -- runbooks past the staleness threshold must be flagged,
#    never dropped or mishandled, across ages/services.
# ---------------------------------------------------------------------------
async def stale_runbook(session, monkeypatch, target_idx: int, variation_idx: int) -> None:
    service = SERVICES_5[variation_idx]
    _, investigation = await _fresh_investigation(session, f"stale-{target_idx}-{variation_idx}", service)

    if target_idx == 0:
        await make_runbook(session, service=service, title="Old", stale=True)
    elif target_idx == 1:
        await make_runbook(session, service=service, title="Old", stale=True)
        await make_runbook(session, service=service, title="Fresh", stale=False)
    elif target_idx == 2:
        from datetime import timedelta

        from tests.conftest import now
        r = await make_runbook(session, service=service, title="Ancient")
        r.updated_at = now() - timedelta(days=400)
    elif target_idx == 3:
        from datetime import timedelta

        from tests.conftest import now
        r = await make_runbook(session, service=service, title="JustOverThreshold")
        r.updated_at = now() - timedelta(days=181)
    else:
        for i in range(3):
            await make_runbook(session, service=service, title=f"AllStale{i}", stale=True)
    await session.commit()

    record = await execute_tool(
        session, investigation.id, "retrieve_runbook",
        {"service": service, "query": "pool exhausted", "limit": 5},
    )
    assert record.status == "success"
    assert len(record.output["results"]) >= 1
    if target_idx in (0, 2, 3, 4):
        assert all(r["stale"] for r in record.output["results"])


# ---------------------------------------------------------------------------
# 5. conflicting_evidence -- prior incidents disagree on root cause; the
#    diagnosis must lower confidence and say so, never crash or hide it.
# ---------------------------------------------------------------------------
_CONFLICT_PAIRS = [
    ("resource_exhaustion", "configuration_error"),
    ("deployment_regression", "cache_invalidation_bug"),
    ("downstream_dependency_failure", "database_contention"),
    ("configuration_error", "cache_invalidation_bug"),
    ("resource_exhaustion", "downstream_dependency_failure"),
]


async def conflicting_evidence(session, monkeypatch, target_idx: int, variation_idx: int) -> None:
    service = SERVICES_5[variation_idx]
    cause_a, cause_b = _CONFLICT_PAIRS[target_idx]
    alert, investigation = await _fresh_investigation(session, f"conflict-{target_idx}-{variation_idx}", service)
    await make_prior_incident(session, service=service, root_cause=cause_a)
    await make_prior_incident(session, service=service, root_cause=cause_b)
    await session.commit()

    result = await run_investigation(session, investigation, alert)
    assert result.status == "completed"
    assert "conflicting" in result.diagnosis["reasoning"].lower()
    assert result.diagnosis["confidence"] <= 0.4


# ---------------------------------------------------------------------------
# 6. openai_timeout -- the reasoning provider becomes unavailable at
#    different points in the agent loop.
# ---------------------------------------------------------------------------
async def openai_timeout(session, monkeypatch, target_idx: int, variation_idx: int) -> None:
    service = SERVICES_5[variation_idx]
    alert, investigation = await _fresh_investigation(session, f"openai-timeout-{target_idx}-{variation_idx}", service)
    # Elevated telemetry with no matching deployment forces the deterministic
    # policy's longest path (5 decide_next_action calls: telemetry,
    # deployments, runbook, prior incidents, finalize), so cutting it off
    # after `target_idx` (0-4) successful calls always fails the investigation
    # before it would have naturally completed.
    await make_telemetry(session, service=service, level="error")
    await session.commit()

    provider = TimesOutReasoningProvider(fail_after_calls=target_idx)
    result = await run_investigation(session, investigation, alert, reasoning=provider)

    assert result.status == "failed"
    assert result.diagnosis["error"] == "llm_unavailable"


# ---------------------------------------------------------------------------
# 7. invalid_tool_output -- each tool can return schema-violating output; the
#    executor must catch it via output-model validation, never crash.
# ---------------------------------------------------------------------------
_BAD_OUTPUTS = [
    {},  # missing "results"
    {"results": "not-a-list"},
    {"results": [{"unexpected": "shape"}]},
    {"results": None},
    {"totally": "wrong"},
]


async def invalid_tool_output(session, monkeypatch, target_idx: int, variation_idx: int) -> None:
    tool_name = TOOLS_5[target_idx]
    service = SERVICES_5[variation_idx]
    _, investigation = await _fresh_investigation(session, f"bad-output-{target_idx}-{variation_idx}", service)

    bad_output = _BAD_OUTPUTS[variation_idx]
    if tool_name == "inspect_change":
        bad_output = {"nonsense": True} if variation_idx % 2 == 0 else {}
    patch_tool_output(monkeypatch, tool_name, bad_output)

    record = await execute_tool(session, investigation.id, tool_name, _args_for_tool(tool_name, service))
    assert record.status == "error"
    assert record.error_code == "tool_output_invalid"


# ---------------------------------------------------------------------------
# 8. duplicate_alerts -- 5 distinct duplication patterns, all through the
#    real ingestion pipeline.
# ---------------------------------------------------------------------------
async def duplicate_alerts(session, monkeypatch, target_idx: int, variation_idx: int) -> None:
    service = SERVICES_5[variation_idx]
    fingerprint = f"dup-{target_idx}-{variation_idx}"
    payload = AlertIn(service=service, severity="high", message="pool exhausted", fingerprint=fingerprint)

    if target_idx == 0:
        _, created1, inv1 = await ingest_alert(session, payload)
        await session.commit()
        _, created2, inv2 = await ingest_alert(session, payload)
        await session.commit()
        assert created1 is True and created2 is False
        assert inv1 == inv2
    elif target_idx == 1:
        # A single AsyncSession cannot run concurrent operations; give each
        # concurrent ingestion its own session, as separate real requests would.
        from tests.conftest import _test_session_factory

        async def _ingest_in_own_session():
            async with _test_session_factory() as s:
                result = await ingest_alert(s, payload)
                await s.commit()
                return result

        results = await asyncio.gather(*(_ingest_in_own_session() for _ in range(3)))
        created_flags = [r[1] for r in results]
        assert created_flags.count(True) == 1
    elif target_idx == 2:
        from app.models import Investigation

        alert, _, inv1 = await ingest_alert(session, payload)
        await session.commit()
        investigation = await session.get(Investigation, inv1)
        await run_investigation(session, investigation, alert)
        await session.commit()
        _, created2, inv2 = await ingest_alert(session, payload)
        await session.commit()
        assert created2 is False and inv2 == inv1
    elif target_idx == 3:
        _, created1, inv1 = await ingest_alert(session, payload)
        await session.commit()
        _, created2, inv2 = await ingest_alert(session, payload)
        await session.commit()
        _, created3, inv3 = await ingest_alert(session, payload)
        await session.commit()
        assert (created1, created2, created3) == (True, False, False)
        assert inv1 == inv2 == inv3
    else:
        other_payload = AlertIn(service=service, severity="critical", message="different message",
                                 fingerprint=fingerprint)
        _, created1, inv1 = await ingest_alert(session, payload)
        await session.commit()
        _, created2, inv2 = await ingest_alert(session, other_payload)
        await session.commit()
        assert created2 is False
        assert inv1 == inv2


# ---------------------------------------------------------------------------
# 9. vector_query_failure -- realistic pgvector-layer failures: genuine
#    embedding-dimension mismatch (Postgres raises this for real) and a
#    simulated extension/operator-unavailable error using the exact message
#    Postgres emits when the pgvector operator cannot be resolved.
# ---------------------------------------------------------------------------
async def vector_query_failure(session, monkeypatch, target_idx: int, variation_idx: int) -> None:
    service = SERVICES_5[variation_idx]
    _, investigation = await _fresh_investigation(session, f"vecfail-{target_idx}-{variation_idx}", service)
    tool_name = "retrieve_runbook" if target_idx % 2 == 0 else "search_prior_incidents"

    if target_idx in (0, 2):
        corrupt_query_embedding_dimension(monkeypatch, wrong_dimensions=64)
    elif target_idx in (1, 3):
        patch_session_execute(monkeypatch, session, vector_operator_unavailable_error())
    else:
        patch_session_execute(monkeypatch, session, TimeoutError("simulated vector query timeout"))
        tool_name = "retrieve_runbook"

    record = await execute_tool(session, investigation.id, tool_name, _args_for_tool(tool_name, service))
    assert record.status == "error"
    assert record.error_code in ("vector_search_failed", "database_unavailable")


# ---------------------------------------------------------------------------
# 10. unavailable_dependency -- generic downstream dependencies (DB,
#     embedding provider, agent-level dependency) fail at different points.
# ---------------------------------------------------------------------------
async def unavailable_dependency(session, monkeypatch, target_idx: int, variation_idx: int) -> None:
    service = SERVICES_5[variation_idx]

    if target_idx == 0:
        patch_session_flush(monkeypatch, session, TimeoutError("db pool exhausted"))
        data = TelemetryIn(service=service, level="error", message="x", host="h",
                            timestamp="2026-01-01T00:00:00Z")
        try:
            await ingest_telemetry(session, EMBEDDING_PROVIDER, data)
            raised = False
        except Exception:
            raised = True
        assert raised
    elif target_idx == 1:
        alert, investigation = await _fresh_investigation(
            session, f"dep-unavail-{target_idx}-{variation_idx}", service
        )
        patch_session_execute(monkeypatch, session, TimeoutError("db unavailable mid-investigation"))
        result = await run_investigation(session, investigation, alert)
        assert result.status in ("completed", "failed")
    elif target_idx == 2:
        alert, investigation = await _fresh_investigation(
            session, f"dep-unavail-{target_idx}-{variation_idx}", service
        )
        provider = UnavailableDependencyReasoningProvider()
        try:
            await run_investigation(session, investigation, alert, reasoning=provider)
            raised = False
        except DependencyUnavailableError:
            raised = True
        assert raised
    elif target_idx == 3:
        from app.embeddings.factory import get_embedding_provider

        _, investigation = await _fresh_investigation(
            session, f"dep-unavail-{target_idx}-{variation_idx}", service
        )

        async def _broken_embed(text: str):
            raise DependencyUnavailableError("embedding backend unavailable")

        monkeypatch.setattr(get_embedding_provider(), "embed", _broken_embed)
        try:
            await execute_tool(
                session, investigation.id, "retrieve_runbook",
                {"service": service, "query": "pool exhausted"},
            )
            raised = False
        except DependencyUnavailableError:
            raised = True
        assert raised
    else:
        alert, investigation = await _fresh_investigation(
            session, f"dep-unavail-{target_idx}-{variation_idx}", service
        )
        await make_telemetry(session, service=service)
        await session.commit()
        patch_session_execute(monkeypatch, session, TimeoutError("db unavailable during retrieval"))
        result = await run_investigation(session, investigation, alert)
        assert result.status in ("completed", "failed")
