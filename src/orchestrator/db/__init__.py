"""Centralized database persistence layer for the AI SDLC Orchestrator.

Exports
-------
- ``init_db(config)``   — initialize engine + session factory, run migrations
- ``get_session()``     — async context manager yielding ``AsyncSession``
- ``get_db()``          — FastAPI dependency
- ``is_enabled()``      — True when DB mode is active (config.url is non-empty)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orchestrator.models import DatabaseConfig

log = logging.getLogger(__name__)

# Module-level engine singleton — set by init_db()
_engine = None


def is_enabled() -> bool:
    """Return True if the DB layer has been initialized."""
    return _engine is not None


async def init_db(config: "DatabaseConfig") -> None:  # noqa: F821
    """Initialize the async engine, session factory, and run Alembic migrations.

    This is idempotent — calling it multiple times with the same config is safe.

    Args:
        config: ``DatabaseConfig`` with a non-empty ``url``.
    """
    global _engine

    if not config.enabled:
        log.debug("DB mode disabled (database.url is empty)")
        return

    from orchestrator.db.engine import create_async_engine_from_config
    from orchestrator.db.session import configure_session_factory
    from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

    if _engine is not None:
        log.debug("DB already initialized, skipping init_db()")
        return

    _engine = create_async_engine_from_config(config)
    factory = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    configure_session_factory(factory)

    if config.migrate_on_start:
        await _run_migrations(config)

    log.info("DB initialized: url=%s", _mask_url(config.url))


async def _run_migrations(config: "DatabaseConfig") -> None:
    """Run ``alembic upgrade head`` programmatically."""
    import asyncio
    from pathlib import Path

    try:
        from alembic.config import Config as AlembicConfig
        from alembic import command as alembic_command
    except ImportError:
        log.warning(
            "alembic is not installed — skipping automatic migrations. "
            "Install with: pip install alembic"
        )
        return

    migrations_dir = Path(__file__).parent / "migrations"
    if not migrations_dir.exists():
        log.warning("No migrations/ directory found at %s — skipping", migrations_dir)
        return

    alembic_cfg = AlembicConfig()
    alembic_cfg.set_main_option("script_location", str(migrations_dir))
    alembic_cfg.set_main_option("sqlalchemy.url", config.url)

    # Alembic is synchronous — run in a thread executor to avoid blocking the event loop.
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, alembic_command.upgrade, alembic_cfg, "head")
    log.info("Alembic migrations applied (upgrade head)")


def _mask_url(url: str) -> str:
    """Mask password in a DB URL for safe logging."""
    import re
    return re.sub(r"(://[^:]+:)[^@]+(@)", r"\1***\2", url)


def get_engine():
    """Return the initialized ``AsyncEngine`` or ``None`` if DB is not enabled."""
    return _engine


# Re-export session helpers
from orchestrator.db.session import get_session, get_db  # noqa: E402

__all__ = ["init_db", "is_enabled", "get_engine", "get_session", "get_db"]
