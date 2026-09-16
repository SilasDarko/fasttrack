from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.embeddings.base import EmbeddingProvider
from app.embeddings.factory import get_embedding_provider
from app.pipeline import ingestion
from app.schemas import (
    DeploymentIn,
    PriorIncidentIn,
    RunbookIn,
    SourceChangeIn,
    TelemetryIn,
)

router = APIRouter(tags=["ingestion"])


@router.post("/telemetry", status_code=status.HTTP_201_CREATED)
async def post_telemetry(
    data: TelemetryIn,
    session: AsyncSession = Depends(get_session),
    provider: EmbeddingProvider = Depends(get_embedding_provider),
):
    row = await ingestion.ingest_telemetry(session, provider, data)
    await session.commit()
    return {"id": row.id}


@router.post("/deployments", status_code=status.HTTP_201_CREATED)
async def post_deployment(
    data: DeploymentIn,
    session: AsyncSession = Depends(get_session),
    provider: EmbeddingProvider = Depends(get_embedding_provider),
):
    row = await ingestion.ingest_deployment(session, provider, data)
    await session.commit()
    return {"id": row.id}


@router.post("/source-changes", status_code=status.HTTP_201_CREATED)
async def post_source_change(
    data: SourceChangeIn,
    session: AsyncSession = Depends(get_session),
    provider: EmbeddingProvider = Depends(get_embedding_provider),
):
    row = await ingestion.ingest_source_change(session, provider, data)
    await session.commit()
    return {"id": row.id}


@router.post("/runbooks", status_code=status.HTTP_201_CREATED)
async def post_runbook(
    data: RunbookIn,
    session: AsyncSession = Depends(get_session),
    provider: EmbeddingProvider = Depends(get_embedding_provider),
):
    row = await ingestion.ingest_runbook(session, provider, data)
    await session.commit()
    return {"id": row.id}


@router.post("/prior-incidents", status_code=status.HTTP_201_CREATED)
async def post_prior_incident(
    data: PriorIncidentIn,
    session: AsyncSession = Depends(get_session),
    provider: EmbeddingProvider = Depends(get_embedding_provider),
):
    row = await ingestion.ingest_prior_incident(session, provider, data)
    await session.commit()
    return {"id": row.id}
