from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, StatementError
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import VectorSearchError
from app.models import PriorIncident
from app.schemas import PriorIncidentOut, SearchPriorIncidentsInput, SearchPriorIncidentsOutput
from app.tools.base import Tool, ToolDeps, register_tool

_SNIPPET_LEN = 240


async def handle(
    session: AsyncSession, params: SearchPriorIncidentsInput, deps: ToolDeps
) -> dict:
    limit = min(params.limit, deps.settings.retrieval_max_limit)
    query_vector = await deps.embedding_provider.embed(params.query)

    stmt = (
        select(PriorIncident)
        .where(PriorIncident.service == params.service)
        .order_by(PriorIncident.embedding.cosine_distance(query_vector), PriorIncident.id)
        .limit(limit)
    )
    try:
        rows = (await session.execute(stmt)).scalars().all()
    except (DBAPIError, StatementError) as exc:
        raise VectorSearchError(f"pgvector similarity search failed: {exc}") from exc

    output = SearchPriorIncidentsOutput(
        results=[
            PriorIncidentOut(
                id=row.id,
                title=row.title,
                service=row.service,
                root_cause=row.root_cause,
                occurred_at=row.occurred_at,
                summary_snippet=row.summary[:_SNIPPET_LEN],
            )
            for row in rows
        ]
    )
    return output.model_dump(mode="json")


register_tool(
    Tool(
        name="search_prior_incidents",
        description=(
            "Semantic search over prior incidents for a service using pgvector cosine "
            "similarity, to find recurrences of past root causes."
        ),
        input_model=SearchPriorIncidentsInput,
        output_model=SearchPriorIncidentsOutput,
        handler=handle,
    )
)
