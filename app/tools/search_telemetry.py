from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import TelemetryEvent
from app.schemas import SearchTelemetryInput, SearchTelemetryOutput, TelemetryEventOut
from app.tools.base import Tool, ToolDeps, register_tool


async def handle(session: AsyncSession, params: SearchTelemetryInput, deps: ToolDeps) -> dict:
    limit = min(params.limit, deps.settings.tool_max_limit)
    since = datetime.now(timezone.utc) - timedelta(minutes=params.since_minutes)

    stmt = (
        select(TelemetryEvent)
        .where(TelemetryEvent.service == params.service, TelemetryEvent.timestamp >= since)
        .order_by(TelemetryEvent.timestamp.desc(), TelemetryEvent.id.desc())
        .limit(limit)
    )
    if params.keyword:
        stmt = stmt.where(TelemetryEvent.message.ilike(f"%{params.keyword}%"))

    rows = (await session.execute(stmt)).scalars().all()
    output = SearchTelemetryOutput(
        results=[
            TelemetryEventOut(
                id=row.id,
                service=row.service,
                level=row.level,
                message=row.message,
                host=row.host,
                timestamp=row.timestamp,
                tags=row.tags,
            )
            for row in rows
        ]
    )
    return output.model_dump(mode="json")


register_tool(
    Tool(
        name="search_telemetry",
        description=(
            "Search recent telemetry events for a service, optionally filtered by keyword. "
            "Read-only, bounded to the most recent matching events."
        ),
        input_model=SearchTelemetryInput,
        output_model=SearchTelemetryOutput,
        handler=handle,
    )
)
