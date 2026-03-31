"""Observability: JSONL run logging with structlog + monitoring stack integration."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

import structlog

if TYPE_CHECKING:
    from orchestrator.monitoring import MonitoringStack
    from orchestrator.db.repositories.events import EventRepository


# ---------------------------------------------------------------------------
# Log-level helpers
# ---------------------------------------------------------------------------

#: Event types that always map to WARN level.
_WARN_EVENT_TYPES: frozenset[str] = frozenset({
    "budget_warning",
    "model_escalation",
    "retry",
})

#: Event types that always map to ERROR level.
_ERROR_EVENT_TYPES: frozenset[str] = frozenset({
    "error",
})


def _derive_level(event_type: str, data: dict) -> str:
    """Derive a log level string (INFO/WARN/ERROR) from event type and data.

    Rules (evaluated in order):
    1. ``error`` event type → ``ERROR``
    2. ``budget_warning``, ``model_escalation``, ``retry`` → ``WARN``
    3. ``agent_result`` or ``task_result`` with ``success=False`` → ``ERROR``
    4. ``run_complete`` where any phase has ``status="failed"`` → ``ERROR``
    5. Everything else → ``INFO``
    """
    if event_type in _ERROR_EVENT_TYPES:
        return "ERROR"
    if event_type in _WARN_EVENT_TYPES:
        return "WARN"
    if event_type in ("agent_result", "task_result") and not data.get("success", True):
        return "ERROR"
    if event_type == "run_complete":
        phases = data.get("phases", {})
        if any(
            p.get("status") == "failed"
            for p in phases.values()
            if isinstance(p, dict)
        ):
            return "ERROR"
    return "INFO"


class RunLogger:
    def __init__(
        self,
        log_dir: Path,
        run_id: str,
        event_repo: "EventRepository | None" = None,
        write_sidecar: bool = True,
    ) -> None:
        self.run_id = run_id
        self._write_sidecar = write_sidecar
        log_dir.mkdir(parents=True, exist_ok=True)
        self._log_path = log_dir / f"run-{run_id}.jsonl"
        if write_sidecar:
            self._log_path.touch()
        self._cumulative_cost: float = 0.0
        self._cumulative_input_tokens: int = 0
        self._cumulative_output_tokens: int = 0
        self._lock = threading.Lock()
        self._log = structlog.get_logger(__name__)
        self._monitoring: MonitoringStack | None = None
        # DB write-behind queue — set by set_event_repo()
        self._event_repo: EventRepository | None = None
        if event_repo is not None:
            self.set_event_repo(event_repo)

    def set_monitoring_stack(self, stack: MonitoringStack) -> None:
        """Attach a MonitoringStack to receive all events."""
        self._monitoring = stack

    def set_event_repo(self, event_repo: "EventRepository") -> None:
        """Attach a DB ``EventRepository`` and start its write-behind drain task."""
        self._event_repo = event_repo
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                event_repo.start_drain_task()
        except RuntimeError:
            pass  # no event loop yet — drain task will be started later

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
        level = _derive_level(event_type, data)
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
            # DB write-behind (non-blocking — push onto queue)
            if self._event_repo is not None:
                self._event_repo.push_sync(event_type, data, level=level)
            # Filesystem sidecar (controlled by write_sidecar config)
            if self._write_sidecar:
                with self._log_path.open("a") as f:
                    f.write(json.dumps(record, default=str) + "\n")
        self._log.info(event_type, **data)

        # Delegate to monitoring stack
        if self._monitoring:
            self._dispatch_to_monitoring(event_type, data)

    def _dispatch_to_monitoring(self, event_type: str, data: dict) -> None:
        """Route events to the appropriate MonitoringStack methods.

        Each event is first enriched with cross-cutting fields (trace_id,
        task_id, agent, level) and forwarded to the Loki shipper via
        ``on_log_event()``.  The enriched dict is a *shallow copy* of *data*
        so the original record written to JSONL is not mutated.

        The routing from event_type → handler is expressed as a dispatch
        table (``_dispatch``) for easy unit-testing: callers can introspect
        the dict keys without executing the handlers.
        """
        m = self._monitoring
        if not m:
            return

        # ------------------------------------------------------------------
        # Enrich the event with cross-cutting observability fields
        # ------------------------------------------------------------------
        enriched: dict[str, Any] = {
            **data,
            "trace_id": m.current_trace_id,
            "task_id": data.get("task_id", ""),
            "agent": data.get("agent", ""),
            "level": _derive_level(event_type, data),
        }

        # ------------------------------------------------------------------
        # Dispatch table: event_type → zero-argument callable
        # ------------------------------------------------------------------
        def _run_start() -> None:
            m.on_run_start(
                workflow_type=data.get("workflow_type", ""),
                feature_request=data.get("feature_request", ""),
            )

        def _run_complete() -> None:
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
                duration_s=data.get("duration_s", 0.0),
                errors=data.get("errors", 0),
            )

        def _agent_invoke() -> None:
            m.on_agent_invoke(
                agent=data.get("agent", ""),
                model=data.get("model", ""),
                attempt=data.get("attempt", 1),
            )

        def _agent_result() -> None:
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

        def _task_invoke() -> None:
            m.on_task_invoke(
                task_id=data.get("task_id", ""),
                step=data.get("step", ""),
                agent=data.get("agent", ""),
                model=data.get("model", ""),
                attempt=data.get("attempt", 1),
                dependencies=data.get("dependencies"),
            )

        def _task_result() -> None:
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

        def _budget_warning() -> None:
            m.on_budget_warning(
                cumulative_cost_usd=data.get("cumulative_cost_usd", 0.0),
                max_budget_usd=data.get("max_budget_usd", 0.0),
            )

        def _model_escalation() -> None:
            m.on_model_escalation(
                agent=data.get("agent", ""),
                from_model=data.get("from_model", ""),
                to_model=data.get("to_model", ""),
            )

        def _phase_complete() -> None:
            m.on_phase_end(
                phase_name=data.get("phase", data.get("step", "")),
                success=data.get("success", True),
                cost_usd=data.get("cost_usd", 0.0),
                duration_s=data.get("duration_s", 0.0),
                artifact_valid=data.get("artifact_valid"),
            )

        _dispatch: dict[str, Callable[[], None]] = {
            "run_start": _run_start,
            "run_complete": _run_complete,
            "agent_invoke": _agent_invoke,
            "agent_result": _agent_result,
            "task_invoke": _task_invoke,
            "task_result": _task_result,
            "budget_warning": _budget_warning,
            "model_escalation": _model_escalation,
            "phase_complete": _phase_complete,
        }

        try:
            # Forward enriched event to Loki (if enabled on the monitoring stack)
            m.on_log_event(enriched)

            # Route to the typed monitoring handler
            handler = _dispatch.get(event_type)
            if handler is not None:
                handler()
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
