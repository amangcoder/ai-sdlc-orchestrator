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
        self._provider = TracerProvider()

        # OTLP exporter
        try:
            otlp_exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
            self._provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
        except Exception as e:
            logger.warning(f"Could not connect OTLP exporter to {endpoint}: {e}")

        if console_export:
            self._provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))

        trace.set_tracer_provider(self._provider)
        self._tracer = trace.get_tracer("orchestrator", "0.1.0")

        # Active span references for nesting
        self._run_span: Any = None
        self._step_span: Any = None
        self._task_span: Any = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    def start_run_span(self, run_id: str, workflow_type: str, feature_request: str) -> None:
        if not self._enabled:
            return
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
        self._step_span = self._tracer.start_span(
            f"orchestrator.step.{step_name}",
            context=ctx,
            attributes={
                "step": step_name,
                "role": role,
                "model_tier": model_tier,
            },
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
        span = self._tracer.start_span(
            f"orchestrator.agent.{agent_name}",
            context=ctx,
            attributes={
                "agent": agent_name,
                "model": model,
                "attempt": attempt,
            },
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
            self._provider.shutdown()
