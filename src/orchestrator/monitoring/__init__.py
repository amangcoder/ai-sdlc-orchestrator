"""Monitoring stack facade — single entry point for all observability subsystems."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from orchestrator.monitoring.config import MonitoringConfig
from orchestrator.monitoring.errors import ErrorCode
from orchestrator.monitoring.health import get_health_state

if TYPE_CHECKING:
    from orchestrator.monitoring.loki import LokiLogShipper
    from orchestrator.monitoring.slo import SLOTracker

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
        self._loki_shipper: Optional[LokiLogShipper] = None
        self._slo_tracker: Optional[SLOTracker] = None

        self._agent_spans: dict[str, Any] = {}  # key: f"{agent}:{attempt}"
        self._workflow_type: str = ""  # stored from on_run_start for burn-rate labeling

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

        # --- Loki log shipping ---
        if config.loki_enabled:
            try:
                from orchestrator.monitoring.loki import LokiLogShipper as _LokiLogShipper
                self._loki_shipper = _LokiLogShipper(
                    config.loki_endpoint,
                    auth_token=config.loki_auth_token,
                )
            except Exception as e:
                logger.warning(f"Failed to start Loki log shipper: {e}")

        # --- SLO tracking ---
        if config.slo.enabled:
            try:
                from orchestrator.monitoring.slo import SLOTracker as _SLOTracker
                self._slo_tracker = _SLOTracker(
                    config.slo,
                    prometheus_url=config.prometheus_url,
                )
            except Exception as e:
                logger.warning(f"Failed to start SLO tracker: {e}")

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

    @property
    def slo_tracker(self) -> "Optional[SLOTracker]":
        """Return the SLOTracker instance, or None if SLO tracking is disabled."""
        return self._slo_tracker

    @property
    def current_trace_id(self) -> str:
        """Return the current OTel trace_id hex string, or empty string if unavailable."""
        if self._tracing is None:
            return ""
        try:
            return self._tracing.current_trace_id  # type: ignore[no-any-return]
        except Exception:  # noqa: BLE001
            return ""

    # --- Loki event forwarding ---

    def on_log_event(self, event: dict) -> None:
        """Forward an enriched log event to the Loki log shipper (if enabled).

        Called by RunLogger._dispatch_to_monitoring() for every pipeline event.
        The *event* dict must already contain the enrichment fields (trace_id,
        task_id, agent, level) added by the RunLogger.
        """
        if self._loki_shipper is not None:
            try:
                self._loki_shipper.push(event)
            except Exception as exc:  # noqa: BLE001
                logger.debug("Loki push failed: %s", exc)

    # --- Event handlers ---

    def on_run_start(
        self,
        workflow_type: str,
        feature_request: str,
    ) -> None:
        self._workflow_type = workflow_type
        if self._tracing:
            self._tracing.start_run_span(self._run_id, workflow_type, feature_request)
        if self._timeline:
            self._timeline.set_run_start()

    def on_run_complete(
        self,
        success: bool,
        total_cost_usd: float,
        workflow_type: str,
        duration_s: float = 0.0,
        errors: int = 0,
    ) -> None:
        status = "success" if success else "failed"

        if self._metrics:
            self._metrics.record_run_complete(workflow_type, status)
            if duration_s > 0:
                self._metrics.record_run_duration(workflow_type, status, duration_s)
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
        if self._slo_tracker is not None:
            self._slo_tracker.record_run(
                success=success,
                cost_usd=total_cost_usd,
                duration_s=duration_s,
                errors=errors,
            )
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
            if duration_s > 0 and self._workflow_type:
                burn_rate = cost_usd / duration_s * 60.0
                self._metrics.record_burn_rate(self._workflow_type, burn_rate)
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

    def on_phase_end(
        self,
        phase_name: str,
        success: bool,
        cost_usd: float,
        duration_s: float,
        artifact_valid: bool | None = None,
        model_tier: str = "",
    ) -> None:
        """Record phase completion with optional artifact validation outcome for SLO tracking.

        Args:
            phase_name:     Identifier for the phase (e.g. ``"pm"``, ``"architect"``).
            success:        Whether the phase completed successfully.
            cost_usd:       Cost incurred during this phase.
            duration_s:     Wall-clock duration in seconds.
            artifact_valid: ``True``/``False`` if an artifact was written and validated;
                            ``None`` if no artifact was produced by this phase.
            model_tier:     Model tier used (e.g. ``"sonnet"``, ``"opus"``).
        """
        # Record Prometheus phase duration metric so orchestrator_phase_duration_seconds
        # gets populated from real pipeline phase_complete events.
        if self._metrics and duration_s > 0:
            self._metrics.record_phase_duration(phase_name, model_tier or "unknown", duration_s)
            if cost_usd > 0 and self._workflow_type:
                burn_rate = cost_usd / max(duration_s, 1e-6) * 60.0
                self._metrics.record_burn_rate(self._workflow_type, burn_rate)

        if self._slo_tracker is not None:
            self._slo_tracker.record_phase_result(phase_name, duration_s, success)
            if artifact_valid is not None:
                self._slo_tracker.record_artifact_validation(artifact_valid)

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

    def on_artifact_produced(
        self,
        artifact_type: str,
        agent: str,
    ) -> None:
        """Record that an artifact was produced by an agent."""
        if self._metrics:
            self._metrics.record_artifact_produced(artifact_type, agent)

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
        if self._loki_shipper is not None:
            try:
                self._loki_shipper.shutdown()
            except Exception as exc:
                logger.warning(f"Error during Loki shipper shutdown: {exc}")
        if self._slo_tracker is not None:
            try:
                # Flush final SLO state to log before shutdown
                report = self._slo_tracker.evaluate_slos()
                logger.info(
                    "SLO final state at shutdown: all_passing=%s slis=%d",
                    report.all_passing,
                    len(report.slis),
                )
            except Exception as exc:
                logger.warning(f"Error flushing SLO state at shutdown: {exc}")
