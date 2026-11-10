"""Async engine and session factory (one engine per process, created lazily)."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from nursing_bot.config import get_settings

_engine: AsyncEngine | None = None
_session_maker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.DATABASE_URL,
            pool_size=settings.DATABASE_POOL_SIZE,
            max_overflow=settings.DATABASE_MAX_OVERFLOW,
            pool_pre_ping=True,
            pool_recycle=1800,
        )
    return _engine


def get_session_maker() -> async_sessionmaker[AsyncSession]:
    global _session_maker
    if _session_maker is None:
        _session_maker = async_sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _session_maker


@asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield a session; roll back if the caller raises. Callers commit explicitly."""
    async with get_session_maker()() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def dispose_engine() -> None:
    global _engine, _session_maker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_maker = None


async def enable_hnsw_iterative_scan(session: AsyncSession) -> None:
    """Let pgvector keep scanning the HNSW graph when filters remove candidates.

    Without this, a filtered vector query can return fewer than LIMIT rows (or
    none) even though matching chunks exist. SET LOCAL lasts until the current
    transaction ends. Requires pgvector >= 0.8.
    """
    await session.execute(text("SET LOCAL hnsw.iterative_scan = 'relaxed_order'"))
