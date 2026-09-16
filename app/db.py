from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def make_engine(database_url: str | None = None):
    settings = get_settings()
    return create_async_engine(database_url or settings.database_url, pool_pre_ping=True)


_engine = make_engine()
_session_factory = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)


def get_engine():
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    return _session_factory


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with _session_factory() as session:
        yield session
