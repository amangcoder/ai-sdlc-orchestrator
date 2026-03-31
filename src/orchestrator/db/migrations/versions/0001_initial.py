"""Initial schema: runs, run_phases, artifacts, run_events, alerts, timeline_entries.

Revision ID: 0001
Revises: (none)
Create Date: 2026-03-27
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def _is_postgresql() -> bool:
    bind = op.get_bind()
    return bind.dialect.name == "postgresql"


def _json_type():
    """JSONB on PostgreSQL, JSON on SQLite."""
    if _is_postgresql():
        return postgresql.JSONB()
    return sa.JSON()


def _bigint_type():
    """BIGINT on PostgreSQL (maps to BIGSERIAL via autoincrement), INTEGER on SQLite.

    SQLite only auto-increments INTEGER PRIMARY KEY columns (not BIGINT).
    """
    if _is_postgresql():
        return sa.BigInteger()
    return sa.Integer()


def upgrade() -> None:
    # ── runs ──────────────────────────────────────────────────────────────
    op.create_table(
        "runs",
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("feature_request", sa.Text(), nullable=False),
        sa.Column("workflow_type", sa.Text(), nullable=False, server_default="feature_development"),
        sa.Column("status", sa.Text(), nullable=False, server_default="running"),
        sa.Column("state_json", _json_type(), nullable=False, server_default="{}"),
        sa.Column("workspace_dir", sa.Text(), nullable=False),
        sa.Column("project_name", sa.Text(), nullable=False, server_default=""),
        sa.Column("source", sa.Text(), nullable=False, server_default="cli"),
        sa.Column("total_cost_usd", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("total_input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("run_id"),
    )
    op.create_index("idx_runs_status", "runs", ["status"])
    op.create_index("idx_runs_start", "runs", ["start_time"])
    op.create_index("idx_runs_updated", "runs", ["updated_at"])

    # ── run_phases ────────────────────────────────────────────────────────
    op.create_table(
        "run_phases",
        sa.Column("id", _bigint_type(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("phase_name", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("model_tier", sa.Text(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "phase_name", name="uq_run_phases_run_phase"),
    )
    op.create_index("idx_phases_run", "run_phases", ["run_id"])

    # ── artifacts ─────────────────────────────────────────────────────────
    op.create_table(
        "artifacts",
        sa.Column("id", _bigint_type(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("agent", sa.Text(), nullable=True),
        sa.Column("schema_name", sa.Text(), nullable=True),
        sa.Column("content", _json_type(), nullable=False, server_default="{}"),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("run_status", sa.Text(), nullable=False, server_default="running"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "name", "version", name="uq_artifacts_run_name_ver"),
    )
    op.create_index("idx_artifacts_run", "artifacts", ["run_id"])
    op.create_index("idx_artifacts_name", "artifacts", ["name"])
    op.create_index("idx_artifacts_run_name", "artifacts", ["run_id", "name"])
    op.create_index("idx_artifacts_run_name_ver", "artifacts", ["run_id", "name", "version"])
    # GIN index for content search — PostgreSQL only
    if _is_postgresql():
        op.create_index(
            "idx_artifacts_content_gin", "artifacts", ["content"],
            postgresql_using="gin",
        )

    # ── run_events ────────────────────────────────────────────────────────
    op.create_table(
        "run_events",
        sa.Column("id", _bigint_type(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("level", sa.Text(), nullable=False, server_default="INFO"),
        sa.Column("data", _json_type(), nullable=False, server_default="{}"),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "seq", name="uq_run_events_run_seq"),
    )
    op.create_index("idx_events_run", "run_events", ["run_id", "seq"])
    op.create_index("idx_events_type", "run_events", ["run_id", "event_type"])

    # ── alerts ────────────────────────────────────────────────────────────
    op.create_table(
        "alerts",
        sa.Column("id", _bigint_type(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.Text(), nullable=True),
        sa.Column("alert_type", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False, server_default="info"),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("data", _json_type(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_alerts_created", "alerts", ["created_at"])
    op.create_index("idx_alerts_run", "alerts", ["run_id"])

    # ── timeline_entries ──────────────────────────────────────────────────
    op.create_table(
        "timeline_entries",
        sa.Column("id", _bigint_type(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("task_id", sa.Text(), nullable=False),
        sa.Column("agent", sa.Text(), nullable=False),
        sa.Column("step", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="running"),
        sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("model_tier", sa.Text(), nullable=True),
        sa.Column("dependencies", _json_type(), nullable=False, server_default="[]"),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "task_id", name="uq_timeline_run_task"),
    )
    op.create_index("idx_timeline_run", "timeline_entries", ["run_id"])


def downgrade() -> None:
    op.drop_table("timeline_entries")
    op.drop_table("alerts")
    op.drop_table("run_events")
    op.drop_table("artifacts")
    op.drop_table("run_phases")
    op.drop_table("runs")
