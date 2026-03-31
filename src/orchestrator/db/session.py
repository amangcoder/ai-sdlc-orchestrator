"""Async session lifecycle for the centralized DB layer.

Provides:
- ``get_session()`` — async context manager yielding ``AsyncSession``
- ``get_db()``      — FastAPI dependency (yields ``AsyncSession``)
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


# Module-level session factory — populated by init_db()
_session_factory: async_sessionmaker[AsyncSession] | None = None


def configure_session_factory(factory: async_sessionmaker[AsyncSession]) -> None:
    """Store the session factory created by ``init_db()``."""
    global _session_factory
    _session_factory = factory


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Async context manager that yields a new ``AsyncSession``.

    Usage::

        async with get_session() as session:
            result = await session.execute(...)
            await session.commit()
    """
    if _session_factory is None:
        raise RuntimeError(
            "Database session factory is not initialized. "
            "Call init_db() before using get_session()."
        )
    async with _session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields an ``AsyncSession``.

    Usage::

        @router.get("/runs")
        async def list_runs(db: AsyncSession = Depends(get_db)):
            ...
    """
    async with get_session() as session:
        yield session
