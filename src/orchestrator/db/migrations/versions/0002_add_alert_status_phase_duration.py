"""Add alert status, phase duration, and missing performance indexes.

This migration fills schema gaps required by the new mobile API endpoints and
monitoring wiring introduced in the gap-fix sprint.

Changes
-------
Columns:
  - alerts.status          TEXT NOT NULL DEFAULT 'active'
      Required by GET /api/v1/alerts (REQ-005).  Allowed values:
      'active' | 'acknowledged' | 'resolved'.

  - run_phases.duration_seconds  FLOAT NULL
      Stores the wall-clock duration of each phase.  Populated by
      WorkflowEngine._emit_phase_complete() (TASK-004) so that
      GET /api/v1/metrics avg_phase_duration_seconds can be computed with
      a simple AVG() instead of a two-column subtraction.

Indexes (performance only — no data changes):
  - idx_alerts_severity     ON alerts(severity)
      Enables O(1) severity-filtered alert lists used by the mobile alerts
      endpoint and dashboard.

  - idx_runs_project        ON runs(project_name)
      Speeds up project-filtered run lists (RunRepository.list).

  - idx_artifacts_agent     ON artifacts(agent)
      Enables the agent= filter in the global artifact search endpoint.

  - idx_phases_name         ON run_phases(phase_name)
      Enables efficient GROUP BY phase_name for cost-by-agent aggregation.

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-02
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_postgresql() -> bool:
    bind = op.get_bind()
    return bind.dialect.name == "postgresql"


# ---------------------------------------------------------------------------
# Upgrade
# ---------------------------------------------------------------------------

def upgrade() -> None:
    # ── alerts.status ────────────────────────────────────────────────────
    # SQLite supports ADD COLUMN natively when a server_default is supplied.
    # PostgreSQL handles this identically.  We use batch_alter_table to keep
    # the migration SQLite-safe for environments that use batch mode.
    with op.batch_alter_table("alerts") as batch_op:
        batch_op.add_column(
            sa.Column(
                "status",
                sa.Text(),
                nullable=False,
                server_default="active",
            )
        )

    # ── run_phases.duration_seconds ───────────────────────────────────────
    # Nullable: existing rows have no known duration.  The WorkflowEngine will
    # populate this on every new phase completion event going forward.
    with op.batch_alter_table("run_phases") as batch_op:
        batch_op.add_column(
            sa.Column("duration_seconds", sa.Float(), nullable=True)
        )

    # ── Performance indexes ───────────────────────────────────────────────
    # Alerts filtered by severity (mobile API + dashboard alert list)
    op.create_index("idx_alerts_severity", "alerts", ["severity"])

    # Runs filtered by project_name (multi-project deployments)
    op.create_index("idx_runs_project", "runs", ["project_name"])

    # Artifact search filtered by agent
    op.create_index("idx_artifacts_agent", "artifacts", ["agent"])

    # Phase-level cost aggregation by phase name (cost-by-agent queries)
    op.create_index("idx_phases_name", "run_phases", ["phase_name"])


# ---------------------------------------------------------------------------
# Downgrade
# ---------------------------------------------------------------------------

def downgrade() -> None:
    # Drop indexes first (before touching the columns they reference)
    op.drop_index("idx_phases_name", table_name="run_phases")
    op.drop_index("idx_artifacts_agent", table_name="artifacts")
    op.drop_index("idx_runs_project", table_name="runs")
    op.drop_index("idx_alerts_severity", table_name="alerts")

    # DROP COLUMN requires batch mode on SQLite (< 3.35.0).
    with op.batch_alter_table("run_phases") as batch_op:
        batch_op.drop_column("duration_seconds")

    with op.batch_alter_table("alerts") as batch_op:
        batch_op.drop_column("status")
