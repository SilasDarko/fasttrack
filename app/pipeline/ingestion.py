from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.embeddings.base import EmbeddingProvider
from app.metrics import INGESTION_RECORDS, INVESTIGATIONS
from app.models import (
    Alert,
    Deployment,
    Investigation,
    PriorIncident,
    Runbook,
    SourceChange,
    TelemetryEvent,
)
from app.schemas import (
    AlertIn,
    DeploymentIn,
    PriorIncidentIn,
    RunbookIn,
    SourceChangeIn,
    TelemetryIn,
)


async def ingest_telemetry(
    session: AsyncSession, provider: EmbeddingProvider, data: TelemetryIn
) -> TelemetryEvent:
    embedding = await provider.embed(f"{data.level} {data.service} {data.message}")
    row = TelemetryEvent(
        service=data.service,
        level=data.level,
        message=data.message,
        host=data.host,
        tags=data.tags,
        timestamp=data.timestamp,
        embedding=embedding,
    )
    session.add(row)
    await session.flush()
    INGESTION_RECORDS.labels(record_type="telemetry").inc()
    return row


async def ingest_deployment(
    session: AsyncSession, provider: EmbeddingProvider, data: DeploymentIn
) -> Deployment:
    embedding = await provider.embed(f"{data.service} {data.version} {data.status}")
    row = Deployment(
        service=data.service,
        version=data.version,
        commit_sha=data.commit_sha,
        environment=data.environment,
        status=data.status,
        deployed_at=data.deployed_at,
        embedding=embedding,
    )
    session.add(row)
    await session.flush()
    INGESTION_RECORDS.labels(record_type="deployment").inc()
    return row


async def ingest_source_change(
    session: AsyncSession, provider: EmbeddingProvider, data: SourceChangeIn
) -> SourceChange:
    embedding = await provider.embed(f"{data.message} {' '.join(data.files_changed)}")
    row = SourceChange(
        repo=data.repo,
        commit_sha=data.commit_sha,
        author=data.author,
        message=data.message,
        files_changed=data.files_changed,
        diff_stat=data.diff_stat,
        timestamp=data.timestamp,
        embedding=embedding,
    )
    session.add(row)
    await session.flush()
    INGESTION_RECORDS.labels(record_type="source_change").inc()
    return row


async def ingest_runbook(
    session: AsyncSession, provider: EmbeddingProvider, data: RunbookIn
) -> Runbook:
    embedding = await provider.embed(f"{data.title} {data.content}")
    row = Runbook(
        title=data.title,
        service=data.service,
        content=data.content,
        tags=data.tags,
        updated_at=data.updated_at,
        embedding=embedding,
    )
    session.add(row)
    await session.flush()
    INGESTION_RECORDS.labels(record_type="runbook").inc()
    return row


async def ingest_prior_incident(
    session: AsyncSession, provider: EmbeddingProvider, data: PriorIncidentIn
) -> PriorIncident:
    embedding = await provider.embed(f"{data.summary} {data.root_cause}")
    row = PriorIncident(
        title=data.title,
        service=data.service,
        summary=data.summary,
        root_cause=data.root_cause,
        resolution=data.resolution,
        occurred_at=data.occurred_at,
        embedding=embedding,
    )
    session.add(row)
    await session.flush()
    INGESTION_RECORDS.labels(record_type="prior_incident").inc()
    return row


async def ingest_alert(session: AsyncSession, data: AlertIn) -> tuple[Alert, bool, int]:
    """Idempotent on fingerprint. Returns (alert, created, investigation_id)."""
    existing = (
        await session.execute(select(Alert).where(Alert.fingerprint == data.fingerprint))
    ).scalar_one_or_none()
    if existing is not None:
        investigation = (
            await session.execute(
                select(Investigation)
                .where(Investigation.alert_id == existing.id)
                .order_by(Investigation.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        return existing, False, (investigation.id if investigation else None)

    alert = Alert(
        service=data.service,
        severity=data.severity,
        message=data.message,
        fingerprint=data.fingerprint,
    )
    session.add(alert)
    try:
        await session.flush()
    except IntegrityError:
        # Lost a concurrent race on the fingerprint's unique constraint: another
        # request inserted the same alert first. Treat it the same as the
        # "already exists" path above instead of surfacing a 500.
        await session.rollback()
        existing = (
            await session.execute(select(Alert).where(Alert.fingerprint == data.fingerprint))
        ).scalar_one()
        investigation = (
            await session.execute(
                select(Investigation)
                .where(Investigation.alert_id == existing.id)
                .order_by(Investigation.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        return existing, False, (investigation.id if investigation else None)

    investigation = Investigation(alert_id=alert.id, status="pending")
    session.add(investigation)
    await session.flush()

    INGESTION_RECORDS.labels(record_type="alert").inc()
    INVESTIGATIONS.labels(status="pending").inc()
    return alert, True, investigation.id
