from datetime import datetime, timedelta, timezone

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.tools  # noqa: F401  (registers the 5 diagnostic tools)
from app.config import get_settings
from app.db import Base, get_session
from app.embeddings.deterministic import DeterministicEmbeddingProvider
from app.main import app as fastapi_app
from app.models import Alert, Deployment, Investigation, PriorIncident, Runbook, SourceChange, TelemetryEvent

EMBEDDING_PROVIDER = DeterministicEmbeddingProvider(dimensions=1536)

# A dedicated engine with NullPool: every checkout opens a brand-new asyncpg
# connection and closes it right after, so a connection object never survives
# from one test's event loop into another's (pytest-asyncio gives each async
# test its own loop by default).
_test_engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
_test_session_factory = async_sessionmaker(_test_engine, expire_on_commit=False)


@pytest_asyncio.fixture(scope="session", loop_scope="session", autouse=True)
async def _prepare_schema():
    async with _test_engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
    yield


async def _truncate_all():
    async with _test_engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(table.delete())


@pytest_asyncio.fixture
async def session():
    async with _test_session_factory() as s:
        yield s
    await _truncate_all()


@pytest_asyncio.fixture
async def client():
    async def _override_get_session():
        async with _test_session_factory() as s:
            yield s

    fastapi_app.dependency_overrides[get_session] = _override_get_session
    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    fastapi_app.dependency_overrides.clear()
    await _truncate_all()


def now() -> datetime:
    return datetime.now(timezone.utc)


async def make_telemetry(session, service="checkout", level="error", message="pool exhausted",
                          minutes_ago=10, **kwargs) -> TelemetryEvent:
    embedding = await EMBEDDING_PROVIDER.embed(f"{level} {service} {message}")
    row = TelemetryEvent(
        service=service,
        level=level,
        message=message,
        host=kwargs.get("host", "host-1"),
        tags=kwargs.get("tags", {}),
        timestamp=now() - timedelta(minutes=minutes_ago),
        embedding=embedding,
    )
    session.add(row)
    await session.flush()
    return row


async def make_deployment(session, service="checkout", commit_sha="a" * 40,
                           minutes_ago=30, status="success", version="v1.0.0") -> Deployment:
    embedding = await EMBEDDING_PROVIDER.embed(f"{service} {version} {status}")
    row = Deployment(
        service=service,
        version=version,
        commit_sha=commit_sha,
        environment="production",
        status=status,
        deployed_at=now() - timedelta(minutes=minutes_ago),
        embedding=embedding,
    )
    session.add(row)
    await session.flush()
    return row


async def make_source_change(session, commit_sha="a" * 40, service="checkout") -> SourceChange:
    embedding = await EMBEDDING_PROVIDER.embed(f"fix {service} pool")
    row = SourceChange(
        repo=f"org/{service}",
        commit_sha=commit_sha,
        author="alice",
        message=f"fix {service} pool",
        files_changed=[f"{service}/pool.py"],
        diff_stat=12,
        timestamp=now() - timedelta(minutes=45),
        embedding=embedding,
    )
    session.add(row)
    await session.flush()
    return row


async def make_runbook(session, service="checkout", title="Pool exhaustion", stale=False,
                        content="connection pool exhausted mitigation steps") -> Runbook:
    embedding = await EMBEDDING_PROVIDER.embed(f"{title} {content}")
    row = Runbook(
        title=title,
        service=service,
        content=content,
        tags=["pool"],
        updated_at=now() - timedelta(days=400 if stale else 5),
        embedding=embedding,
    )
    session.add(row)
    await session.flush()
    return row


async def make_prior_incident(session, service="checkout", root_cause="resource_exhaustion",
                               summary="pool exhausted before") -> PriorIncident:
    embedding = await EMBEDDING_PROVIDER.embed(f"{summary} {root_cause}")
    row = PriorIncident(
        title=f"{service} incident",
        service=service,
        summary=summary,
        root_cause=root_cause,
        resolution="restarted service",
        occurred_at=now() - timedelta(days=30),
        embedding=embedding,
    )
    session.add(row)
    await session.flush()
    return row


async def make_alert(session, service="checkout", severity="high", message="pool exhausted",
                      fingerprint="fp-1") -> Alert:
    row = Alert(service=service, severity=severity, message=message, fingerprint=fingerprint)
    session.add(row)
    await session.flush()
    return row


async def make_investigation(session, alert: Alert, status="pending") -> Investigation:
    row = Investigation(alert_id=alert.id, status=status)
    session.add(row)
    await session.flush()
    return row
