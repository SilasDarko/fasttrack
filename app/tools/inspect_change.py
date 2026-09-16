from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Deployment, SourceChange
from app.schemas import DeploymentOut, InspectChangeInput, InspectChangeOutput, SourceChangeOut
from app.tools.base import Tool, ToolDeps, register_tool


async def handle(session: AsyncSession, params: InspectChangeInput, deps: ToolDeps) -> dict:
    change_row = (
        await session.execute(
            select(SourceChange).where(SourceChange.commit_sha == params.commit_sha)
        )
    ).scalar_one_or_none()

    deployment_row = (
        await session.execute(
            select(Deployment)
            .where(Deployment.commit_sha == params.commit_sha)
            .order_by(Deployment.deployed_at.desc(), Deployment.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    output = InspectChangeOutput(
        change=(
            SourceChangeOut(
                id=change_row.id,
                repo=change_row.repo,
                commit_sha=change_row.commit_sha,
                author=change_row.author,
                message=change_row.message,
                files_changed=change_row.files_changed,
                diff_stat=change_row.diff_stat,
                timestamp=change_row.timestamp,
            )
            if change_row
            else None
        ),
        correlated_deployment=(
            DeploymentOut(
                id=deployment_row.id,
                service=deployment_row.service,
                version=deployment_row.version,
                commit_sha=deployment_row.commit_sha,
                environment=deployment_row.environment,
                status=deployment_row.status,
                deployed_at=deployment_row.deployed_at,
            )
            if deployment_row
            else None
        ),
    )
    return output.model_dump(mode="json")


register_tool(
    Tool(
        name="inspect_change",
        description=(
            "Look up a specific source change by commit SHA and any deployment that shipped it."
        ),
        input_model=InspectChangeInput,
        output_model=InspectChangeOutput,
        handler=handle,
    )
)
