"""Database engine, session factory, and FastAPI dependency.

Async end-to-end per the coding conventions: ``asyncpg`` under SQLAlchemy 2.x
async, no blocking calls. The ``get_session`` dependency yields an
``AsyncSession`` and rolls back on exception so a failed handler can never leave
an uncommitted transaction dangling.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings


engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_pre_ping=True,  # cheap "SELECT 1" to recycle dead connections
)

# expire_on_commit=False: ORM objects returned from a handler are typically
# accessed after the session closes (e.g. when serializing to Pydantic). With
# the default True that would trigger a lazy reload on a closed session.
SessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: provide a session, rollback on error, always close.

    Reads ``SessionLocal`` at call time (not at import) so tests can swap in a
    loop-local factory by patching this module's ``SessionLocal`` — important
    because asyncpg connections are bound to the event loop that made them,
    and pytest-asyncio gives each test a fresh loop.
    """
    async with SessionLocal() as session:  # noqa: UP046 — read latest binding
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
