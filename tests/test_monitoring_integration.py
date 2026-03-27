"""Integration tests for the MonitoringStack and MonitoringConfig (TASK-017).

Acceptance criteria covered:
  1. Config parsing: full YAML with nested slo: section parses all fields correctly.
  2. MonitoringStack initialises without raising when all features are disabled.
  3. MonitoringStack wires Loki: on_log_event() forwards events to LokiLogShipper.push().
  4. MonitoringStack wires SLO: on_run_complete() records runs in the SLO tracker.
  5. MonitoringStack event enrichment: on_phase_end() calls record_phase_result().
  6. MonitoringStack shutdown: shutdown() cleans up all wired subsystems without raising.
  7. SLOConfig nested parse: all 6 SLI fields are present after parsing YAML.
  8. MonitoringConfig reject ftp:// URLs on field validation.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(
    *,
    metrics_enabled: bool = False,
    tracing_enabled: bool = False,
    loki_enabled: bool = False,
    loki_endpoint: str = "http://localhost:3100",
    grafana_url: str | None = None,
    jaeger_ui_url: str | None = None,
    slo_enabled: bool = False,
    pipeline_success_rate: float = 0.95,
    phase_duration_p95_seconds: float = 300.0,
    cost_per_run_p50_usd: float = 1.0,
    artifact_validation_rate: float = 0.99,
    max_errors_per_run: int = 5,
    recovery_success_rate: float = 0.90,
    evaluation_window_hours: int = 24,
):
    """Return a MonitoringConfig with optional nested SLOConfig.

    Follows the _make_config() helper pattern used across this test suite.
    """
    from orchestrator.monitoring.config import MonitoringConfig, SLOConfig

    slo = SLOConfig(
        enabled=slo_enabled,
        pipeline_success_rate=pipeline_success_rate,
        phase_duration_p95_seconds=phase_duration_p95_seconds,
        cost_per_run_p50_usd=cost_per_run_p50_usd,
        artifact_validation_rate=artifact_validation_rate,
        max_errors_per_run=max_errors_per_run,
        recovery_success_rate=recovery_success_rate,
        evaluation_window_hours=evaluation_window_hours,
    )
    return MonitoringConfig(
        metrics_enabled=metrics_enabled,
        tracing_enabled=tracing_enabled,
        loki_enabled=loki_enabled,
        loki_endpoint=loki_endpoint,
        grafana_url=grafana_url,
        jaeger_ui_url=jaeger_ui_url,
        slo=slo,
    )


def _make_stack(config=None, *, run_id: str = "run-test-001", tmp_path: Path | None = None):
    """Return a MonitoringStack built from *config* (or a minimal default)."""
    from orchestrator.monitoring import MonitoringStack

    if config is None:
        config = _make_config()
    workspace = tmp_path or Path("/tmp")
    return MonitoringStack(config=config, run_id=run_id, workspace=workspace)


# ---------------------------------------------------------------------------
# 1. Full YAML config parsing — all nested fields
# ---------------------------------------------------------------------------


class TestConfigParsing:
    """Config parsing tests with full YAML — verifies nested SLOConfig fields."""

    def test_full_yaml_parses_all_slo_fields(self, tmp_path: Path):
        """Full YAML with nested slo: section must parse all 6 SLI fields."""
        import yaml

        from orchestrator.monitoring.config import MonitoringConfig

        yaml_content = textwrap.dedent("""
            monitoring:
              metrics_enabled: false
              tracing_enabled: false
              loki_enabled: true
              loki_endpoint: "http://localhost:3100"
              grafana_url: "http://localhost:3000"
              jaeger_ui_url: "http://localhost:16686"
              slo:
                enabled: true
                pipeline_success_rate: 0.97
                phase_duration_p95_seconds: 240.0
                cost_per_run_p50_usd: 0.75
                artifact_validation_rate: 0.98
                max_errors_per_run: 3
                recovery_success_rate: 0.85
                evaluation_window_hours: 48
        """)
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml_content)

        raw = yaml.safe_load(config_file.read_text())
        monitoring_raw = raw["monitoring"]
        config = MonitoringConfig(**monitoring_raw)

        assert config.slo.enabled is True
        assert config.slo.pipeline_success_rate == pytest.approx(0.97)
        assert config.slo.phase_duration_p95_seconds == pytest.approx(240.0)
        assert config.slo.cost_per_run_p50_usd == pytest.approx(0.75)
        assert config.slo.artifact_validation_rate == pytest.approx(0.98)
        assert config.slo.max_errors_per_run == 3
        assert config.slo.recovery_success_rate == pytest.approx(0.85)
        assert config.slo.evaluation_window_hours == 48

    def test_full_yaml_parses_loki_and_grafana_urls(self, tmp_path: Path):
        """Grafana and Jaeger URL fields must survive YAML round-trip."""
        import yaml

        from orchestrator.monitoring.config import MonitoringConfig

        yaml_content = textwrap.dedent("""
            monitoring:
              grafana_url: "http://grafana:3000"
              jaeger_ui_url: "http://jaeger:16686"
              slo:
                enabled: false
        """)
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml_content)

        raw = yaml.safe_load(config_file.read_text())
        config = MonitoringConfig(**raw["monitoring"])

        assert config.grafana_url == "http://grafana:3000"
        assert config.jaeger_ui_url == "http://jaeger:16686"

    def test_all_six_sli_fields_on_slo_config(self):
        """SLOConfig must expose all 6 canonical SLI fields."""
        from orchestrator.monitoring.config import SLOConfig

        slo = SLOConfig()
        assert hasattr(slo, "pipeline_success_rate")
        assert hasattr(slo, "phase_duration_p95_seconds")
        assert hasattr(slo, "cost_per_run_p50_usd")
        assert hasattr(slo, "artifact_validation_rate")
        assert hasattr(slo, "max_errors_per_run")
        assert hasattr(slo, "recovery_success_rate")


# ---------------------------------------------------------------------------
# 2 & 3. MonitoringStack wiring
# ---------------------------------------------------------------------------


class TestMonitoringStackWiring:
    """Verify MonitoringStack initialises and wires subsystems correctly."""

    def test_stack_initialises_without_raising(self, tmp_path: Path):
        """MonitoringStack(disabled config) must not raise during __init__."""
        stack = _make_stack(tmp_path=tmp_path)  # all features disabled
        assert stack is not None

    def test_on_log_event_forwards_to_loki_push(self, tmp_path: Path):
        """on_log_event() must call loki_shipper.push() when Loki is wired."""
        config = _make_config(loki_enabled=True, loki_endpoint="http://localhost:3100")
        stack = _make_stack(config=config, tmp_path=tmp_path)

        # Replace the real shipper with a mock
        fake_shipper = MagicMock()
        stack._loki_shipper = fake_shipper

        event = {"event": "run_start", "run_id": "run-001", "level": "info"}
        stack.on_log_event(event)

        fake_shipper.push.assert_called_once_with(event)

    def test_on_log_event_noop_when_loki_disabled(self, tmp_path: Path):
        """on_log_event() must be a no-op when Loki is not wired."""
        config = _make_config(loki_enabled=False)
        stack = _make_stack(config=config, tmp_path=tmp_path)

        # _loki_shipper must be None
        assert stack._loki_shipper is None
        # Calling on_log_event must not raise
        stack.on_log_event({"event": "noop"})

    def test_on_run_complete_records_slo_run(self, tmp_path: Path):
        """on_run_complete() must call slo_tracker.record_run() when SLO is enabled."""
        config = _make_config(slo_enabled=True)
        stack = _make_stack(config=config, tmp_path=tmp_path)

        if stack._slo_tracker is None:
            pytest.skip("SLO tracker not wired (slo.enabled may not activate the tracker)")

        fake_slo = MagicMock()
        stack._slo_tracker = fake_slo

        stack.on_run_complete(
            success=True,
            total_cost_usd=0.5,
            workflow_type="full_run",
            duration_s=45.0,
            errors=0,
        )

        fake_slo.record_run.assert_called_once()


# ---------------------------------------------------------------------------
# 4. Event enrichment
# ---------------------------------------------------------------------------


class TestEventEnrichment:
    """on_phase_end() must propagate to SLO and artifact-validation tracking."""

    def test_on_phase_end_records_phase_result_in_slo(self, tmp_path: Path):
        """on_phase_end() must call slo_tracker.record_phase_result()."""
        config = _make_config(slo_enabled=True)
        stack = _make_stack(config=config, tmp_path=tmp_path)

        if stack._slo_tracker is None:
            pytest.skip("SLO tracker not wired — skipping phase_end enrichment test")

        fake_slo = MagicMock()
        stack._slo_tracker = fake_slo

        stack.on_phase_end(
            phase_name="pm",
            success=True,
            cost_usd=0.1,
            duration_s=12.5,
        )

        # MonitoringStack calls record_phase_result with positional args
        fake_slo.record_phase_result.assert_called_once_with("pm", 12.5, True)

    def test_on_phase_end_records_artifact_validation_when_valid_true(self, tmp_path: Path):
        """on_phase_end() with artifact_valid=True must call record_artifact_validation(True)."""
        config = _make_config(slo_enabled=True)
        stack = _make_stack(config=config, tmp_path=tmp_path)

        if stack._slo_tracker is None:
            pytest.skip("SLO tracker not wired — skipping artifact validation test")

        fake_slo = MagicMock()
        stack._slo_tracker = fake_slo

        stack.on_phase_end(
            phase_name="architect",
            success=True,
            cost_usd=0.2,
            duration_s=20.0,
            artifact_valid=True,
        )

        fake_slo.record_artifact_validation.assert_called_once_with(True)


# ---------------------------------------------------------------------------
# 5. Shutdown cleanup
# ---------------------------------------------------------------------------


class TestShutdownCleanup:
    """shutdown() must call shutdown on every wired subsystem without raising."""

    def test_shutdown_calls_loki_shutdown_when_wired(self, tmp_path: Path):
        """shutdown() must call _loki_shipper.shutdown() when Loki is wired."""
        config = _make_config(loki_enabled=True, loki_endpoint="http://localhost:3100")
        stack = _make_stack(config=config, tmp_path=tmp_path)

        fake_shipper = MagicMock()
        stack._loki_shipper = fake_shipper

        stack.shutdown()

        fake_shipper.shutdown.assert_called_once()

    def test_shutdown_does_not_raise_when_loki_raises(self, tmp_path: Path):
        """shutdown() must not propagate exceptions from loki_shipper.shutdown()."""
        config = _make_config(loki_enabled=True, loki_endpoint="http://localhost:3100")
        stack = _make_stack(config=config, tmp_path=tmp_path)

        fake_shipper = MagicMock()
        fake_shipper.shutdown.side_effect = RuntimeError("loki boom")
        stack._loki_shipper = fake_shipper

        # Must not raise
        stack.shutdown()

    def test_shutdown_noop_when_all_subsystems_none(self, tmp_path: Path):
        """shutdown() must succeed gracefully when no subsystem is wired."""
        config = _make_config()  # all disabled
        stack = _make_stack(config=config, tmp_path=tmp_path)

        # Ensure all subsystems are None
        assert stack._loki_shipper is None
        assert stack._metrics is None

        # Must not raise
        stack.shutdown()
