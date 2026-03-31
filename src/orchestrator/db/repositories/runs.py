"""RunRepository — CRUD operations for the ``runs`` and ``run_phases`` tables."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update, desc
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.db.models import Run, RunPhase

log = logging.getLogger(__name__)


def _upsert_stmt(dialect_name: str, table):
    """Return a dialect-appropriate INSERT ... ON CONFLICT DO UPDATE statement."""
    if dialect_name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
        return insert(table)
    # SQLite
    from sqlalchemy.dialects.sqlite import insert
    return insert(table)


class RunRepository:
    """Manages ``runs`` and ``run_phases`` table operations."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Run CRUD
    # ------------------------------------------------------------------

    async def upsert(self, state: Any, source: str = "cli", project_name: str = "") -> None:
        """Insert or update a run row from a ``RunState`` Pydantic model.

        Uses INSERT ... ON CONFLICT DO UPDATE so concurrent processes writing
        the same run_id don't race.  ``updated_at`` is only advanced when the
        incoming timestamp is newer (prevents stale overwrites).
        """
        from orchestrator.models import RunState
        if not isinstance(state, RunState):
            raise TypeError(f"Expected RunState, got {type(state)}")

        dialect = self._session.bind.dialect.name if self._session.bind else "sqlite"

        state_dict = state.model_dump(mode="json")
        now = datetime.now(timezone.utc)
        end_time = now if state.status in ("completed", "failed", "cancelled", "crashed") else None

        values = {
            "run_id": state.run_id,
            "feature_request": state.feature_request,
            "workflow_type": state.workflow_type.value if hasattr(state.workflow_type, "value") else state.workflow_type,
            "status": state.status.value if hasattr(state.status, "value") else state.status,
            "state_json": state_dict,
            "workspace_dir": state.workspace_dir,
            "project_name": project_name,
            "source": source,
            "total_cost_usd": state.total_cost_usd,
            "total_input_tokens": state.total_input_tokens,
            "total_output_tokens": state.total_output_tokens,
            "updated_at": now,
        }
        if end_time:
            values["end_time"] = end_time

        if dialect == "postgresql":
            from sqlalchemy.dialects.postgresql import insert as pg_insert
            stmt = pg_insert(Run).values(**values)
            stmt = stmt.on_conflict_do_update(
                index_elements=["run_id"],
                set_={
                    k: stmt.excluded[k]
                    for k in values
                    if k != "run_id"
                },
                where=Run.updated_at <= stmt.excluded.updated_at,
            )
        else:
            from sqlalchemy.dialects.sqlite import insert as sq_insert
            stmt = sq_insert(Run).values(**values)
            stmt = stmt.on_conflict_do_update(
                index_elements=["run_id"],
                set_={k: v for k, v in values.items() if k != "run_id"},
            )

        await self._session.execute(stmt)

        # Sync phase rows (upsert each phase)
        for phase_name, phase_state in state.phases.items():
            await self._upsert_phase(state.run_id, phase_name, phase_state, dialect)

    async def _upsert_phase(
        self, run_id: str, phase_name: str, phase_state: Any, dialect: str
    ) -> None:
        mt = phase_state.model_tier
        values = {
            "run_id": run_id,
            "phase_name": phase_name,
            "status": phase_state.status.value if hasattr(phase_state.status, "value") else phase_state.status,
            "model_tier": mt.value if hasattr(mt, "value") else mt,
            "cost_usd": phase_state.cost_usd,
            "retry_count": phase_state.retry_count,
            "error": phase_state.error,
            "error_code": phase_state.error_code,
        }
        if dialect == "postgresql":
            from sqlalchemy.dialects.postgresql import insert as pg_insert
            stmt = pg_insert(RunPhase).values(**values)
            stmt = stmt.on_conflict_do_update(
                constraint="uq_run_phases_run_phase",
                set_={k: v for k, v in values.items() if k not in ("run_id", "phase_name")},
            )
        else:
            from sqlalchemy.dialects.sqlite import insert as sq_insert
            stmt = sq_insert(RunPhase).values(**values)
            stmt = stmt.on_conflict_do_update(
                index_elements=["run_id", "phase_name"],
                set_={k: v for k, v in values.items() if k not in ("run_id", "phase_name")},
            )
        await self._session.execute(stmt)

    async def update_status(self, run_id: str, status: str, **extra: Any) -> None:
        """Update run status and optional extra fields."""
        now = datetime.now(timezone.utc)
        values: dict[str, Any] = {"status": status, "updated_at": now}
        if status in ("completed", "failed", "cancelled", "crashed"):
            values["end_time"] = now
        values.update(extra)
        await self._session.execute(
            update(Run).where(Run.run_id == run_id).values(**values)
        )

    async def get(self, run_id: str) -> dict[str, Any] | None:
        """Return the full state dict for *run_id*, or None."""
        result = await self._session.execute(
            select(Run).where(Run.run_id == run_id)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        return self._run_to_dict(row)

    async def get_state_json(self, run_id: str) -> dict[str, Any] | None:
        """Return the raw ``state_json`` JSONB blob for *run_id*."""
        result = await self._session.execute(
            select(Run.state_json).where(Run.run_id == run_id)
        )
        return result.scalar_one_or_none()

    async def list(
        self,
        limit: int = 200,
        status: str | None = None,
        project_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return runs sorted newest-first."""
        stmt = select(Run).order_by(desc(Run.start_time)).limit(limit)
        if status:
            stmt = stmt.where(Run.status == status)
        if project_name:
            stmt = stmt.where(Run.project_name == project_name)
        result = await self._session.execute(stmt)
        return [self._run_to_dict(r) for r in result.scalars().all()]

    async def exists(self, run_id: str) -> bool:
        result = await self._session.execute(
            select(Run.run_id).where(Run.run_id == run_id)
        )
        return result.scalar_one_or_none() is not None

    @staticmethod
    def _run_to_dict(row: Run) -> dict[str, Any]:
        return {
            "run_id": row.run_id,
            "feature_request": row.feature_request,
            "workflow_type": row.workflow_type,
            "status": row.status,
            "state_json": row.state_json,
            "workspace_dir": row.workspace_dir,
            "project_name": row.project_name,
            "source": row.source,
            "total_cost_usd": row.total_cost_usd,
            "total_input_tokens": row.total_input_tokens,
            "total_output_tokens": row.total_output_tokens,
            "start_time": row.start_time,
            "end_time": row.end_time,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
