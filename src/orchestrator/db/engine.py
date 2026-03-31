"""Async SQLAlchemy engine factory.

Creates either a PostgreSQL (asyncpg) or SQLite (aiosqlite) engine based on
the URL scheme in ``DatabaseConfig.url``.  Raises a clear ``ImportError`` with
install hints when the required driver package is not installed.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

log = logging.getLogger(__name__)


def create_async_engine_from_config(config: "DatabaseConfig") -> "AsyncEngine":  # noqa: F821
    """Return a configured ``AsyncEngine`` for the given ``DatabaseConfig``.

    Args:
        config: ``DatabaseConfig`` instance (``url`` must be non-empty).

    Raises:
        ValueError: If ``config.url`` is empty.
        ImportError: If the required async driver is not installed.
        RuntimeError: For unsupported URL schemes.
    """
    from orchestrator.models import DatabaseConfig  # local import avoids circular

    if not isinstance(config, DatabaseConfig):
        raise TypeError(f"Expected DatabaseConfig, got {type(config)}")
    if not config.url:
        raise ValueError("DatabaseConfig.url is empty — DB mode is not enabled")

    try:
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy.pool import NullPool, AsyncAdaptedQueuePool
    except ImportError as exc:
        raise ImportError(
            "SQLAlchemy async support is required for DB mode. "
            "Install it with: pip install 'ai-sdlc-orchestrator[database]'"
        ) from exc

    url = config.url

    if url.startswith("postgresql"):
        try:
            import asyncpg  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "asyncpg is required for PostgreSQL support. "
                "Install it with: pip install asyncpg"
            ) from exc
        engine = create_async_engine(
            url,
            echo=config.echo,
            pool_size=config.pool_size,
            max_overflow=config.max_overflow,
            poolclass=AsyncAdaptedQueuePool,
        )
        log.info("DB engine: PostgreSQL pool_size=%d max_overflow=%d", config.pool_size, config.max_overflow)

    elif url.startswith("sqlite"):
        try:
            import aiosqlite  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "aiosqlite is required for SQLite async support. "
                "Install it with: pip install aiosqlite"
            ) from exc
        # SQLite doesn't support connection pools — use NullPool.
        engine = create_async_engine(
            url,
            echo=config.echo,
            poolclass=NullPool,
            connect_args={"check_same_thread": False},
        )
        log.info("DB engine: SQLite (NullPool) url=%s", url)

    else:
        raise RuntimeError(
            f"Unsupported database URL scheme in {url!r}. "
            "Supported schemes: postgresql+asyncpg://, sqlite+aiosqlite://"
        )

    return engine
