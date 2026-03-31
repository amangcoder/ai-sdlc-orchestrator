"""TimelineRepository — per-task timeline stored in ``timeline_entries``."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.db.models import TimelineEntry


class TimelineRepository:
    """Manages ``timeline_entries`` (replaces timeline.json)."""

    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory

    async def upsert_entry(self, run_id: str, entry: dict[str, Any]) -> None:
        """Insert or update a timeline entry by run_id + task_id."""
        from datetime import datetime, timezone

        async with self._session_factory() as session:
            dialect = session.bind.dialect.name if session.bind else "sqlite"
            task_id = entry["task_id"]
            start_raw = entry.get("start_time")
            start_time = (
                datetime.fromisoformat(start_raw)
                if isinstance(start_raw, str)
                else (start_raw or datetime.now(timezone.utc))
            )
            end_raw = entry.get("end_time")
            end_time = (
                datetime.fromisoformat(end_raw)
                if isinstance(end_raw, str)
                else end_raw
            )

            values = {
                "run_id": run_id,
                "task_id": task_id,
                "agent": entry.get("agent", ""),
                "step": entry.get("step", ""),
                "status": entry.get("status", "running"),
                "cost_usd": entry.get("cost_usd", 0.0),
                "model_tier": entry.get("model_tier"),
                "dependencies": entry.get("dependencies", []),
                "error_code": entry.get("error_code"),
                "start_time": start_time,
                "end_time": end_time,
                "duration_seconds": entry.get("duration_seconds"),
            }

            if dialect == "postgresql":
                from sqlalchemy.dialects.postgresql import insert as pg_insert
                stmt = pg_insert(TimelineEntry).values(**values)
                stmt = stmt.on_conflict_do_update(
                    constraint="uq_timeline_run_task",
                    set_={k: v for k, v in values.items() if k not in ("run_id", "task_id")},
                )
            else:
                from sqlalchemy.dialects.sqlite import insert as sq_insert
                stmt = sq_insert(TimelineEntry).values(**values)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["run_id", "task_id"],
                    set_={k: v for k, v in values.items() if k not in ("run_id", "task_id")},
                )
            await session.execute(stmt)
            await session.commit()

    async def get_all(self, run_id: str) -> list[dict[str, Any]]:
        """Return all timeline entries for a run."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(TimelineEntry)
                .where(TimelineEntry.run_id == run_id)
                .order_by(TimelineEntry.start_time)
            )
            return [self._to_dict(r) for r in result.scalars().all()]

    @staticmethod
    def _to_dict(row: TimelineEntry) -> dict[str, Any]:
        return {
            "task_id": row.task_id,
            "agent": row.agent,
            "step": row.step,
            "status": row.status,
            "cost_usd": row.cost_usd,
            "model_tier": row.model_tier,
            "dependencies": row.dependencies,
            "error_code": row.error_code,
            "start_time": row.start_time.isoformat() if row.start_time else None,
            "end_time": row.end_time.isoformat() if row.end_time else None,
            "duration_seconds": row.duration_seconds,
        }
