"""Monitoring stack facade — single entry point for all observability subsystems."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from orchestrator.monitoring.config import MonitoringConfig
from orchestrator.monitoring.errors import ErrorCode
from orchestrator.monitoring.health import get_health_state

logger = logging.getLogger(__name__)


class MonitoringStack:
    """Facade that initializes and delegates to enabled monitoring subsystems.

    Each on_* method fans out to the subsystems that are active.
    Missing dependencies are handled gracefully — the subsystem is simply skipped.
    """

    def __init__(
        self,
        config: MonitoringConfig,
        run_id: str,
        workspace: Path,
        max_budget_usd: float = 0.0,
        total_steps: int = 0,
    ) -> None:
        self._config = config
        self._run_id = run_id
        self._workspace = workspace

        self._metrics: Any = None
        self._tracing: Any = None
        self._alerting: Any = None
        self._budget: Any = None
        self._timeline: Any = None

        self._agent_spans: dict[str, Any] = {}  # key: f"{agent}:{attempt}"

        # --- Metrics ---
        if config.metrics_enabled:
            try:
                from orchestrator.monitoring.metrics import MetricsManager
                self._metrics = MetricsManager(port=config.metrics_port)
            except Exception as e:
                logger.warning(f"Failed to start metrics: {e}")

        # --- Tracing ---
        if config.tracing_enabled:
            try:
                from orchestrator.monitoring.tracing import TracingManager
                self._tracing = TracingManager(
                    endpoint=config.tracing_endpoint,
                    console_export=config.tracing_console,
                )
            except Exception as e:
                logger.warning(f"Failed to start tracing: {e}")

        # --- Alerting ---
        if config.webhooks:
            from orchestrator.monitoring.alerting import AlertManager
            self._alerting = AlertManager(
                webhooks=config.webhooks,
                workspace=workspace,
            )

        # --- Budget forecasting ---
        if max_budget_usd > 0:
            from orchestrator.monitoring.budget import BudgetForecaster
            self._budget = BudgetForecaster(
                max_budget_usd=max_budget_usd,
                total_steps=total_steps,
            )

        # --- Timeline ---
        from orchestrator.monitoring.timeline import TimelineRecorder
        self._timeline = TimelineRecorder()

        # --- Health state ---
        self._health = get_health_state()
        self._health.update(
            run_id=run_id,
            budget_remaining_usd=max_budget_usd,
        )

    # --- Properties for external access ---

    @property
    def budget_forecaster(self) -> Any:
        return self._budget

    @property
    def timeline(self) -> Any:
        return self._timeline

    # --- Event handlers ---

    def on_run_start(
        self,
        workflow_type: str,
        feature_request: str,
    ) -> None:
        if self._tracing:
            self._tracing.start_run_span(self._run_id, workflow_type, feature_request)
        if self._timeline:
            self._timeline.set_run_start()

    def on_run_complete(
        self,
        success: bool,
        total_cost_usd: float,
        workflow_type: str,
    ) -> None:
        status = "completed" if success else "failed"

        if self._metrics:
            self._metrics.record_run_complete(workflow_type, status)
        if self._tracing:
            self._tracing.end_run_span(success, total_cost_usd)
        if self._timeline:
            self._timeline.set_run_end()
            self._timeline.export_json(self._workspace / "timeline.json")
            self._timeline.export_html(self._workspace / "timeline.html")
        if self._alerting:
            if self._config.alerts.on_completion or not success:
                self._alerting.send_alert("run_complete", {
                    "run_id": self._run_id,
                    "workflow_type": workflow_type,
                    "status": status,
                    "total_cost_usd": total_cost_usd,
                })
        self._health.update(current_step=None)

    def on_step_start(
        self,
        step_name: str,
        role: str = "",
        model_tier: str = "",
    ) -> None:
        if self._tracing:
            self._tracing.start_step_span(step_name, role, model_tier)
        self._health.update(current_step=step_name)

    def on_step_end(
        self,
        step_name: str,
        success: bool,
        cost_usd: float,
        duration_s: float,
        model_tier: str = "",
    ) -> None:
        if self._metrics:
            self._metrics.record_phase_duration(step_name, model_tier, duration_s)
        if self._tracing:
            self._tracing.end_step_span(success, cost_usd, duration_s)
        if self._budget:
            self._budget.record_cost(step_name, cost_usd)
        if not success and self._alerting and self._config.alerts.on_failure:
            self._alerting.send_alert("phase_failed", {
                "run_id": self._run_id,
                "step": step_name,
                "cost_usd": cost_usd,
            })

    def on_agent_invoke(
        self,
        agent: str,
        model: str,
        attempt: int,
    ) -> None:
        span_key = f"{agent}:{attempt}"
        if self._tracing:
            span = self._tracing.start_agent_span(agent, model, attempt)
            if span:
                self._agent_spans[span_key] = span
        if self._metrics:
            self._metrics.inc_active_agents()

    def on_agent_result(
        self,
        agent: str,
        model: str,
        success: bool,
        cost_usd: float,
        attempt: int,
        error_code: str | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> None:
        status = "success" if success else "failure"
        span_key = f"{agent}:{attempt}"

        if self._metrics:
            self._metrics.record_agent_invocation(agent, model, status)
            self._metrics.record_agent_cost(agent, model, cost_usd)
            self._metrics.record_agent_tokens(agent, model, input_tokens, output_tokens)
            self._metrics.dec_active_agents()
            if not success and error_code:
                self._metrics.record_error(agent, error_code)

        if self._tracing:
            span = self._agent_spans.pop(span_key, None)
            if span:
                self._tracing.end_agent_span(span, success, cost_usd, error_code)

    def on_task_invoke(
        self,
        task_id: str,
        step: str,
        agent: str,
        model: str,
        attempt: int,
        dependencies: list[str] | None = None,
    ) -> None:
        if self._timeline:
            self._timeline.record_start(
                task_id=task_id,
                agent=agent,
                step=step,
                dependencies=dependencies,
                model_tier=model,
            )

    def on_task_result(
        self,
        task_id: str,
        success: bool,
        cost_usd: float,
        step: str = "",
        role: str = "",
        model_tier: str = "",
        duration_s: float = 0.0,
        error_code: str | None = None,
    ) -> None:
        status = "completed" if success else "failed"
        if self._timeline:
            self._timeline.record_end(task_id, status, cost_usd, error_code)
        if self._metrics and duration_s > 0:
            self._metrics.record_task_duration(step, role, model_tier, duration_s)

    def on_budget_warning(
        self,
        cumulative_cost_usd: float,
        max_budget_usd: float,
    ) -> None:
        ratio = cumulative_cost_usd / max_budget_usd if max_budget_usd > 0 else 0.0
        if self._metrics:
            self._metrics.update_budget_utilization(ratio)
        self._health.update(budget_remaining_usd=max(0, max_budget_usd - cumulative_cost_usd))
        if self._alerting and self._config.alerts.on_budget_warning:
            event = "budget_exceeded" if ratio >= 1.0 else "budget_warning"
            self._alerting.send_alert(event, {
                "run_id": self._run_id,
                "cumulative_cost_usd": cumulative_cost_usd,
                "max_budget_usd": max_budget_usd,
                "utilization_pct": round(ratio * 100, 1),
            })

    def on_model_escalation(
        self,
        agent: str,
        from_model: str,
        to_model: str,
    ) -> None:
        if self._metrics:
            self._metrics.record_model_escalation()
        if self._tracing:
            self._tracing.record_escalation(agent, from_model, to_model)

    def on_retry(self, agent: str) -> None:
        if self._metrics:
            self._metrics.record_retry(agent)

    def on_error(
        self,
        agent: str,
        error_code: str,
        message: str,
    ) -> None:
        if self._metrics:
            self._metrics.record_error(agent, error_code)

    def shutdown(self) -> None:
        if self._metrics:
            try:
                self._metrics.shutdown()
            except Exception as exc:
                logger.warning(f"Error during metrics shutdown: {exc}")
        if self._alerting:
            self._alerting.shutdown()
        if self._tracing:
            try:
                self._tracing.shutdown()
            except Exception as exc:
                logger.warning(f"Error during tracing shutdown: {exc}")
