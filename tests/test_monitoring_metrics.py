"""Tests for MetricsManager shutdown behaviour (TASK-005) and extended metrics (TASK-002).

These tests verify that:
- MetricsManager.shutdown() stops the Prometheus HTTP server thread and
  releases the port so a second instance can be created on the same port.
- The enabled=False path skips server startup and shutdown() is a no-op.
- MonitoringStack.shutdown() calls metrics.shutdown() wrapped in try/except.
- Three new metrics are registered: burn_rate_usd_per_minute (Gauge),
  run_duration_seconds (Histogram), artifacts_produced_total (Counter).
- record_burn_rate, record_run_duration, record_artifact_produced work correctly.
- MonitoringStack wires the new metrics into on_step_end, on_run_complete,
  and on_artifact_produced event handlers.
"""

from __future__ import annotations

import socket
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _free_port() -> int:
    """Return a TCP port that is free at the time of the call."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _port_in_use(port: int) -> bool:
    """Return True if something is already listening on *port*."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("0.0.0.0", port))
            return False
        except OSError:
            return True


# ---------------------------------------------------------------------------
# Unit tests — MetricsManager (patching start_http_server directly)
# ---------------------------------------------------------------------------


class TestMetricsManagerShutdownUnit:
    """Unit tests that patch start_http_server so no real HTTP server is spawned."""

    def _make_httpd(self) -> MagicMock:
        httpd = MagicMock()
        httpd.shutdown = MagicMock()
        httpd.server_close = MagicMock()
        return httpd

    # ------------------------------------------------------------------

    def test_shutdown_calls_httpd_shutdown_and_server_close(self):
        """shutdown() must call httpd.shutdown() then httpd.server_close()."""
        httpd = self._make_httpd()

        with patch("orchestrator.monitoring.metrics.HAS_PROMETHEUS", True), \
             patch("orchestrator.monitoring.metrics.start_http_server", return_value=(httpd, MagicMock())), \
             patch("orchestrator.monitoring.metrics.CollectorRegistry", MagicMock()), \
             patch("orchestrator.monitoring.metrics.Counter", MagicMock()), \
             patch("orchestrator.monitoring.metrics.Gauge", MagicMock()), \
             patch("orchestrator.monitoring.metrics.Histogram", MagicMock()):
            from orchestrator.monitoring.metrics import MetricsManager

            mgr = MetricsManager(port=9999)
            assert mgr.enabled is True
            assert mgr._http_server is httpd

            mgr.shutdown()

        httpd.shutdown.assert_called_once()
        httpd.server_close.assert_called_once()

    def test_shutdown_clears_http_server_reference(self):
        """After shutdown(), _http_server must be None."""
        httpd = self._make_httpd()

        with patch("orchestrator.monitoring.metrics.HAS_PROMETHEUS", True), \
             patch("orchestrator.monitoring.metrics.start_http_server", return_value=(httpd, MagicMock())), \
             patch("orchestrator.monitoring.metrics.CollectorRegistry", MagicMock()), \
             patch("orchestrator.monitoring.metrics.Counter", MagicMock()), \
             patch("orchestrator.monitoring.metrics.Gauge", MagicMock()), \
             patch("orchestrator.monitoring.metrics.Histogram", MagicMock()):
            from orchestrator.monitoring.metrics import MetricsManager

            mgr = MetricsManager(port=9999)
            mgr.shutdown()
            assert mgr._http_server is None

    def test_shutdown_idempotent(self):
        """Calling shutdown() twice must not raise; httpd.shutdown() is only called once."""
        httpd = self._make_httpd()

        with patch("orchestrator.monitoring.metrics.HAS_PROMETHEUS", True), \
             patch("orchestrator.monitoring.metrics.start_http_server", return_value=(httpd, MagicMock())), \
             patch("orchestrator.monitoring.metrics.CollectorRegistry", MagicMock()), \
             patch("orchestrator.monitoring.metrics.Counter", MagicMock()), \
             patch("orchestrator.monitoring.metrics.Gauge", MagicMock()), \
             patch("orchestrator.monitoring.metrics.Histogram", MagicMock()):
            from orchestrator.monitoring.metrics import MetricsManager

            mgr = MetricsManager(port=9999)
            mgr.shutdown()
            mgr.shutdown()  # second call must be a no-op

        httpd.shutdown.assert_called_once()
        httpd.server_close.assert_called_once()

    def test_shutdown_noop_when_disabled(self):
        """When prometheus_client is not available (enabled=False), shutdown() is a no-op."""
        with patch("orchestrator.monitoring.metrics.HAS_PROMETHEUS", False):
            from orchestrator.monitoring.metrics import MetricsManager

            mgr = MetricsManager(port=9999)

        assert mgr.enabled is False
        assert mgr._http_server is None
        # Must not raise
        mgr.shutdown()
        assert mgr._http_server is None

    def test_shutdown_noop_when_server_not_started(self):
        """If start_http_server raised OSError, _http_server stays None and shutdown() is a no-op."""
        with patch("orchestrator.monitoring.metrics.HAS_PROMETHEUS", True), \
             patch("orchestrator.monitoring.metrics.start_http_server", side_effect=OSError("Address already in use")), \
             patch("orchestrator.monitoring.metrics.CollectorRegistry", MagicMock()), \
             patch("orchestrator.monitoring.metrics.Counter", MagicMock()), \
             patch("orchestrator.monitoring.metrics.Gauge", MagicMock()), \
             patch("orchestrator.monitoring.metrics.Histogram", MagicMock()):
            from orchestrator.monitoring.metrics import MetricsManager

            mgr = MetricsManager(port=9999)
            assert mgr._http_server is None
            # Must not raise
            mgr.shutdown()

    def test_shutdown_handles_httpd_exception_gracefully(self):
        """If httpd.shutdown() raises, MetricsManager.shutdown() must not propagate."""
        httpd = self._make_httpd()
        httpd.shutdown.side_effect = RuntimeError("server already dead")

        with patch("orchestrator.monitoring.metrics.HAS_PROMETHEUS", True), \
             patch("orchestrator.monitoring.metrics.start_http_server", return_value=(httpd, MagicMock())), \
             patch("orchestrator.monitoring.metrics.CollectorRegistry", MagicMock()), \
             patch("orchestrator.monitoring.metrics.Counter", MagicMock()), \
             patch("orchestrator.monitoring.metrics.Gauge", MagicMock()), \
             patch("orchestrator.monitoring.metrics.Histogram", MagicMock()):
            from orchestrator.monitoring.metrics import MetricsManager

            mgr = MetricsManager(port=9999)
            # Must not raise even when httpd.shutdown() fails
            mgr.shutdown()

    def test_http_server_set_when_start_returns_tuple(self):
        """_http_server is set to result[0] when start_http_server returns (server, thread)."""
        httpd = self._make_httpd()
        fake_thread = MagicMock()

        with patch("orchestrator.monitoring.metrics.HAS_PROMETHEUS", True), \
             patch("orchestrator.monitoring.metrics.start_http_server", return_value=(httpd, fake_thread)), \
             patch("orchestrator.monitoring.metrics.CollectorRegistry", MagicMock()), \
             patch("orchestrator.monitoring.metrics.Counter", MagicMock()), \
             patch("orchestrator.monitoring.metrics.Gauge", MagicMock()), \
             patch("orchestrator.monitoring.metrics.Histogram", MagicMock()):
            from orchestrator.monitoring.metrics import MetricsManager

            mgr = MetricsManager(port=9999)
            assert mgr._http_server is httpd

        mgr.shutdown()

    def test_http_server_none_when_start_returns_none(self):
        """_http_server stays None when start_http_server returns None (old prometheus_client)."""
        with patch("orchestrator.monitoring.metrics.HAS_PROMETHEUS", True), \
             patch("orchestrator.monitoring.metrics.start_http_server", return_value=None), \
             patch("orchestrator.monitoring.metrics.CollectorRegistry", MagicMock()), \
             patch("orchestrator.monitoring.metrics.Counter", MagicMock()), \
             patch("orchestrator.monitoring.metrics.Gauge", MagicMock()), \
             patch("orchestrator.monitoring.metrics.Histogram", MagicMock()):
            from orchestrator.monitoring.metrics import MetricsManager

            mgr = MetricsManager(port=9999)
            assert mgr._http_server is None
            # shutdown must be a no-op (server was None)
            mgr.shutdown()


# ---------------------------------------------------------------------------
# Integration tests — port reuse after shutdown (requires prometheus_client)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not __import__("importlib").util.find_spec("prometheus_client"),
    reason="prometheus_client not installed",
)
class TestMetricsManagerPortReuseIntegration:
    """Integration tests that start a real server and verify port lifecycle."""

    def test_port_released_after_shutdown(self):
        """After shutdown(), the port must no longer be bound."""
        from orchestrator.monitoring.metrics import MetricsManager

        port = _free_port()
        mgr = MetricsManager(port=port)

        # Give the background thread a moment to bind
        time.sleep(0.1)

        # Port should be in use now (server started)
        assert _port_in_use(port), "expected port to be bound after MetricsManager init"

        mgr.shutdown()

        # Give the OS a moment to release the socket
        time.sleep(0.3)

        assert not _port_in_use(port), "expected port to be free after MetricsManager.shutdown()"

    def test_second_instance_succeeds_after_first_shutdown(self):
        """Creating a second MetricsManager on the same port must NOT raise OSError."""
        from orchestrator.monitoring.metrics import MetricsManager

        port = _free_port()
        mgr1 = MetricsManager(port=port)
        time.sleep(0.1)
        assert _port_in_use(port)

        mgr1.shutdown()
        time.sleep(0.1)

        # Second manager on the same port should succeed without OSError
        mgr2 = MetricsManager(port=port)
        assert mgr2._http_server is not None, (
            "second MetricsManager should have started its HTTP server successfully"
        )
        mgr2.shutdown()


# ---------------------------------------------------------------------------
# Unit tests — MonitoringStack.shutdown() delegates to metrics.shutdown()
# ---------------------------------------------------------------------------


class TestMonitoringStackShutdownDelegation:
    """Verify MonitoringStack.shutdown() calls metrics.shutdown() with error isolation."""

    def _make_config(self, metrics_enabled: bool = False):
        from orchestrator.monitoring.config import MonitoringConfig
        return MonitoringConfig(metrics_enabled=metrics_enabled)

    def test_shutdown_calls_metrics_shutdown_when_present(self):
        """MonitoringStack.shutdown() must call self._metrics.shutdown()."""
        from orchestrator.monitoring import MonitoringStack

        config = self._make_config()
        stack = MonitoringStack(config=config, run_id="run-1", workspace=Path("/tmp"))

        fake_metrics = MagicMock()
        stack._metrics = fake_metrics

        stack.shutdown()

        fake_metrics.shutdown.assert_called_once()

    def test_shutdown_continues_when_metrics_shutdown_raises(self):
        """MonitoringStack.shutdown() must NOT propagate exceptions from metrics.shutdown()."""
        from orchestrator.monitoring import MonitoringStack

        config = self._make_config()
        stack = MonitoringStack(config=config, run_id="run-1", workspace=Path("/tmp"))

        fake_metrics = MagicMock()
        fake_metrics.shutdown.side_effect = RuntimeError("boom")
        stack._metrics = fake_metrics

        fake_alerting = MagicMock()
        stack._alerting = fake_alerting

        # Must not raise; alerting.shutdown() must still be called
        stack.shutdown()

        fake_metrics.shutdown.assert_called_once()
        fake_alerting.shutdown.assert_called_once()

    def test_shutdown_skips_metrics_when_none(self):
        """When _metrics is None, shutdown() must not fail."""
        from orchestrator.monitoring import MonitoringStack

        config = self._make_config()
        stack = MonitoringStack(config=config, run_id="run-1", workspace=Path("/tmp"))

        assert stack._metrics is None
        # Must not raise
        stack.shutdown()

    def test_shutdown_order_metrics_before_alerting(self):
        """Metrics must be shut down before alerting (dependency order)."""
        from orchestrator.monitoring import MonitoringStack

        config = self._make_config()
        stack = MonitoringStack(config=config, run_id="run-1", workspace=Path("/tmp"))

        call_order: list[str] = []

        fake_metrics = MagicMock()
        fake_metrics.shutdown.side_effect = lambda: call_order.append("metrics")
        stack._metrics = fake_metrics

        fake_alerting = MagicMock()
        fake_alerting.shutdown.side_effect = lambda: call_order.append("alerting")
        stack._alerting = fake_alerting

        stack.shutdown()

        assert call_order == ["metrics", "alerting"], (
            f"Expected shutdown order [metrics, alerting], got {call_order}"
        )

    def test_shutdown_order_metrics_before_tracing(self):
        """Metrics must be shut down before tracing."""
        from orchestrator.monitoring import MonitoringStack

        config = self._make_config()
        stack = MonitoringStack(config=config, run_id="run-1", workspace=Path("/tmp"))

        call_order: list[str] = []

        fake_metrics = MagicMock()
        fake_metrics.shutdown.side_effect = lambda: call_order.append("metrics")
        stack._metrics = fake_metrics

        fake_tracing = MagicMock()
        fake_tracing.shutdown.side_effect = lambda: call_order.append("tracing")
        stack._tracing = fake_tracing

        stack.shutdown()

        assert call_order.index("metrics") < call_order.index("tracing"), (
            f"metrics must shut down before tracing; got order {call_order}"
        )


# ---------------------------------------------------------------------------
# Unit tests — TASK-002: three new metrics in MetricsManager
# ---------------------------------------------------------------------------


def _make_patched_metrics_manager(port: int = 9999):
    """Return a MetricsManager created under full prometheus_client mock."""
    httpd = MagicMock()
    httpd.shutdown = MagicMock()
    httpd.server_close = MagicMock()

    mock_gauge = MagicMock()
    mock_histogram = MagicMock()
    mock_counter = MagicMock()

    with patch("orchestrator.monitoring.metrics.HAS_PROMETHEUS", True), \
         patch("orchestrator.monitoring.metrics.start_http_server", return_value=(httpd, MagicMock())), \
         patch("orchestrator.monitoring.metrics.CollectorRegistry", MagicMock()), \
         patch("orchestrator.monitoring.metrics.Counter", mock_counter), \
         patch("orchestrator.monitoring.metrics.Gauge", mock_gauge), \
         patch("orchestrator.monitoring.metrics.Histogram", mock_histogram):
        from orchestrator.monitoring.metrics import MetricsManager
        mgr = MetricsManager(port=port)

    return mgr, mock_gauge, mock_histogram, mock_counter


class TestExtendedMetricsRegistration:
    """Verify the three new TASK-002 metrics are registered via HAS_PROMETHEUS guard."""

    def _common_patches(self):
        """Return a context-manager that patches away prometheus_client."""
        return (
            patch("orchestrator.monitoring.metrics.HAS_PROMETHEUS", True),
            patch("orchestrator.monitoring.metrics.start_http_server", return_value=(MagicMock(), MagicMock())),
            patch("orchestrator.monitoring.metrics.CollectorRegistry", MagicMock()),
        )

    def test_burn_rate_gauge_registered_with_correct_label(self):
        """burn_rate_usd_per_minute Gauge must be registered with label workflow_type."""
        mock_gauge = MagicMock()

        with patch("orchestrator.monitoring.metrics.HAS_PROMETHEUS", True), \
             patch("orchestrator.monitoring.metrics.start_http_server", return_value=(MagicMock(), MagicMock())), \
             patch("orchestrator.monitoring.metrics.CollectorRegistry", MagicMock()), \
             patch("orchestrator.monitoring.metrics.Counter", MagicMock()), \
             patch("orchestrator.monitoring.metrics.Gauge", mock_gauge), \
             patch("orchestrator.monitoring.metrics.Histogram", MagicMock()):
            from orchestrator.monitoring.metrics import MetricsManager
            MetricsManager(port=9999)

        # Find the call that registered burn_rate_usd_per_minute
        gauge_calls = mock_gauge.call_args_list
        names = [c.args[0] if c.args else c.kwargs.get("name", "") for c in gauge_calls]
        assert "orchestrator_burn_rate_usd_per_minute" in names, (
            f"Expected 'orchestrator_burn_rate_usd_per_minute' Gauge to be registered; got {names}"
        )

    def test_run_duration_histogram_registered_with_correct_buckets_and_labels(self):
        """run_duration_seconds Histogram must use buckets=[10,30,60,120,300,600]."""
        mock_histogram = MagicMock()

        with patch("orchestrator.monitoring.metrics.HAS_PROMETHEUS", True), \
             patch("orchestrator.monitoring.metrics.start_http_server", return_value=(MagicMock(), MagicMock())), \
             patch("orchestrator.monitoring.metrics.CollectorRegistry", MagicMock()), \
             patch("orchestrator.monitoring.metrics.Counter", MagicMock()), \
             patch("orchestrator.monitoring.metrics.Gauge", MagicMock()), \
             patch("orchestrator.monitoring.metrics.Histogram", mock_histogram):
            from orchestrator.monitoring.metrics import MetricsManager
            MetricsManager(port=9999)

        histogram_calls = mock_histogram.call_args_list
        run_duration_call = None
        for call in histogram_calls:
            name = call.args[0] if call.args else call.kwargs.get("name", "")
            if name == "orchestrator_run_duration_seconds":
                run_duration_call = call
                break

        assert run_duration_call is not None, (
            "orchestrator_run_duration_seconds Histogram was not registered"
        )

        # Buckets must be [10, 30, 60, 120, 300, 600]
        call_kwargs = run_duration_call.kwargs
        call_args = run_duration_call.args
        # buckets can be positional (4th arg) or keyword
        if "buckets" in call_kwargs:
            buckets = call_kwargs["buckets"]
        else:
            # positional: name, description, labels, buckets
            buckets = call_args[3] if len(call_args) > 3 else None

        assert buckets == [10, 30, 60, 120, 300, 600], (
            f"Expected buckets [10,30,60,120,300,600], got {buckets}"
        )

        # Labels must include workflow_type and status
        if "labelnames" in call_kwargs:
            labels = list(call_kwargs["labelnames"])
        elif len(call_args) > 2:
            labels = list(call_args[2])
        else:
            labels = []

        assert "workflow_type" in labels, f"workflow_type label missing; got {labels}"
        assert "status" in labels, f"status label missing; got {labels}"

    def test_artifacts_produced_counter_registered_with_correct_labels(self):
        """artifacts_produced_total Counter must have labels artifact_type and agent."""
        mock_counter = MagicMock()

        with patch("orchestrator.monitoring.metrics.HAS_PROMETHEUS", True), \
             patch("orchestrator.monitoring.metrics.start_http_server", return_value=(MagicMock(), MagicMock())), \
             patch("orchestrator.monitoring.metrics.CollectorRegistry", MagicMock()), \
             patch("orchestrator.monitoring.metrics.Counter", mock_counter), \
             patch("orchestrator.monitoring.metrics.Gauge", MagicMock()), \
             patch("orchestrator.monitoring.metrics.Histogram", MagicMock()):
            from orchestrator.monitoring.metrics import MetricsManager
            MetricsManager(port=9999)

        counter_calls = mock_counter.call_args_list
        artifacts_call = None
        for call in counter_calls:
            name = call.args[0] if call.args else call.kwargs.get("name", "")
            if name == "orchestrator_artifacts_produced_total":
                artifacts_call = call
                break

        assert artifacts_call is not None, (
            "orchestrator_artifacts_produced_total Counter was not registered"
        )

        call_kwargs = artifacts_call.kwargs
        call_args = artifacts_call.args
        if "labelnames" in call_kwargs:
            labels = list(call_kwargs["labelnames"])
        elif len(call_args) > 2:
            labels = list(call_args[2])
        else:
            labels = []

        assert "artifact_type" in labels, f"artifact_type label missing; got {labels}"
        assert "agent" in labels, f"agent label missing; got {labels}"

    def test_metrics_not_registered_when_prometheus_unavailable(self):
        """When HAS_PROMETHEUS is False, none of the new attributes must exist."""
        with patch("orchestrator.monitoring.metrics.HAS_PROMETHEUS", False):
            from orchestrator.monitoring.metrics import MetricsManager
            mgr = MetricsManager(port=9999)

        assert mgr.enabled is False
        assert not hasattr(mgr, "burn_rate_usd_per_minute")
        assert not hasattr(mgr, "run_duration_seconds")
        assert not hasattr(mgr, "artifacts_produced_total")


class TestRecordBurnRate:
    """Unit tests for MetricsManager.record_burn_rate."""

    def _make_mgr(self):
        with patch("orchestrator.monitoring.metrics.HAS_PROMETHEUS", True), \
             patch("orchestrator.monitoring.metrics.start_http_server", return_value=(MagicMock(), MagicMock())), \
             patch("orchestrator.monitoring.metrics.CollectorRegistry", MagicMock()), \
             patch("orchestrator.monitoring.metrics.Counter", MagicMock()), \
             patch("orchestrator.monitoring.metrics.Gauge", MagicMock()), \
             patch("orchestrator.monitoring.metrics.Histogram", MagicMock()):
            from orchestrator.monitoring.metrics import MetricsManager
            return MetricsManager(port=9999)

    def test_record_burn_rate_calls_set_on_gauge(self):
        """record_burn_rate(wt, rate) must call gauge.labels(...).set(rate)."""
        mgr = self._make_mgr()
        mock_labels = MagicMock()
        mgr.burn_rate_usd_per_minute = MagicMock()
        mgr.burn_rate_usd_per_minute.labels.return_value = mock_labels

        mgr.record_burn_rate("full_run", 0.42)

        mgr.burn_rate_usd_per_minute.labels.assert_called_once_with(workflow_type="full_run")
        mock_labels.set.assert_called_once_with(0.42)

    def test_record_burn_rate_noop_when_disabled(self):
        """record_burn_rate must do nothing when metrics are disabled."""
        with patch("orchestrator.monitoring.metrics.HAS_PROMETHEUS", False):
            from orchestrator.monitoring.metrics import MetricsManager
            mgr = MetricsManager(port=9999)

        # Must not raise
        mgr.record_burn_rate("full_run", 0.42)


class TestRecordRunDuration:
    """Unit tests for MetricsManager.record_run_duration."""

    def _make_mgr(self):
        with patch("orchestrator.monitoring.metrics.HAS_PROMETHEUS", True), \
             patch("orchestrator.monitoring.metrics.start_http_server", return_value=(MagicMock(), MagicMock())), \
             patch("orchestrator.monitoring.metrics.CollectorRegistry", MagicMock()), \
             patch("orchestrator.monitoring.metrics.Counter", MagicMock()), \
             patch("orchestrator.monitoring.metrics.Gauge", MagicMock()), \
             patch("orchestrator.monitoring.metrics.Histogram", MagicMock()):
            from orchestrator.monitoring.metrics import MetricsManager
            return MetricsManager(port=9999)

    def test_record_run_duration_success(self):
        """record_run_duration with status='success' must call histogram.labels(...).observe(duration)."""
        mgr = self._make_mgr()
        mock_labels = MagicMock()
        mgr.run_duration_seconds = MagicMock()
        mgr.run_duration_seconds.labels.return_value = mock_labels

        mgr.record_run_duration("full_run", "success", 120.5)

        mgr.run_duration_seconds.labels.assert_called_once_with(workflow_type="full_run", status="success")
        mock_labels.observe.assert_called_once_with(120.5)

    def test_record_run_duration_failed(self):
        """record_run_duration with status='failed' must pass that status label."""
        mgr = self._make_mgr()
        mock_labels = MagicMock()
        mgr.run_duration_seconds = MagicMock()
        mgr.run_duration_seconds.labels.return_value = mock_labels

        mgr.record_run_duration("full_run", "failed", 45.0)

        mgr.run_duration_seconds.labels.assert_called_once_with(workflow_type="full_run", status="failed")
        mock_labels.observe.assert_called_once_with(45.0)

    def test_record_run_duration_noop_when_disabled(self):
        with patch("orchestrator.monitoring.metrics.HAS_PROMETHEUS", False):
            from orchestrator.monitoring.metrics import MetricsManager
            mgr = MetricsManager(port=9999)

        mgr.record_run_duration("full_run", "success", 60.0)


class TestRecordArtifactProduced:
    """Unit tests for MetricsManager.record_artifact_produced."""

    def _make_mgr(self):
        with patch("orchestrator.monitoring.metrics.HAS_PROMETHEUS", True), \
             patch("orchestrator.monitoring.metrics.start_http_server", return_value=(MagicMock(), MagicMock())), \
             patch("orchestrator.monitoring.metrics.CollectorRegistry", MagicMock()), \
             patch("orchestrator.monitoring.metrics.Counter", MagicMock()), \
             patch("orchestrator.monitoring.metrics.Gauge", MagicMock()), \
             patch("orchestrator.monitoring.metrics.Histogram", MagicMock()):
            from orchestrator.monitoring.metrics import MetricsManager
            return MetricsManager(port=9999)

    def test_record_artifact_produced_increments_counter(self):
        """record_artifact_produced must call counter.labels(...).inc()."""
        mgr = self._make_mgr()
        mock_labels = MagicMock()
        mgr.artifacts_produced_total = MagicMock()
        mgr.artifacts_produced_total.labels.return_value = mock_labels

        mgr.record_artifact_produced("prd", "backend_engineer")

        mgr.artifacts_produced_total.labels.assert_called_once_with(
            artifact_type="prd", agent="backend_engineer"
        )
        mock_labels.inc.assert_called_once()

    def test_record_artifact_produced_noop_when_disabled(self):
        with patch("orchestrator.monitoring.metrics.HAS_PROMETHEUS", False):
            from orchestrator.monitoring.metrics import MetricsManager
            mgr = MetricsManager(port=9999)

        mgr.record_artifact_produced("prd", "backend_engineer")


# ---------------------------------------------------------------------------
# Unit tests — TASK-002: MonitoringStack event-handler wiring
# ---------------------------------------------------------------------------


class TestMonitoringStackNewMetricsWiring:
    """Verify MonitoringStack calls new metric methods via on_step_end,
    on_run_complete, and on_artifact_produced."""

    def _make_config(self, metrics_enabled: bool = False):
        from orchestrator.monitoring.config import MonitoringConfig
        return MonitoringConfig(metrics_enabled=metrics_enabled)

    def _make_stack_with_fake_metrics(self):
        from orchestrator.monitoring import MonitoringStack

        config = self._make_config()
        stack = MonitoringStack(config=config, run_id="run-42", workspace=Path("/tmp"))
        stack._metrics = MagicMock()
        return stack

    # --- on_step_end → record_burn_rate ---

    def test_on_step_end_records_burn_rate_when_cost_and_duration_nonzero(self):
        """on_step_end must call record_burn_rate when cost_usd > 0 and duration_s > 0."""
        stack = self._make_stack_with_fake_metrics()
        stack._workflow_type = "full_run"

        stack.on_step_end("pm", success=True, cost_usd=0.02, duration_s=10.0)

        expected_rate = 0.02 / 10.0 * 60.0
        stack._metrics.record_burn_rate.assert_called_once_with("full_run", pytest.approx(expected_rate))

    def test_on_step_end_skips_burn_rate_when_duration_zero(self):
        """on_step_end must NOT call record_burn_rate when duration_s == 0."""
        stack = self._make_stack_with_fake_metrics()
        stack._workflow_type = "full_run"

        stack.on_step_end("pm", success=True, cost_usd=0.02, duration_s=0.0)

        stack._metrics.record_burn_rate.assert_not_called()

    def test_on_step_end_skips_burn_rate_when_workflow_type_empty(self):
        """on_step_end must NOT call record_burn_rate when workflow_type is unknown."""
        stack = self._make_stack_with_fake_metrics()
        stack._workflow_type = ""

        stack.on_step_end("pm", success=True, cost_usd=0.02, duration_s=10.0)

        stack._metrics.record_burn_rate.assert_not_called()

    # --- on_run_complete → record_run_duration ---

    def test_on_run_complete_records_run_duration_when_duration_provided(self):
        """on_run_complete must call record_run_duration when duration_s > 0."""
        stack = self._make_stack_with_fake_metrics()

        stack.on_run_complete(
            success=True,
            total_cost_usd=1.5,
            workflow_type="full_run",
            duration_s=300.0,
        )

        stack._metrics.record_run_duration.assert_called_once_with("full_run", "success", 300.0)

    def test_on_run_complete_records_failed_status_for_failure(self):
        """on_run_complete with success=False must record status='failed'."""
        stack = self._make_stack_with_fake_metrics()

        stack.on_run_complete(
            success=False,
            total_cost_usd=0.5,
            workflow_type="full_run",
            duration_s=120.0,
        )

        stack._metrics.record_run_duration.assert_called_once_with("full_run", "failed", 120.0)

    def test_on_run_complete_skips_run_duration_when_no_duration(self):
        """on_run_complete with default duration_s=0.0 must NOT call record_run_duration."""
        stack = self._make_stack_with_fake_metrics()

        stack.on_run_complete(
            success=True,
            total_cost_usd=1.5,
            workflow_type="full_run",
        )

        stack._metrics.record_run_duration.assert_not_called()

    # --- on_artifact_produced → record_artifact_produced ---

    def test_on_artifact_produced_delegates_to_metrics(self):
        """on_artifact_produced must call metrics.record_artifact_produced."""
        stack = self._make_stack_with_fake_metrics()

        stack.on_artifact_produced("prd", "backend_engineer")

        stack._metrics.record_artifact_produced.assert_called_once_with("prd", "backend_engineer")

    def test_on_artifact_produced_noop_when_metrics_none(self):
        """on_artifact_produced must not raise when _metrics is None."""
        from orchestrator.monitoring import MonitoringStack

        config = self._make_config()
        stack = MonitoringStack(config=config, run_id="run-42", workspace=Path("/tmp"))
        assert stack._metrics is None

        # Must not raise
        stack.on_artifact_produced("prd", "backend_engineer")

    # --- workflow_type stored from on_run_start ---

    def test_on_run_start_stores_workflow_type(self):
        """on_run_start must populate _workflow_type for later burn-rate labeling."""
        from orchestrator.monitoring import MonitoringStack

        config = self._make_config()
        stack = MonitoringStack(config=config, run_id="run-42", workspace=Path("/tmp"))

        stack.on_run_start(workflow_type="mobile_feature", feature_request="Build auth")

        assert stack._workflow_type == "mobile_feature"
