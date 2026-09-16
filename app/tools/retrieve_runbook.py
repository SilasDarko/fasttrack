from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, StatementError
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import VectorSearchError
from app.models import Runbook
from app.schemas import RetrieveRunbookInput, RetrieveRunbookOutput, RunbookOut
from app.tools.base import Tool, ToolDeps, register_tool

_SNIPPET_LEN = 240


async def handle(session: AsyncSession, params: RetrieveRunbookInput, deps: ToolDeps) -> dict:
    limit = min(params.limit, deps.settings.retrieval_max_limit)
    query_vector = await deps.embedding_provider.embed(params.query)

    stmt = (
        select(Runbook)
        .where(Runbook.service == params.service)
        .order_by(Runbook.embedding.cosine_distance(query_vector), Runbook.id)
        .limit(limit)
    )
    try:
        rows = (await session.execute(stmt)).scalars().all()
    except (DBAPIError, StatementError) as exc:
        # StatementError covers pgvector's own client-side dimension check
        # (raised before the query even reaches Postgres); DBAPIError covers
        # failures the database itself reports (e.g. the extension/operator
        # being unavailable).
        raise VectorSearchError(f"pgvector similarity search failed: {exc}") from exc

    stale_cutoff = datetime.now(timezone.utc) - timedelta(days=deps.settings.runbook_stale_days)
    output = RetrieveRunbookOutput(
        results=[
            RunbookOut(
                id=row.id,
                title=row.title,
                service=row.service,
                updated_at=row.updated_at,
                stale=row.updated_at < stale_cutoff,
                snippet=row.content[:_SNIPPET_LEN],
            )
            for row in rows
        ]
    )
    return output.model_dump(mode="json")


register_tool(
    Tool(
        name="retrieve_runbook",
        description=(
            "Semantic search over runbooks for a service using pgvector cosine similarity. "
            "Flags results whose last update is older than the staleness threshold."
        ),
        input_model=RetrieveRunbookInput,
        output_model=RetrieveRunbookOutput,
        handler=handle,
    )
)
