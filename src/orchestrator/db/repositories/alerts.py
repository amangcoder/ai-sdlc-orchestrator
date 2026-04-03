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
        status: str = "active",
    ) -> None:
        """Append a new alert row.

        Args:
            alert_type: Categorisation key (e.g. ``'slo_breach'``).
            message:    Human-readable description of the alert condition.
            severity:   One of ``'critical'``, ``'warning'``, ``'info'``.
            run_id:     Optional run the alert is associated with.
            data:       Arbitrary structured payload for further diagnostics.
            status:     Lifecycle status — ``'active'`` (default),
                        ``'acknowledged'``, or ``'resolved'``.
        """
        async with self._session_factory() as session:
            session.add(
                Alert(
                    run_id=run_id,
                    alert_type=alert_type,
                    severity=severity,
                    message=message,
                    data=data or {},
                    status=status,
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
        triggered_at = row.created_at.isoformat() if row.created_at else None
        return {
            "id": str(row.id),
            "run_id": row.run_id,
            "alert_type": row.alert_type,
            "severity": row.severity,
            "message": row.message,
            "data": row.data,
            # ``triggered_at`` is the canonical name used by mobile/dashboard APIs.
            # ``created_at`` is preserved for backward compatibility.
            "triggered_at": triggered_at,
            "created_at": triggered_at,
            # Lifecycle status: 'active' | 'acknowledged' | 'resolved'.
            # Defaults to 'active' for rows that pre-date migration 0002.
            "status": getattr(row, "status", "active") or "active",
        }
