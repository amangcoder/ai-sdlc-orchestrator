"""Alembic environment configuration for the orchestrator DB layer.

Supports both offline (generate SQL script) and online (apply to DB) modes.
Uses async engine via ``run_async_migrations()``.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

# Import ORM metadata so Alembic can detect schema changes.
from orchestrator.db.models import Base

# Alembic Config object provides access to the .ini file values.
config = context.config

# Interpret the config file for Python logging if present.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (generate SQL without a live connection)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # Render JSONB for PostgreSQL; JSON for SQLite (handled per-dialect)
        render_as_batch=url.startswith("sqlite"),
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    url = str(connection.engine.url)
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=url.startswith("sqlite"),
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations in 'online' mode (connect and apply)."""
    url = config.get_main_option("sqlalchemy.url")
    engine = create_async_engine(url, echo=False)
    async with engine.connect() as conn:
        await conn.run_sync(do_run_migrations)
    await engine.dispose()


def run_migrations() -> None:
    if context.is_offline_mode():
        run_migrations_offline()
    else:
        asyncio.run(run_migrations_online())


run_migrations()
