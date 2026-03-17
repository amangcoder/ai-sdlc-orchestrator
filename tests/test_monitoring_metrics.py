"""Tests for MetricsManager shutdown behaviour (TASK-005).

These tests verify that:
- MetricsManager.shutdown() stops the Prometheus HTTP server thread and
  releases the port so a second instance can be created on the same port.
- The enabled=False path skips server startup and shutdown() is a no-op.
- MonitoringStack.shutdown() calls metrics.shutdown() wrapped in try/except.
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
            s.bind(("127.0.0.1", port))
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

        # Port should be in use now (server started)
        assert _port_in_use(port), "expected port to be bound after MetricsManager init"

        mgr.shutdown()

        # Give the OS a moment to release the socket
        time.sleep(0.05)

        assert not _port_in_use(port), "expected port to be free after MetricsManager.shutdown()"

    def test_second_instance_succeeds_after_first_shutdown(self):
        """Creating a second MetricsManager on the same port must NOT raise OSError."""
        from orchestrator.monitoring.metrics import MetricsManager

        port = _free_port()
        mgr1 = MetricsManager(port=port)
        assert _port_in_use(port)

        mgr1.shutdown()
        time.sleep(0.05)

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
