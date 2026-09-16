from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Deployment
from app.schemas import DeploymentOut, SearchDeploymentsInput, SearchDeploymentsOutput
from app.tools.base import Tool, ToolDeps, register_tool


async def handle(session: AsyncSession, params: SearchDeploymentsInput, deps: ToolDeps) -> dict:
    limit = min(params.limit, deps.settings.tool_max_limit)
    since = datetime.now(timezone.utc) - timedelta(minutes=params.within_minutes)

    stmt = (
        select(Deployment)
        .where(Deployment.service == params.service, Deployment.deployed_at >= since)
        .order_by(Deployment.deployed_at.desc(), Deployment.id.desc())
        .limit(limit)
    )
    rows = (await session.execute(stmt)).scalars().all()
    output = SearchDeploymentsOutput(
        results=[
            DeploymentOut(
                id=row.id,
                service=row.service,
                version=row.version,
                commit_sha=row.commit_sha,
                environment=row.environment,
                status=row.status,
                deployed_at=row.deployed_at,
            )
            for row in rows
        ]
    )
    return output.model_dump(mode="json")


register_tool(
    Tool(
        name="search_deployments",
        description=(
            "Search deployments for a service within a recent time window, most recent first. "
            "Used to correlate an alert with a recent release."
        ),
        input_model=SearchDeploymentsInput,
        output_model=SearchDeploymentsOutput,
        handler=handle,
    )
)
