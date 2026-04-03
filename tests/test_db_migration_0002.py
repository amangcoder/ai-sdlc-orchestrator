"""Tests for DB migration 0002: alert status, phase duration, and new indexes.

Verifies:
  - Migration 0002 runs successfully on an empty database (upgrade path)
  - alerts.status column exists with correct default ('active')
  - run_phases.duration_seconds column exists as nullable Float
  - All four new indexes are present in the schema
  - AlertRepository._to_dict() includes 'triggered_at' and 'status'
  - ArtifactRepository._to_metadata() includes 'updated_at' and 'artifact_name'
  - Downgrade (rollback) removes added columns and indexes cleanly

Run with: pytest tests/test_db_migration_0002.py -v
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Skip entire module when SQLAlchemy/aiosqlite is not installed
# ---------------------------------------------------------------------------

sqlalchemy = pytest.importorskip("sqlalchemy")
pytest.importorskip("aiosqlite")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test_0002.db")


@pytest.fixture
def async_db_url(db_path):
    """Async SQLite URL for Alembic (env.py uses create_async_engine)."""
    return f"sqlite+aiosqlite:///{db_path}"


@pytest.fixture
def inspect_url(db_path):
    """Synchronous SQLite URL for schema inspection (same file, sync driver)."""
    return f"sqlite:///{db_path}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_alembic(async_url: str, direction: str, revision: str) -> None:
    """Apply or roll back Alembic migrations.

    Args:
        async_url:  async sqlite+aiosqlite URL (what env.py expects).
        direction:  ``'upgrade'`` or ``'downgrade'``.
        revision:   Alembic revision identifier or ``'head'``.
    """
    from alembic.config import Config
    from alembic import command
    from pathlib import Path

    project_root = Path(__file__).parent.parent
    cfg = Config()
    cfg.set_main_option(
        "script_location",
        str(project_root / "src" / "orchestrator" / "db" / "migrations"),
    )
    cfg.set_main_option("sqlalchemy.url", async_url)

    if direction == "upgrade":
        command.upgrade(cfg, revision)
    else:
        command.downgrade(cfg, revision)


def _get_table_columns(sync_url: str, table_name: str) -> set[str]:
    """Return the set of column names for *table_name* in the DB at *sync_url*."""
    from sqlalchemy import create_engine, inspect

    engine = create_engine(sync_url)
    try:
        insp = inspect(engine)
        return {col["name"] for col in insp.get_columns(table_name)}
    finally:
        engine.dispose()


def _get_index_names(sync_url: str, table_name: str) -> set[str]:
    """Return index names for *table_name*."""
    from sqlalchemy import create_engine, inspect

    engine = create_engine(sync_url)
    try:
        insp = inspect(engine)
        return {idx["name"] for idx in insp.get_indexes(table_name)}
    finally:
        engine.dispose()


def _run_sql(sync_url: str, sql: str, params: dict | None = None) -> Any:
    """Execute *sql* and return the fetchone result."""
    from sqlalchemy import create_engine, text

    engine = create_engine(sync_url)
    try:
        with engine.connect() as conn:
            result = conn.execute(text(sql), params or {})
            conn.commit()
            try:
                return result.fetchone()
            except Exception:
                return None
    finally:
        engine.dispose()


def _exec_sql(sync_url: str, sql: str, params: dict | None = None) -> None:
    """Execute a DML statement (no return value needed)."""
    from sqlalchemy import create_engine, text

    engine = create_engine(sync_url)
    try:
        with engine.connect() as conn:
            conn.execute(text(sql), params or {})
            conn.commit()
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# Migration upgrade tests
# ---------------------------------------------------------------------------

class TestMigration0002Upgrade:
    """Verify that migration 0002 adds all expected schema objects."""

    def test_alerts_status_column_exists_after_upgrade(self, async_db_url, inspect_url):
        _run_alembic(async_db_url, "upgrade", "head")
        cols = _get_table_columns(inspect_url, "alerts")
        assert "status" in cols, "alerts.status column missing after migration 0002"

    def test_run_phases_duration_seconds_column_exists(self, async_db_url, inspect_url):
        _run_alembic(async_db_url, "upgrade", "head")
        cols = _get_table_columns(inspect_url, "run_phases")
        assert "duration_seconds" in cols, (
            "run_phases.duration_seconds column missing after migration 0002"
        )

    def test_idx_alerts_severity_index_created(self, async_db_url, inspect_url):
        _run_alembic(async_db_url, "upgrade", "head")
        indexes = _get_index_names(inspect_url, "alerts")
        assert "idx_alerts_severity" in indexes, (
            "idx_alerts_severity missing after migration 0002"
        )

    def test_idx_runs_project_index_created(self, async_db_url, inspect_url):
        _run_alembic(async_db_url, "upgrade", "head")
        indexes = _get_index_names(inspect_url, "runs")
        assert "idx_runs_project" in indexes, (
            "idx_runs_project missing after migration 0002"
        )

    def test_idx_artifacts_agent_index_created(self, async_db_url, inspect_url):
        _run_alembic(async_db_url, "upgrade", "head")
        indexes = _get_index_names(inspect_url, "artifacts")
        assert "idx_artifacts_agent" in indexes, (
            "idx_artifacts_agent missing after migration 0002"
        )

    def test_idx_phases_name_index_created(self, async_db_url, inspect_url):
        _run_alembic(async_db_url, "upgrade", "head")
        indexes = _get_index_names(inspect_url, "run_phases")
        assert "idx_phases_name" in indexes, (
            "idx_phases_name missing after migration 0002"
        )

    def test_preexisting_0001_columns_intact(self, async_db_url, inspect_url):
        """Upgrade must not remove any columns that existed in migration 0001."""
        _run_alembic(async_db_url, "upgrade", "head")

        run_cols = _get_table_columns(inspect_url, "runs")
        expected = {"run_id", "feature_request", "status", "total_cost_usd",
                    "start_time", "created_at", "updated_at"}
        missing = expected - run_cols
        assert not missing, f"Migration 0002 removed run columns: {missing}"

        artifact_cols = _get_table_columns(inspect_url, "artifacts")
        expected_art = {"id", "run_id", "name", "version", "agent",
                        "schema_name", "content", "size_bytes", "created_at"}
        missing_art = expected_art - artifact_cols
        assert not missing_art, f"Migration 0002 removed artifact columns: {missing_art}"


# ---------------------------------------------------------------------------
# Constraint and default tests (DML via sync engine on the migrated DB)
# ---------------------------------------------------------------------------

class TestAlertStatusDefault:
    """Verify the alerts.status column default and constraints."""

    def test_alert_status_defaults_to_active(self, async_db_url, inspect_url):
        """An inserted Alert with no explicit status should get 'active'."""
        _run_alembic(async_db_url, "upgrade", "head")
        _exec_sql(
            inspect_url,
            "INSERT INTO alerts (alert_type, severity, message, data, created_at) "
            "VALUES ('test_default', 'info', 'test msg', '{}', datetime('now'))",
        )
        row = _run_sql(
            inspect_url,
            "SELECT status FROM alerts WHERE alert_type = 'test_default'",
        )
        assert row is not None
        assert row[0] == "active", f"Expected default status 'active', got {row[0]!r}"

    def test_alert_status_stores_resolved(self, async_db_url, inspect_url):
        """Explicit 'resolved' status should persist correctly."""
        _run_alembic(async_db_url, "upgrade", "head")
        _exec_sql(
            inspect_url,
            "INSERT INTO alerts (alert_type, severity, message, data, status, created_at) "
            "VALUES ('slo_breach', 'warning', 'SLO breach', '{}', 'resolved', datetime('now'))",
        )
        row = _run_sql(
            inspect_url,
            "SELECT status FROM alerts WHERE alert_type = 'slo_breach'",
        )
        assert row is not None
        assert row[0] == "resolved"


class TestRunPhaseDurationSeconds:
    """Verify the run_phases.duration_seconds column is nullable and stores floats."""

    def _insert_run(self, url: str, run_id: str) -> None:
        _exec_sql(
            url,
            "INSERT INTO runs (run_id, feature_request, workflow_type, status, "
            "state_json, workspace_dir, project_name, source, total_cost_usd, "
            "total_input_tokens, total_output_tokens, start_time, created_at, updated_at) "
            "VALUES (:run_id, 'test', 'feature_development', 'running', "
            "'{}', '/tmp', '', 'cli', 0.0, 0, 0, "
            "datetime('now'), datetime('now'), datetime('now'))",
            {"run_id": run_id},
        )

    def test_duration_seconds_nullable(self, async_db_url, inspect_url):
        """Existing-style rows (no duration) must be insertable without errors."""
        _run_alembic(async_db_url, "upgrade", "head")
        self._insert_run(inspect_url, "run-dur-null-test")
        _exec_sql(
            inspect_url,
            "INSERT INTO run_phases (run_id, phase_name, status, cost_usd, retry_count) "
            "VALUES ('run-dur-null-test', 'pm', 'completed', 0.5, 0)",
        )
        row = _run_sql(
            inspect_url,
            "SELECT duration_seconds FROM run_phases WHERE run_id = 'run-dur-null-test'",
        )
        assert row is not None
        assert row[0] is None, f"Expected NULL duration_seconds, got {row[0]!r}"

    def test_duration_seconds_stores_float(self, async_db_url, inspect_url):
        """Phase with a measured duration should persist the float value."""
        _run_alembic(async_db_url, "upgrade", "head")
        self._insert_run(inspect_url, "run-dur-val-test")
        _exec_sql(
            inspect_url,
            "INSERT INTO run_phases (run_id, phase_name, status, cost_usd, "
            "retry_count, duration_seconds) "
            "VALUES ('run-dur-val-test', 'architect', 'completed', 0.75, 0, 42.5)",
        )
        row = _run_sql(
            inspect_url,
            "SELECT duration_seconds FROM run_phases "
            "WHERE run_id = 'run-dur-val-test' AND phase_name = 'architect'",
        )
        assert row is not None
        assert abs(row[0] - 42.5) < 0.001, f"Expected 42.5, got {row[0]}"


# ---------------------------------------------------------------------------
# Downgrade (rollback) tests
# ---------------------------------------------------------------------------

class TestMigration0002Downgrade:
    """Verify that rolling back to 0001 removes all migration 0002 additions."""

    def test_downgrade_removes_alert_status_column(self, async_db_url, inspect_url):
        _run_alembic(async_db_url, "upgrade", "head")
        _run_alembic(async_db_url, "downgrade", "0001")
        cols = _get_table_columns(inspect_url, "alerts")
        assert "status" not in cols, (
            "alerts.status should be removed after downgrade to 0001"
        )

    def test_downgrade_removes_phase_duration_column(self, async_db_url, inspect_url):
        _run_alembic(async_db_url, "upgrade", "head")
        _run_alembic(async_db_url, "downgrade", "0001")
        cols = _get_table_columns(inspect_url, "run_phases")
        assert "duration_seconds" not in cols, (
            "run_phases.duration_seconds should be removed after downgrade to 0001"
        )

    def test_downgrade_removes_severity_index(self, async_db_url, inspect_url):
        _run_alembic(async_db_url, "upgrade", "head")
        _run_alembic(async_db_url, "downgrade", "0001")
        indexes = _get_index_names(inspect_url, "alerts")
        assert "idx_alerts_severity" not in indexes

    def test_downgrade_preserves_0001_tables(self, async_db_url, inspect_url):
        """Downgrade must not drop 0001 tables."""
        _run_alembic(async_db_url, "upgrade", "head")
        _run_alembic(async_db_url, "downgrade", "0001")

        from sqlalchemy import create_engine, inspect as sa_inspect

        engine = create_engine(inspect_url)
        try:
            insp = sa_inspect(engine)
            table_names = set(insp.get_table_names())
            required = {"runs", "run_phases", "artifacts", "run_events",
                        "alerts", "timeline_entries"}
            missing = required - table_names
            assert not missing, f"Downgrade dropped unexpected tables: {missing}"
        finally:
            engine.dispose()

    def test_upgrade_after_downgrade_succeeds(self, async_db_url, inspect_url):
        """Re-applying 0002 after a downgrade must succeed (idempotent path)."""
        _run_alembic(async_db_url, "upgrade", "head")
        _run_alembic(async_db_url, "downgrade", "0001")
        _run_alembic(async_db_url, "upgrade", "head")  # Must not raise
        cols = _get_table_columns(inspect_url, "alerts")
        assert "status" in cols, "status column missing after re-upgrade"


# ---------------------------------------------------------------------------
# Repository layer tests (pure unit tests — no DB needed)
# ---------------------------------------------------------------------------

class TestAlertRepositoryToDict:
    """AlertRepository._to_dict() must expose triggered_at and status."""

    def _make_alert_row(self, **overrides):
        """Build an Alert ORM object with sensible defaults."""
        from orchestrator.db.models import Alert

        row = Alert(
            alert_type=overrides.pop("alert_type", "test"),
            severity=overrides.pop("severity", "info"),
            message=overrides.pop("message", "Test alert"),
            data=overrides.pop("data", {}),
            status=overrides.pop("status", "active"),
            created_at=overrides.pop(
                "created_at",
                datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc),
            ),
        )
        row.id = overrides.pop("id", 1)
        row.run_id = overrides.pop("run_id", None)
        for k, v in overrides.items():
            setattr(row, k, v)
        return row

    def test_to_dict_has_triggered_at(self):
        from orchestrator.db.repositories.alerts import AlertRepository

        row = self._make_alert_row()
        result = AlertRepository._to_dict(row)
        assert "triggered_at" in result, "triggered_at missing from AlertRepository._to_dict"
        assert result["triggered_at"] == "2024-01-15T10:30:00+00:00"

    def test_to_dict_triggered_at_equals_created_at(self):
        from orchestrator.db.repositories.alerts import AlertRepository

        row = self._make_alert_row()
        result = AlertRepository._to_dict(row)
        assert result["triggered_at"] == result["created_at"]

    def test_to_dict_has_status(self):
        from orchestrator.db.repositories.alerts import AlertRepository

        row = self._make_alert_row(status="resolved", severity="critical",
                                   message="Budget exceeded", id=42)
        result = AlertRepository._to_dict(row)
        assert "status" in result, "status missing from AlertRepository._to_dict"
        assert result["status"] == "resolved"

    def test_to_dict_status_defaults_to_active_when_none(self):
        """status=None should fall back to 'active' (defensive coding)."""
        from orchestrator.db.repositories.alerts import AlertRepository

        row = self._make_alert_row(status=None)
        result = AlertRepository._to_dict(row)
        assert result["status"] == "active"

    def test_to_dict_id_is_string(self):
        """id must be serialised as a string for mobile JSON consumers."""
        from orchestrator.db.repositories.alerts import AlertRepository

        row = self._make_alert_row(id=7)
        result = AlertRepository._to_dict(row)
        assert isinstance(result["id"], str), "id must be a string in API response"
        assert result["id"] == "7"

    def test_to_dict_active_status_preserved(self):
        from orchestrator.db.repositories.alerts import AlertRepository

        row = self._make_alert_row(status="active")
        result = AlertRepository._to_dict(row)
        assert result["status"] == "active"

    def test_to_dict_acknowledged_status_preserved(self):
        from orchestrator.db.repositories.alerts import AlertRepository

        row = self._make_alert_row(status="acknowledged")
        result = AlertRepository._to_dict(row)
        assert result["status"] == "acknowledged"


class TestArtifactRepositoryToMetadata:
    """ArtifactRepository._to_metadata() must expose updated_at and artifact_name."""

    def _make_artifact_row(self, **overrides):
        from orchestrator.db.models import Artifact

        row = Artifact(
            run_id=overrides.pop("run_id", "run-abc"),
            name=overrides.pop("name", "prd"),
            version=overrides.pop("version", 1),
            agent=overrides.pop("agent", "pm"),
            schema_name=overrides.pop("schema_name", "prd"),
            content=overrides.pop("content", {}),
            size_bytes=overrides.pop("size_bytes", 512),
            run_status=overrides.pop("run_status", "completed"),
            created_at=overrides.pop(
                "created_at",
                datetime(2024, 1, 15, 11, 0, 0, tzinfo=timezone.utc),
            ),
        )
        row.id = overrides.pop("id", 1)
        for k, v in overrides.items():
            setattr(row, k, v)
        return row

    def test_to_metadata_has_updated_at(self):
        from orchestrator.db.repositories.artifacts import ArtifactRepository

        row = self._make_artifact_row()
        result = ArtifactRepository._to_metadata(row)
        assert "updated_at" in result, "updated_at missing from ArtifactRepository._to_metadata"
        assert result["updated_at"] == "2024-01-15T11:00:00+00:00"

    def test_to_metadata_has_artifact_name(self):
        from orchestrator.db.repositories.artifacts import ArtifactRepository

        row = self._make_artifact_row(name="architecture", agent="architect",
                                      schema_name="architecture")
        result = ArtifactRepository._to_metadata(row)
        assert "artifact_name" in result, "artifact_name missing"
        assert result["artifact_name"] == "architecture"

    def test_to_metadata_has_schema_field(self):
        """'schema' key must be present (mobile artifact search response contract)."""
        from orchestrator.db.repositories.artifacts import ArtifactRepository

        row = self._make_artifact_row(name="tasks", agent="tpm", schema_name="tasks")
        result = ArtifactRepository._to_metadata(row)
        assert "schema" in result
        assert result["schema"] == "tasks"

    def test_to_metadata_version_matches(self):
        """'version' (int) must be present alongside legacy 'current_version'."""
        from orchestrator.db.repositories.artifacts import ArtifactRepository

        row = self._make_artifact_row(version=3)
        result = ArtifactRepository._to_metadata(row)
        assert result.get("version") == 3
        assert result.get("current_version") == 3

    def test_to_metadata_updated_at_equals_created_at(self):
        """Since artifacts are immutable per version, updated_at == created_at."""
        from orchestrator.db.repositories.artifacts import ArtifactRepository

        row = self._make_artifact_row()
        result = ArtifactRepository._to_metadata(row)
        assert result["updated_at"] == result["created_at"]

    def test_to_metadata_preserves_legacy_name_key(self):
        """Legacy 'name' key must still be present (dashboard consumers)."""
        from orchestrator.db.repositories.artifacts import ArtifactRepository

        row = self._make_artifact_row(name="prd")
        result = ArtifactRepository._to_metadata(row)
        assert result.get("name") == "prd"

    def test_to_metadata_handles_null_schema(self):
        """schema_name=None should produce None without crashing."""
        from orchestrator.db.repositories.artifacts import ArtifactRepository

        row = self._make_artifact_row(schema_name=None)
        result = ArtifactRepository._to_metadata(row)
        assert "schema" in result
        assert result["schema"] is None

    def test_to_metadata_handles_null_created_at(self):
        """created_at=None should produce None for updated_at without raising."""
        from orchestrator.db.repositories.artifacts import ArtifactRepository

        row = self._make_artifact_row(created_at=None)
        result = ArtifactRepository._to_metadata(row)
        assert result["updated_at"] is None
        assert result["created_at"] is None
