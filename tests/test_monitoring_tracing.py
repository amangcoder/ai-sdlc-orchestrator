"""Tests for TracingManager span-leak fixes and context-manager API (TASK-006).

These tests verify that:
- span_context() guarantees span.end() on both success and exception paths.
- Spans are closed when an exception is raised mid-pipeline.
- TracingManager no longer calls trace.set_tracer_provider() globally.
- _cleanup_orphaned_spans() ends all open instance-level spans.
- shutdown() calls _cleanup_orphaned_spans() before provider shutdown.
- MonitoringStack.shutdown() calls tracing.shutdown() wrapped in try/except.
- The legacy start_*/end_* API remains functional for backward compatibility.
"""

from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_span() -> MagicMock:
    span = MagicMock()
    span.end = MagicMock()
    span.set_attribute = MagicMock()
    span.add_event = MagicMock()
    return span


def _make_mock_tracer(span: MagicMock) -> MagicMock:
    tracer = MagicMock()
    tracer.start_span = MagicMock(return_value=span)
    return tracer


def _make_mock_provider(tracer: MagicMock) -> MagicMock:
    provider = MagicMock()
    provider.get_tracer = MagicMock(return_value=tracer)
    provider.add_span_processor = MagicMock()
    provider.shutdown = MagicMock()
    return provider


def _build_manager_with_mocks():
    """Return a tuple of (mgr, provider, tracer, span, mock_trace, exit_stack).

    Patches remain **active** after the call.  The caller is responsible for
    calling ``exit_stack.close()`` when done (or using it as a context manager).

    This keeps the module-level ``trace`` name patched throughout each test so
    methods that reference ``orchestrator.monitoring.tracing.trace`` work
    correctly regardless of whether opentelemetry is installed in the test env.
    """
    mock_span = _make_mock_span()
    mock_tracer = _make_mock_tracer(mock_span)
    mock_provider_instance = _make_mock_provider(mock_tracer)
    MockTracerProvider = MagicMock(return_value=mock_provider_instance)
    mock_trace = MagicMock()
    mock_trace.set_span_in_context = MagicMock(return_value=MagicMock())

    stack = ExitStack()
    stack.enter_context(patch("orchestrator.monitoring.tracing.HAS_OTEL", True))
    stack.enter_context(patch("orchestrator.monitoring.tracing.TracerProvider", MockTracerProvider))
    stack.enter_context(patch("orchestrator.monitoring.tracing.BatchSpanProcessor", MagicMock()))
    stack.enter_context(
        patch(
            "orchestrator.monitoring.tracing.OTLPSpanExporter",
            MagicMock(side_effect=Exception("no endpoint")),
        )
    )
    stack.enter_context(patch("orchestrator.monitoring.tracing.trace", mock_trace))
    stack.enter_context(patch("orchestrator.__version__", "0.0.0-test", create=True))

    from orchestrator.monitoring.tracing import TracingManager

    mgr = TracingManager(endpoint="http://localhost:4317")

    return mgr, mock_provider_instance, mock_tracer, mock_span, mock_trace, stack


# ===========================================================================
# 1. span_context() — happy path
# ===========================================================================


class TestSpanContextHappyPath:
    """span_context() must end the span even on the success path."""

    def test_span_end_called_on_success(self):
        mgr, provider, tracer, span, mock_trace, stack = _build_manager_with_mocks()
        try:
            with mgr.span_context("test.op", {"k": "v"}) as s:
                assert s is span
                assert span.end.call_count == 0, "span.end() must not be called inside the with block"

            span.end.assert_called_once()
        finally:
            stack.close()

    def test_span_started_with_correct_name_and_attrs(self):
        mgr, provider, tracer, span, mock_trace, stack = _build_manager_with_mocks()
        try:
            with mgr.span_context("my.operation", {"attr1": "val1"}):
                pass

            tracer.start_span.assert_called_once()
            call_kwargs = tracer.start_span.call_args
            assert call_kwargs[0][0] == "my.operation"
            assert call_kwargs[1]["attributes"] == {"attr1": "val1"}
        finally:
            stack.close()

    def test_span_context_empty_attributes_defaults_to_empty_dict(self):
        mgr, provider, tracer, span, mock_trace, stack = _build_manager_with_mocks()
        try:
            with mgr.span_context("no.attrs"):
                pass

            call_kwargs = tracer.start_span.call_args
            assert call_kwargs[1]["attributes"] == {}
        finally:
            stack.close()


# ===========================================================================
# 2. span_context() — exception path
# ===========================================================================


class TestSpanContextExceptionPath:
    """span_context() must end the span even when an exception is raised."""

    def test_span_end_called_when_exception_raised(self):
        mgr, provider, tracer, span, mock_trace, stack = _build_manager_with_mocks()
        try:
            with pytest.raises(RuntimeError, match="pipeline failure"):
                with mgr.span_context("failing.op"):
                    raise RuntimeError("pipeline failure")

            span.end.assert_called_once()
        finally:
            stack.close()

    def test_exception_propagates_after_span_end(self):
        mgr, provider, tracer, span, mock_trace, stack = _build_manager_with_mocks()
        try:
            class _SentinelError(Exception):
                pass

            with pytest.raises(_SentinelError):
                with mgr.span_context("op"):
                    raise _SentinelError("sentinel")

            span.end.assert_called_once()
        finally:
            stack.close()

    def test_span_end_called_exactly_once_on_exception(self):
        """span.end() must be called exactly once regardless of exception type."""
        mgr, provider, tracer, span, mock_trace, stack = _build_manager_with_mocks()
        try:
            try:
                with mgr.span_context("op"):
                    raise ValueError("boom")
            except ValueError:
                pass

            assert span.end.call_count == 1
        finally:
            stack.close()


# ===========================================================================
# 3. span_context() — disabled path
# ===========================================================================


class TestSpanContextDisabled:
    """When tracing is disabled, span_context() must yield None without error."""

    def test_yields_none_when_disabled(self):
        with patch("orchestrator.monitoring.tracing.HAS_OTEL", False):
            from orchestrator.monitoring.tracing import TracingManager
            mgr = TracingManager()

        assert mgr.enabled is False

        # Even outside the patch block, enabled=False so no trace module access
        with mgr.span_context("op") as s:
            assert s is None

    def test_no_exception_when_disabled_and_exception_raised_in_body(self):
        with patch("orchestrator.monitoring.tracing.HAS_OTEL", False):
            from orchestrator.monitoring.tracing import TracingManager
            mgr = TracingManager()

        with pytest.raises(RuntimeError):
            with mgr.span_context("op"):
                raise RuntimeError("still propagates")


# ===========================================================================
# 4. No global trace.set_tracer_provider() side effect
# ===========================================================================


class TestNoGlobalTracerProvider:
    """TracingManager must NOT call trace.set_tracer_provider()."""

    def test_set_tracer_provider_never_called(self):
        mgr, provider, tracer, span, mock_trace, stack = _build_manager_with_mocks()
        try:
            mock_trace.set_tracer_provider.assert_not_called()
        finally:
            stack.close()

    def test_tracer_obtained_from_provider_not_global(self):
        """get_tracer() must be called on the provider instance, not on trace module."""
        mgr, provider, tracer, span, mock_trace, stack = _build_manager_with_mocks()
        try:
            # get_tracer must be called on the provider instance
            provider.get_tracer.assert_called_once()
            # get_tracer must NOT be called on the global trace module
            mock_trace.get_tracer.assert_not_called()
        finally:
            stack.close()


# ===========================================================================
# 5. _cleanup_orphaned_spans()
# ===========================================================================


class TestCleanupOrphanedSpans:
    """_cleanup_orphaned_spans() must end all open instance-level spans."""

    def test_all_open_spans_ended(self):
        mgr, provider, tracer, span, mock_trace, stack = _build_manager_with_mocks()
        try:
            run_span = _make_mock_span()
            step_span = _make_mock_span()
            task_span = _make_mock_span()

            mgr._run_span = run_span
            mgr._step_span = step_span
            mgr._task_span = task_span

            mgr._cleanup_orphaned_spans()

            run_span.end.assert_called_once()
            step_span.end.assert_called_once()
            task_span.end.assert_called_once()
        finally:
            stack.close()

    def test_span_references_cleared_after_cleanup(self):
        mgr, provider, tracer, span, mock_trace, stack = _build_manager_with_mocks()
        try:
            mgr._run_span = _make_mock_span()
            mgr._step_span = _make_mock_span()
            mgr._task_span = _make_mock_span()

            mgr._cleanup_orphaned_spans()

            assert mgr._run_span is None
            assert mgr._step_span is None
            assert mgr._task_span is None
        finally:
            stack.close()

    def test_no_error_when_all_spans_none(self):
        mgr, provider, tracer, span, mock_trace, stack = _build_manager_with_mocks()
        try:
            # All spans already None — must not raise
            mgr._cleanup_orphaned_spans()
        finally:
            stack.close()

    def test_cleanup_continues_if_one_span_end_raises(self):
        """If one span.end() raises, the other spans must still be cleaned up."""
        mgr, provider, tracer, span, mock_trace, stack = _build_manager_with_mocks()
        try:
            bad_span = _make_mock_span()
            bad_span.end.side_effect = RuntimeError("span already ended")

            good_span = _make_mock_span()

            mgr._task_span = bad_span
            mgr._step_span = None
            mgr._run_span = good_span

            # Must not raise
            mgr._cleanup_orphaned_spans()

            bad_span.end.assert_called_once()
            good_span.end.assert_called_once()
            assert mgr._task_span is None
            assert mgr._run_span is None
        finally:
            stack.close()

    def test_only_open_spans_have_end_called(self):
        """Only non-None spans must have end() called."""
        mgr, provider, tracer, span, mock_trace, stack = _build_manager_with_mocks()
        try:
            open_span = _make_mock_span()
            mgr._run_span = open_span
            # _step_span and _task_span remain None

            mgr._cleanup_orphaned_spans()

            open_span.end.assert_called_once()
        finally:
            stack.close()


# ===========================================================================
# 6. shutdown() calls _cleanup_orphaned_spans() then provider.shutdown()
# ===========================================================================


class TestShutdown:
    """shutdown() must clean up orphaned spans before calling provider.shutdown()."""

    def test_shutdown_calls_cleanup_before_provider_shutdown(self):
        mgr, provider, tracer, span, mock_trace, stack = _build_manager_with_mocks()
        try:
            call_order: list[str] = []
            mgr._cleanup_orphaned_spans = MagicMock(side_effect=lambda: call_order.append("cleanup"))
            provider.shutdown.side_effect = lambda: call_order.append("provider_shutdown")

            mgr.shutdown()

            assert call_order == ["cleanup", "provider_shutdown"]
        finally:
            stack.close()

    def test_shutdown_noop_when_disabled(self):
        with patch("orchestrator.monitoring.tracing.HAS_OTEL", False):
            from orchestrator.monitoring.tracing import TracingManager
            mgr = TracingManager()

        # Must not raise and must not access provider (it doesn't exist on disabled instance)
        mgr.shutdown()

    def test_shutdown_clears_orphaned_run_span(self):
        """Integration: shutdown() with an open _run_span must end it."""
        mgr, provider, tracer, span, mock_trace, stack = _build_manager_with_mocks()
        try:
            orphan = _make_mock_span()
            mgr._run_span = orphan

            mgr.shutdown()

            orphan.end.assert_called_once()
            assert mgr._run_span is None
        finally:
            stack.close()

    def test_shutdown_clears_orphaned_step_span(self):
        """Integration: shutdown() with an open _step_span must end it."""
        mgr, provider, tracer, span, mock_trace, stack = _build_manager_with_mocks()
        try:
            orphan = _make_mock_span()
            mgr._step_span = orphan

            mgr.shutdown()

            orphan.end.assert_called_once()
            assert mgr._step_span is None
        finally:
            stack.close()


# ===========================================================================
# 7. Backward-compatible start_*/end_* API
# ===========================================================================


class TestBackwardCompatibleAPI:
    """The legacy start_*/end_* methods must remain functional."""

    def test_start_end_run_span(self):
        mgr, provider, tracer, span, mock_trace, stack = _build_manager_with_mocks()
        try:
            mgr.start_run_span("run-1", "full", "add feature X")
            assert mgr._run_span is span

            mgr.end_run_span(success=True, total_cost_usd=0.5)
            span.end.assert_called_once()
            assert mgr._run_span is None
        finally:
            stack.close()

    def test_start_end_step_span(self):
        mgr, provider, tracer, span, mock_trace, stack = _build_manager_with_mocks()
        try:
            mgr.start_step_span("pm", "pm_role", "premium")
            assert mgr._step_span is span

            mgr.end_step_span(success=True, cost_usd=0.1, duration_s=2.5)
            span.end.assert_called_once()
            assert mgr._step_span is None
        finally:
            stack.close()

    def test_start_end_agent_span(self):
        mgr, provider, tracer, span, mock_trace, stack = _build_manager_with_mocks()
        try:
            returned_span = mgr.start_agent_span("pm_agent", "claude-3-opus", 1)
            assert returned_span is span

            mgr.end_agent_span(span, success=True, cost_usd=0.05)
            span.end.assert_called_once()
        finally:
            stack.close()

    def test_end_run_span_noop_when_no_active_span(self):
        mgr, provider, tracer, span, mock_trace, stack = _build_manager_with_mocks()
        try:
            # _run_span is None by default — must not raise
            mgr.end_run_span(success=True, total_cost_usd=0.0)
        finally:
            stack.close()

    def test_end_step_span_noop_when_no_active_span(self):
        mgr, provider, tracer, span, mock_trace, stack = _build_manager_with_mocks()
        try:
            mgr.end_step_span(success=True, cost_usd=0.0, duration_s=0.0)
        finally:
            stack.close()

    def test_all_methods_noop_when_disabled(self):
        with patch("orchestrator.monitoring.tracing.HAS_OTEL", False):
            from orchestrator.monitoring.tracing import TracingManager
            mgr = TracingManager()

        mgr.start_run_span("r", "full", "feat")
        mgr.end_run_span(True, 0.0)
        mgr.start_step_span("pm", "role", "tier")
        mgr.end_step_span(True, 0.0, 0.0)
        result = mgr.start_agent_span("a", "m", 1)
        assert result is None
        mgr.end_agent_span(None, True, 0.0)
        mgr.record_escalation("a", "cheap", "expensive")

    def test_record_escalation_adds_event_to_step_span(self):
        mgr, provider, tracer, span, mock_trace, stack = _build_manager_with_mocks()
        try:
            mgr._step_span = span
            mgr.record_escalation("agent_x", "claude-haiku", "claude-opus")

            span.add_event.assert_called_once_with(
                "model_escalation",
                attributes={"agent": "agent_x", "from_model": "claude-haiku", "to_model": "claude-opus"},
            )
        finally:
            stack.close()

    def test_end_agent_span_sets_error_code_when_provided(self):
        mgr, provider, tracer, span, mock_trace, stack = _build_manager_with_mocks()
        try:
            mgr.end_agent_span(span, success=False, cost_usd=0.01, error_code="TIMEOUT")
            span.set_attribute.assert_any_call("error_code", "TIMEOUT")
        finally:
            stack.close()

    def test_end_agent_span_skips_error_code_when_none(self):
        mgr, provider, tracer, span, mock_trace, stack = _build_manager_with_mocks()
        try:
            mgr.end_agent_span(span, success=True, cost_usd=0.01, error_code=None)
            # error_code attribute must NOT be set
            set_calls = [c.args[0] for c in span.set_attribute.call_args_list]
            assert "error_code" not in set_calls
        finally:
            stack.close()


# ===========================================================================
# 8. MonitoringStack.shutdown() — tracing.shutdown() wrapped in try/except
# ===========================================================================


class TestMonitoringStackTracingShutdown:
    """MonitoringStack.shutdown() must call tracing.shutdown() with error isolation."""

    def _make_config(self, tracing_enabled: bool = False):
        from orchestrator.monitoring.config import MonitoringConfig
        return MonitoringConfig(tracing_enabled=tracing_enabled)

    def test_shutdown_calls_tracing_shutdown_when_present(self):
        """MonitoringStack.shutdown() must call self._tracing.shutdown()."""
        from orchestrator.monitoring import MonitoringStack

        config = self._make_config()
        stack = MonitoringStack(config=config, run_id="run-1", workspace=Path("/tmp"))

        mock_tracing = MagicMock()
        stack._tracing = mock_tracing

        stack.shutdown()

        mock_tracing.shutdown.assert_called_once()

    def test_shutdown_does_not_propagate_tracing_exception(self):
        """If tracing.shutdown() raises, MonitoringStack.shutdown() must NOT propagate it."""
        from orchestrator.monitoring import MonitoringStack

        config = self._make_config()
        stack = MonitoringStack(config=config, run_id="run-2", workspace=Path("/tmp"))

        mock_tracing = MagicMock()
        mock_tracing.shutdown.side_effect = RuntimeError("OTel provider already shut down")
        stack._tracing = mock_tracing

        # Must not raise
        stack.shutdown()

        mock_tracing.shutdown.assert_called_once()

    def test_shutdown_skips_tracing_when_none(self):
        """If tracing is not configured, shutdown() must not fail."""
        from orchestrator.monitoring import MonitoringStack

        config = self._make_config()
        stack = MonitoringStack(config=config, run_id="run-3", workspace=Path("/tmp"))
        stack._tracing = None

        # Must not raise
        stack.shutdown()

    def test_shutdown_isolates_metrics_and_tracing_failures_independently(self):
        """Both metrics and tracing shutdown failures must be swallowed independently."""
        from orchestrator.monitoring import MonitoringStack

        config = self._make_config()
        stack = MonitoringStack(config=config, run_id="run-4", workspace=Path("/tmp"))

        mock_metrics = MagicMock()
        mock_metrics.shutdown.side_effect = OSError("metrics failed")
        stack._metrics = mock_metrics

        mock_tracing = MagicMock()
        mock_tracing.shutdown.side_effect = RuntimeError("tracing failed")
        stack._tracing = mock_tracing

        # Both failures must be swallowed
        stack.shutdown()

        mock_metrics.shutdown.assert_called_once()
        mock_tracing.shutdown.assert_called_once()
