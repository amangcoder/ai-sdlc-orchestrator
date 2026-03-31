"""AlertRepository — alert history stored in the ``alerts`` table."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.db.models import Alert


class AlertRepository:
    """Manages the ``alerts`` table (replaces workspace/alerts.jsonl)."""

    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory

    async def append(
        self,
        alert_type: str,
        message: str,
        severity: str = "info",
        run_id: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        """Append a new alert row."""
        async with self._session_factory() as session:
            session.add(
                Alert(
                    run_id=run_id,
                    alert_type=alert_type,
                    severity=severity,
                    message=message,
                    data=data or {},
                    created_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()

    async def list(
        self,
        limit: int = 100,
        run_id: str | None = None,
        severity: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return alerts sorted newest-first."""
        async with self._session_factory() as session:
            stmt = select(Alert).order_by(desc(Alert.created_at)).limit(limit)
            if run_id:
                stmt = stmt.where(Alert.run_id == run_id)
            if severity:
                stmt = stmt.where(Alert.severity == severity)
            result = await session.execute(stmt)
            return [self._to_dict(r) for r in result.scalars().all()]

    @staticmethod
    def _to_dict(row: Alert) -> dict[str, Any]:
        return {
            "id": row.id,
            "run_id": row.run_id,
            "alert_type": row.alert_type,
            "severity": row.severity,
            "message": row.message,
            "data": row.data,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
