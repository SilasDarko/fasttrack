from app.tools.executor import execute_tool
from tests.conftest import (
    make_alert,
    make_deployment,
    make_investigation,
    make_runbook,
    make_telemetry,
)


async def test_pgvector_ranks_closer_vector_first(session):
    alert = await make_alert(session, fingerprint="fp-ret-1")
    investigation = await make_investigation(session, alert)
    await make_runbook(session, title="Connection pool exhaustion runbook",
                        content="connection pool exhausted increase pool size")
    await make_runbook(session, title="Unrelated deploy rollback runbook",
                        content="rollback a bad deployment revert version")
    await session.commit()

    record = await execute_tool(
        session, investigation.id, "retrieve_runbook",
        {"service": "checkout", "query": "connection pool exhausted", "limit": 2},
    )

    assert record.status == "success"
    titles = [r["title"] for r in record.output["results"]]
    assert titles[0] == "Connection pool exhaustion runbook"


async def test_service_filter_excludes_other_services(session):
    alert = await make_alert(session, fingerprint="fp-ret-2")
    investigation = await make_investigation(session, alert)
    await make_telemetry(session, service="checkout", minutes_ago=5)
    await make_telemetry(session, service="payments", minutes_ago=5)
    await session.commit()

    record = await execute_tool(
        session, investigation.id, "search_telemetry", {"service": "checkout"}
    )

    assert record.status == "success"
    assert all(r["service"] == "checkout" for r in record.output["results"])
    assert len(record.output["results"]) == 1


async def test_stale_runbook_is_flagged(session):
    alert = await make_alert(session, fingerprint="fp-ret-3")
    investigation = await make_investigation(session, alert)
    await make_runbook(session, title="Old runbook", stale=True)
    await make_runbook(session, title="Fresh runbook", stale=False)
    await session.commit()

    record = await execute_tool(
        session, investigation.id, "retrieve_runbook",
        {"service": "checkout", "query": "pool exhausted", "limit": 5},
    )

    by_title = {r["title"]: r["stale"] for r in record.output["results"]}
    assert by_title["Old runbook"] is True
    assert by_title["Fresh runbook"] is False


async def test_limit_is_clamped_for_retrieval_tools(session):
    alert = await make_alert(session, fingerprint="fp-ret-4")
    investigation = await make_investigation(session, alert)
    for i in range(8):
        await make_runbook(session, title=f"Runbook {i}")
    await session.commit()

    record = await execute_tool(
        session, investigation.id, "retrieve_runbook",
        {"service": "checkout", "query": "pool", "limit": 100},
    )

    assert len(record.output["results"]) <= 5


async def test_search_deployments_within_minutes_window(session):
    alert = await make_alert(session, fingerprint="fp-ret-5")
    investigation = await make_investigation(session, alert)
    await make_deployment(session, commit_sha="1" * 40, minutes_ago=10)
    await make_deployment(session, commit_sha="2" * 40, minutes_ago=1000)
    await session.commit()

    record = await execute_tool(
        session, investigation.id, "search_deployments",
        {"service": "checkout", "within_minutes": 60},
    )

    shas = [r["commit_sha"] for r in record.output["results"]]
    assert "1" * 40 in shas
    assert "2" * 40 not in shas
