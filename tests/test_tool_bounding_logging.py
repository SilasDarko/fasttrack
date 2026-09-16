from sqlalchemy import select

from app.models import ToolExecutionLog
from app.schemas import SearchTelemetryOutput
from app.tools.base import TOOL_REGISTRY, Tool
from app.tools.executor import execute_tool
from tests.conftest import make_alert, make_investigation, make_telemetry


async def test_limit_is_clamped_to_configured_max(session):
    alert = await make_alert(session, fingerprint="fp-bound-1")
    investigation = await make_investigation(session, alert)
    for i in range(25):
        await make_telemetry(session, minutes_ago=i)
    await session.commit()

    record = await execute_tool(
        session, investigation.id, "search_telemetry", {"service": "checkout", "limit": 999}
    )

    assert record.status == "success"
    assert len(record.output["results"]) <= 20


async def test_successful_call_writes_tool_execution_log_with_latency(session):
    alert = await make_alert(session, fingerprint="fp-bound-2")
    investigation = await make_investigation(session, alert)
    await make_telemetry(session)
    await session.commit()

    await execute_tool(session, investigation.id, "search_telemetry", {"service": "checkout"})
    await session.commit()

    rows = (
        await session.execute(
            select(ToolExecutionLog).where(ToolExecutionLog.investigation_id == investigation.id)
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "success"
    assert rows[0].latency_ms >= 0


async def test_invalid_tool_output_is_caught_and_logged(session, monkeypatch):
    alert = await make_alert(session, fingerprint="fp-bound-3")
    investigation = await make_investigation(session, alert)
    await session.commit()

    real_tool = TOOL_REGISTRY["search_telemetry"]

    async def broken_handler(session, params, deps):
        return {"results": "not-a-list"}  # violates SearchTelemetryOutput schema

    monkeypatch.setitem(
        TOOL_REGISTRY,
        "search_telemetry",
        Tool(
            name="search_telemetry",
            description=real_tool.description,
            input_model=real_tool.input_model,
            output_model=SearchTelemetryOutput,
            handler=broken_handler,
        ),
    )

    record = await execute_tool(session, investigation.id, "search_telemetry", {"service": "checkout"})
    await session.commit()

    assert record.status == "error"
    assert record.error_code == "tool_output_invalid"

    rows = (
        await session.execute(
            select(ToolExecutionLog).where(ToolExecutionLog.investigation_id == investigation.id)
        )
    ).scalars().all()
    assert rows[0].error_code == "tool_output_invalid"
