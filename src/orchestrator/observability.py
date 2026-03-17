"""Observability: JSONL run logging with structlog + monitoring stack integration."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from orchestrator.monitoring import MonitoringStack


class RunLogger:
    def __init__(self, log_dir: Path, run_id: str) -> None:
        self.run_id = run_id
        log_dir.mkdir(parents=True, exist_ok=True)
        self._log_path = log_dir / f"run-{run_id}.jsonl"
        self._log_path.touch()
        self._cumulative_cost: float = 0.0
        self._cumulative_input_tokens: int = 0
        self._cumulative_output_tokens: int = 0
        self._lock = threading.Lock()
        self._log = structlog.get_logger(__name__)
        self._monitoring: MonitoringStack | None = None

    def set_monitoring_stack(self, stack: MonitoringStack) -> None:
        """Attach a MonitoringStack to receive all events."""
        self._monitoring = stack

    @property
    def cumulative_input_tokens(self) -> int:
        with self._lock:
            return self._cumulative_input_tokens

    @property
    def cumulative_output_tokens(self) -> int:
        with self._lock:
            return self._cumulative_output_tokens

    @property
    def cumulative_total_tokens(self) -> int:
        with self._lock:
            return self._cumulative_input_tokens + self._cumulative_output_tokens

    def log_event(self, event_type: str, data: dict) -> None:
        with self._lock:
            if event_type == "agent_result":
                self._cumulative_cost += data.get("cost_usd", 0.0)
                self._cumulative_input_tokens += data.get("input_tokens", 0)
                self._cumulative_output_tokens += data.get("output_tokens", 0)
            record = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "run_id": self.run_id,
                "event": event_type,
                **data,
            }
            with self._log_path.open("a") as f:
                f.write(json.dumps(record, default=str) + "\n")
        self._log.info(event_type, **data)

        # Delegate to monitoring stack
        if self._monitoring:
            self._dispatch_to_monitoring(event_type, data)

    def _dispatch_to_monitoring(self, event_type: str, data: dict) -> None:
        """Route events to the appropriate MonitoringStack methods."""
        m = self._monitoring
        if not m:
            return

        try:
            if event_type == "run_start":
                m.on_run_start(
                    workflow_type=data.get("workflow_type", ""),
                    feature_request=data.get("feature_request", ""),
                )
            elif event_type == "run_complete":
                phases = data.get("phases", {})
                has_failed = any(
                    p.get("status") == "failed"
                    for p in phases.values()
                    if isinstance(p, dict)
                )
                m.on_run_complete(
                    success=not has_failed,
                    total_cost_usd=data.get("total_cost_usd", 0.0),
                    workflow_type=data.get("workflow_type", ""),
                )
            elif event_type == "agent_invoke":
                m.on_agent_invoke(
                    agent=data.get("agent", ""),
                    model=data.get("model", ""),
                    attempt=data.get("attempt", 1),
                )
            elif event_type == "agent_result":
                m.on_agent_result(
                    agent=data.get("agent", ""),
                    model=data.get("model", ""),
                    success=data.get("success", False),
                    cost_usd=data.get("cost_usd", 0.0),
                    attempt=data.get("attempt", 1),
                    error_code=data.get("error_code"),
                    input_tokens=data.get("input_tokens", 0),
                    output_tokens=data.get("output_tokens", 0),
                )
            elif event_type == "task_invoke":
                m.on_task_invoke(
                    task_id=data.get("task_id", ""),
                    step=data.get("step", ""),
                    agent=data.get("agent", ""),
                    model=data.get("model", ""),
                    attempt=data.get("attempt", 1),
                    dependencies=data.get("dependencies"),
                )
            elif event_type == "task_result":
                m.on_task_result(
                    task_id=data.get("task_id", ""),
                    success=data.get("success", False),
                    cost_usd=data.get("cost_usd", 0.0),
                    step=data.get("step", ""),
                    role=data.get("role", ""),
                    model_tier=data.get("model", ""),
                    duration_s=data.get("duration_s", 0.0),
                    error_code=data.get("error_code"),
                )
            elif event_type == "budget_warning":
                m.on_budget_warning(
                    cumulative_cost_usd=data.get("cumulative_cost_usd", 0.0),
                    max_budget_usd=data.get("max_budget_usd", 0.0),
                )
            elif event_type == "model_escalation":
                m.on_model_escalation(
                    agent=data.get("agent", ""),
                    from_model=data.get("from_model", ""),
                    to_model=data.get("to_model", ""),
                )
        except Exception as e:
            # Never let monitoring failures break the pipeline
            self._log.debug("monitoring_dispatch_error", error=str(e), event_type=event_type)

    def check_budget(self, max_budget_usd: float) -> str | None:
        if max_budget_usd <= 0:
            return None
        with self._lock:
            ratio = self._cumulative_cost / max_budget_usd
        if ratio >= 1.0:
            return "exceeded"
        if ratio >= 0.8:
            return "warning"
        return None

    @property
    def cumulative_cost_usd(self) -> float:
        with self._lock:
            return self._cumulative_cost
