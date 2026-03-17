"""Prometheus metrics collection with background HTTP server."""

from __future__ import annotations

import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)

try:
    from prometheus_client import (
        CollectorRegistry,
        Counter,
        Gauge,
        Histogram,
        start_http_server,
    )
    HAS_PROMETHEUS = True
except ImportError:
    HAS_PROMETHEUS = False


class MetricsManager:
    """Collects Prometheus metrics and serves them on a background HTTP server."""

    def __init__(self, port: int = 9090) -> None:
        if not HAS_PROMETHEUS:
            logger.warning("prometheus_client not installed — metrics disabled")
            self._enabled = False
            self._http_server: Any = None
            return

        self._enabled = True
        self._registry = CollectorRegistry()

        # --- Counters ---
        self.run_total = Counter(
            "orchestrator_run_total",
            "Total orchestrator runs",
            ["workflow_type", "status"],
            registry=self._registry,
        )
        self.agent_invocations_total = Counter(
            "orchestrator_agent_invocations_total",
            "Total agent invocations",
            ["agent", "model", "status"],
            registry=self._registry,
        )
        self.agent_cost_usd = Counter(
            "orchestrator_agent_cost_usd",
            "Cumulative agent cost in USD",
            ["agent", "model"],
            registry=self._registry,
        )
        self.model_escalations_total = Counter(
            "orchestrator_model_escalations_total",
            "Total model escalations",
            registry=self._registry,
        )
        self.retry_total = Counter(
            "orchestrator_retry_total",
            "Total agent retries",
            ["agent"],
            registry=self._registry,
        )
        self.errors_total = Counter(
            "orchestrator_errors_total",
            "Total errors by agent and type",
            ["agent", "error_type"],
            registry=self._registry,
        )

        self.agent_input_tokens_total = Counter(
            "orchestrator_agent_input_tokens_total",
            "Cumulative input tokens consumed",
            ["agent", "model"],
            registry=self._registry,
        )
        self.agent_output_tokens_total = Counter(
            "orchestrator_agent_output_tokens_total",
            "Cumulative output tokens consumed",
            ["agent", "model"],
            registry=self._registry,
        )

        # --- Histograms ---
        self.phase_duration_seconds = Histogram(
            "orchestrator_phase_duration_seconds",
            "Phase duration in seconds",
            ["phase", "model_tier"],
            registry=self._registry,
        )
        self.task_duration_seconds = Histogram(
            "orchestrator_task_duration_seconds",
            "Task duration in seconds",
            ["step", "role", "model_tier"],
            registry=self._registry,
        )

        # --- Gauges ---
        self.budget_utilization_ratio = Gauge(
            "orchestrator_budget_utilization_ratio",
            "Current budget utilization (0.0 to 1.0+)",
            registry=self._registry,
        )
        self.active_agents = Gauge(
            "orchestrator_active_agents",
            "Number of currently running agents",
            registry=self._registry,
        )

        # Start background HTTP server
        self._http_server: Any = None
        try:
            result = start_http_server(port, registry=self._registry)
            # prometheus_client >= 0.8 returns (HTTPServer, thread); older returns None
            if result is not None:
                self._http_server = result[0]
            logger.info(f"Prometheus metrics server started on :{port}")
        except OSError as e:
            logger.warning(f"Could not start metrics server on :{port}: {e}")

    @property
    def enabled(self) -> bool:
        return self._enabled

    def record_run_complete(self, workflow_type: str, status: str) -> None:
        if not self._enabled:
            return
        self.run_total.labels(workflow_type=workflow_type, status=status).inc()

    def record_agent_invocation(self, agent: str, model: str, status: str) -> None:
        if not self._enabled:
            return
        self.agent_invocations_total.labels(agent=agent, model=model, status=status).inc()

    def record_agent_cost(self, agent: str, model: str, cost_usd: float) -> None:
        if not self._enabled:
            return
        self.agent_cost_usd.labels(agent=agent, model=model).inc(cost_usd)

    def record_agent_tokens(self, agent: str, model: str, input_tokens: int, output_tokens: int) -> None:
        if not self._enabled:
            return
        if input_tokens > 0:
            self.agent_input_tokens_total.labels(agent=agent, model=model).inc(input_tokens)
        if output_tokens > 0:
            self.agent_output_tokens_total.labels(agent=agent, model=model).inc(output_tokens)

    def record_phase_duration(self, phase: str, model_tier: str, duration_s: float) -> None:
        if not self._enabled:
            return
        self.phase_duration_seconds.labels(phase=phase, model_tier=model_tier).observe(duration_s)

    def record_task_duration(self, step: str, role: str, model_tier: str, duration_s: float) -> None:
        if not self._enabled:
            return
        self.task_duration_seconds.labels(step=step, role=role, model_tier=model_tier).observe(duration_s)

    def record_model_escalation(self) -> None:
        if not self._enabled:
            return
        self.model_escalations_total.inc()

    def record_retry(self, agent: str) -> None:
        if not self._enabled:
            return
        self.retry_total.labels(agent=agent).inc()

    def record_error(self, agent: str, error_type: str) -> None:
        if not self._enabled:
            return
        self.errors_total.labels(agent=agent, error_type=error_type).inc()

    def update_budget_utilization(self, ratio: float) -> None:
        if not self._enabled:
            return
        self.budget_utilization_ratio.set(ratio)

    def inc_active_agents(self) -> None:
        if not self._enabled:
            return
        self.active_agents.inc()

    def dec_active_agents(self) -> None:
        if not self._enabled:
            return
        self.active_agents.dec()

    def shutdown(self) -> None:
        """Stop the Prometheus HTTP server and release the port.

        Safe to call multiple times. When *enabled* is ``False`` or the server
        was never started (e.g. the port was already in use at startup) this is
        a no-op.
        """
        if not self._enabled or self._http_server is None:
            return
        try:
            self._http_server.shutdown()
            self._http_server.server_close()
            logger.info("Prometheus metrics server stopped")
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning(f"Error stopping Prometheus metrics server: {exc}")
        finally:
            self._http_server = None
