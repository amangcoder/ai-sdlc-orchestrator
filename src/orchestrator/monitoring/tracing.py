"""OpenTelemetry distributed tracing integration."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Generator

logger = logging.getLogger(__name__)

try:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import (
        BatchSpanProcessor,
        ConsoleSpanExporter,
    )
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

    HAS_OTEL = True
except ImportError:
    HAS_OTEL = False
    trace = None  # type: ignore[assignment]
    TracerProvider = None  # type: ignore[misc, assignment]
    BatchSpanProcessor = None  # type: ignore[misc, assignment]
    ConsoleSpanExporter = None  # type: ignore[misc, assignment]
    OTLPSpanExporter = None  # type: ignore[misc, assignment]


class TracingManager:
    """Manages OpenTelemetry spans for the orchestrator pipeline."""

    def __init__(
        self,
        endpoint: str = "http://localhost:4317",
        console_export: bool = False,
    ) -> None:
        if not HAS_OTEL:
            logger.warning("opentelemetry not installed — tracing disabled")
            self._enabled = False
            return

        self._enabled = True
        # Use a dedicated TracerProvider instance rather than overwriting the
        # global OTel provider.  Calling trace.set_tracer_provider() is a
        # process-wide side effect that breaks any other library (or test) that
        # relies on the global provider.  Getting the tracer directly from our
        # own provider instance is both safer and easier to reason about.
        self._provider = TracerProvider()

        # OTLP exporter
        try:
            otlp_exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
            self._provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
        except Exception as e:
            logger.warning(f"Could not connect OTLP exporter to {endpoint}: {e}")

        if console_export:
            self._provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))

        # Obtain tracer directly from our dedicated provider — no global side effect.
        from orchestrator import __version__
        self._tracer = self._provider.get_tracer("orchestrator", __version__)

        # Active span references for nesting (used by the legacy start_*/end_* API)
        self._run_span: Any = None
        self._step_span: Any = None
        self._task_span: Any = None
        # run_id stored so it can be propagated to child spans
        self._run_id: str = ""

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def current_trace_id(self) -> str:
        """Return the hex trace_id for the most-current active span, or empty string.

        Checks spans in priority order: task → step → run.
        Returns a 32-character lowercase hex string when OTel is enabled and
        a valid span is active; otherwise returns ``""``.
        """
        if not self._enabled:
            return ""
        span = (
            getattr(self, "_task_span", None)
            or getattr(self, "_step_span", None)
            or getattr(self, "_run_span", None)
        )
        if span is None:
            return ""
        try:
            ctx = span.get_span_context()
            if ctx and ctx.is_valid:
                return format(ctx.trace_id, "032x")
        except Exception:  # noqa: BLE001 — defensive: span API may not be available
            pass
        return ""

    @contextmanager
    def span_context(
        self,
        name: str,
        attributes: dict | None = None,
    ) -> Generator[Any, None, None]:
        """Context manager that guarantees ``span.end()`` on both success and
        exception paths.

        This prevents open spans from accumulating in the OTel
        ``BatchSpanProcessor`` queue when an exception escapes mid-pipeline.

        Usage::

            with tracing.span_context("my.operation", {"key": "value"}) as span:
                do_work()
                span.set_attribute("result", "ok")

        When tracing is disabled the context manager yields ``None`` and is
        otherwise a no-op.
        """
        if not self._enabled:
            yield None
            return

        parent = self._step_span or self._run_span
        ctx = trace.set_span_in_context(parent) if parent else None
        span = self._tracer.start_span(
            name,
            context=ctx,
            attributes=attributes or {},
        )
        try:
            yield span
        finally:
            span.end()

    def _cleanup_orphaned_spans(self) -> None:
        """End any spans that were started but never explicitly ended.

        Should be called from ``shutdown()`` to drain the
        ``BatchSpanProcessor`` queue and prevent memory / resource leaks when
        an exception escaped between a ``start_*_span()`` / ``end_*_span()``
        pair.
        """
        for attr in ("_task_span", "_step_span", "_run_span"):
            span = getattr(self, attr, None)
            if span is not None:
                try:
                    span.end()
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Error ending orphaned span %s: %s", attr, exc)
                setattr(self, attr, None)

    def start_run_span(self, run_id: str, workflow_type: str, feature_request: str) -> None:
        if not self._enabled:
            return
        self._run_id = run_id  # propagate to child spans
        self._run_span = self._tracer.start_span(
            "orchestrator.run",
            attributes={
                "run_id": run_id,
                "workflow_type": workflow_type,
                "feature_request": feature_request[:200],
            },
        )

    def end_run_span(self, success: bool, total_cost_usd: float) -> None:
        if not self._enabled or not self._run_span:
            return
        self._run_span.set_attribute("success", success)
        self._run_span.set_attribute("total_cost_usd", total_cost_usd)
        self._run_span.end()
        self._run_span = None

    def start_step_span(self, step_name: str, role: str, model_tier: str) -> None:
        if not self._enabled:
            return
        ctx = trace.set_span_in_context(self._run_span) if self._run_span else None
        attrs: dict = {
            "step": step_name,
            "role": role,
            "model_tier": model_tier,
        }
        if self._run_id:
            attrs["run_id"] = self._run_id
        self._step_span = self._tracer.start_span(
            f"orchestrator.step.{step_name}",
            context=ctx,
            attributes=attrs,
        )

    def end_step_span(self, success: bool, cost_usd: float, duration_s: float) -> None:
        if not self._enabled or not self._step_span:
            return
        self._step_span.set_attribute("success", success)
        self._step_span.set_attribute("cost_usd", cost_usd)
        self._step_span.set_attribute("duration_seconds", duration_s)
        self._step_span.end()
        self._step_span = None

    def start_agent_span(
        self,
        agent_name: str,
        model: str,
        attempt: int,
    ) -> Any:
        if not self._enabled:
            return None
        parent = self._step_span or self._run_span
        ctx = trace.set_span_in_context(parent) if parent else None
        attrs: dict = {
            "agent": agent_name,
            "model": model,
            "attempt": attempt,
        }
        if self._run_id:
            attrs["run_id"] = self._run_id
        span = self._tracer.start_span(
            f"orchestrator.agent.{agent_name}",
            context=ctx,
            attributes=attrs,
        )
        return span

    def end_agent_span(
        self,
        span: Any,
        success: bool,
        cost_usd: float,
        error_code: str | None = None,
    ) -> None:
        if not self._enabled or span is None:
            return
        span.set_attribute("success", success)
        span.set_attribute("cost_usd", cost_usd)
        if error_code:
            span.set_attribute("error_code", error_code)
        span.end()

    def record_escalation(self, agent: str, from_model: str, to_model: str) -> None:
        if not self._enabled:
            return
        parent = self._step_span or self._run_span
        if parent:
            parent.add_event(
                "model_escalation",
                attributes={"agent": agent, "from_model": from_model, "to_model": to_model},
            )

    def shutdown(self) -> None:
        if self._enabled:
            self._cleanup_orphaned_spans()
            self._provider.shutdown()
