"""SQLAlchemy 2.x ORM mapped classes for the centralized persistence layer.

Tables
------
- runs            — one row per orchestration run (replaces state.json + run_registry)
- run_phases      — per-phase status/cost records (denormalized for dashboard queries)
- artifacts       — versioned artifact store (replaces .index.json + .versions/)
- run_events      — JSONL event log (replaces run-{id}.jsonl)
- alerts          — alert history (replaces workspace/alerts.jsonl)
- timeline_entries — per-task timeline (replaces timeline.json)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# JSON type that falls back to TEXT on SQLite (where JSONB is not available)
try:
    from sqlalchemy.dialects.postgresql import JSONB as JSONType
except ImportError:  # pragma: no cover
    from sqlalchemy import JSON as JSONType  # type: ignore[assignment]

from sqlalchemy import JSON


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Run(Base):
    """Core run metadata and full serialized RunState for crash recovery/resume."""

    __tablename__ = "runs"

    run_id: Mapped[str] = mapped_column(Text, primary_key=True)
    feature_request: Mapped[str] = mapped_column(Text, nullable=False)
    workflow_type: Mapped[str] = mapped_column(Text, nullable=False, default="feature_development")
    status: Mapped[str] = mapped_column(Text, nullable=False, default="running")
    # Full serialized RunState as JSON — used for resume and crash recovery.
    state_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    workspace_dir: Mapped[str] = mapped_column(Text, nullable=False)
    project_name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source: Mapped[str] = mapped_column(Text, nullable=False, default="cli")
    total_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    total_input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    start_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    phases: Mapped[list[RunPhase]] = relationship(
        "RunPhase", back_populates="run", cascade="all, delete-orphan"
    )
    artifacts: Mapped[list[Artifact]] = relationship(
        "Artifact", back_populates="run", cascade="all, delete-orphan"
    )
    events: Mapped[list[RunEvent]] = relationship(
        "RunEvent", back_populates="run", cascade="all, delete-orphan"
    )
    timeline_entries: Mapped[list[TimelineEntry]] = relationship(
        "TimelineEntry", back_populates="run", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("idx_runs_status", "status"),
        Index("idx_runs_start", "start_time"),
        Index("idx_runs_updated", "updated_at"),
    )


class RunPhase(Base):
    """Per-phase execution record (denormalized from state_json for fast queries)."""

    __tablename__ = "run_phases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        Text, ForeignKey("runs.run_id", ondelete="CASCADE"), nullable=False
    )
    phase_name: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    model_tier: Mapped[str | None] = mapped_column(Text, nullable=True)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    run: Mapped[Run] = relationship("Run", back_populates="phases")

    __table_args__ = (
        UniqueConstraint("run_id", "phase_name", name="uq_run_phases_run_phase"),
        Index("idx_phases_run", "run_id"),
    )


class Artifact(Base):
    """Versioned artifact store (replaces artifacts/*.json + .index.json + .versions/)."""

    __tablename__ = "artifacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        Text, ForeignKey("runs.run_id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    schema_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    content: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    run_status: Mapped[str] = mapped_column(Text, nullable=False, default="running")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    run: Mapped[Run] = relationship("Run", back_populates="artifacts")

    __table_args__ = (
        UniqueConstraint("run_id", "name", "version", name="uq_artifacts_run_name_ver"),
        Index("idx_artifacts_run", "run_id"),
        Index("idx_artifacts_name", "name"),
        Index("idx_artifacts_run_name", "run_id", "name"),
        Index("idx_artifacts_run_name_ver", "run_id", "name", "version"),
    )


class RunEvent(Base):
    """JSONL event log (replaces run-{id}.jsonl files)."""

    __tablename__ = "run_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        Text, ForeignKey("runs.run_id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    level: Mapped[str] = mapped_column(Text, nullable=False, default="INFO")
    data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    run: Mapped[Run] = relationship("Run", back_populates="events")

    __table_args__ = (
        UniqueConstraint("run_id", "seq", name="uq_run_events_run_seq"),
        Index("idx_events_run", "run_id", "seq"),
        Index("idx_events_type", "run_id", "event_type"),
    )


class Alert(Base):
    """Alert history (replaces workspace/alerts.jsonl)."""

    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("runs.run_id", ondelete="SET NULL"), nullable=True
    )
    alert_type: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(Text, nullable=False, default="info")
    message: Mapped[str] = mapped_column(Text, nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    __table_args__ = (
        Index("idx_alerts_created", "created_at"),
        Index("idx_alerts_run", "run_id"),
    )


class TimelineEntry(Base):
    """Per-task timeline entry (replaces timeline.json)."""

    __tablename__ = "timeline_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        Text, ForeignKey("runs.run_id", ondelete="CASCADE"), nullable=False
    )
    task_id: Mapped[str] = mapped_column(Text, nullable=False)
    agent: Mapped[str] = mapped_column(Text, nullable=False)
    step: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="running")
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    model_tier: Mapped[str | None] = mapped_column(Text, nullable=True)
    dependencies: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)

    run: Mapped[Run] = relationship("Run", back_populates="timeline_entries")

    __table_args__ = (
        UniqueConstraint("run_id", "task_id", name="uq_timeline_run_task"),
        Index("idx_timeline_run", "run_id"),
    )
