"""Data access layer — reads workspace files for the dashboard."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from orchestrator.workspace_manager import WorkspaceManager


@dataclass
class RunSummary:
    run_id: str
    feature_request: str
    workflow_type: str
    status: str
    total_cost_usd: float
    start_time: str
    end_time: str | None
    steps_completed: int
    steps_total: int


@dataclass
class RunDetail:
    run_id: str
    feature_request: str
    workflow_type: str
    status: str
    total_cost_usd: float
    phases: dict[str, Any]
    events: list[dict[str, Any]]
    timeline: dict[str, Any] | None
    interrupt_history: list[dict[str, Any]]


class RunDataReader:
    """Reads orchestrator workspace data for dashboard consumption."""

    def __init__(self, workspace_manager: WorkspaceManager) -> None:
        self.manager = workspace_manager
        self.workspace = workspace_manager.project_workspace

    def list_runs(self) -> list[RunSummary]:
        """List all runs using WorkspaceManager's discovery logic."""
        runs_data = self.manager.list_runs()
        summaries: list[RunSummary] = []
        
        for data in runs_data:
            run_id = data.get("run_id")
            if not run_id:
                continue
                
            phases = data.get("phases", {})
            completed = sum(1 for p in phases.values() if p.get("status") == "completed")
            total = len(phases)
            has_failed = any(p.get("status") == "failed" for p in phases.values())
            
            # Start time from state if available, else use mtime as fallback for sorting/display
            start_time = data.get("start_time") or ""
            
            summaries.append(RunSummary(
                run_id=run_id,
                feature_request=data.get("feature_request", "")[:120],
                workflow_type=data.get("workflow_type", "unknown"),
                status="failed" if has_failed else ("completed" if completed == total and total > 0 else "running"),
                total_cost_usd=data.get("total_cost_usd", 0.0),
                start_time=start_time,
                end_time=data.get("end_time"),
                steps_completed=completed,
                steps_total=total,
            ))
        return summaries

    def get_run(self, run_id: str) -> RunDetail | None:
        state_path = self.manager.find_run_state(run_id)
        if not state_path or not state_path.exists():
            return None
            
        try:
            state = json.loads(state_path.read_text())
        except (json.JSONDecodeError, OSError):
            return None
            
        run_workspace = state_path.parent
        logs_dir = run_workspace / "logs"
        log_file = logs_dir / f"run-{run_id}.jsonl"
        
        # In new style, logs might be named just "run.jsonl" or follow the old pattern
        if not log_file.exists():
            # Try generic log name if in isolated dir
            log_file = logs_dir / "run.jsonl"
            
        events = self._read_events(log_file) if log_file.exists() else []

        # Extract info from events or state
        feature_request = state.get("feature_request", "")
        workflow_type = state.get("workflow_type", "")
        
        has_failed = any(
            p.get("status") == "failed" for p in state.get("phases", {}).values()
        )
        is_completed = all(
            p.get("status") == "completed" for p in state.get("phases", {}).values()
        ) and bool(state.get("phases"))

        # Find complete event for cost
        end_event = next((e for e in events if e.get("event") == "run_complete"), {})

        return RunDetail(
            run_id=run_id,
            feature_request=feature_request,
            workflow_type=workflow_type,
            status="failed" if has_failed else ("completed" if is_completed else "running"),
            total_cost_usd=state.get("total_cost_usd", end_event.get("total_cost_usd", 0.0)),
            phases=state.get("phases", {}),
            events=events,
            timeline=self._load_timeline(run_id),
            interrupt_history=state.get("interrupt_history", []),
        )
        phases = end_event.get("phases", {})
        if state and state.get("run_id") == run_id:
            phases = state.get("phases", phases)

        # Load timeline
        timeline = self._load_timeline(run_id)

        all_phases = phases or {}
        has_failed = any(
            p.get("status") == "failed" for p in all_phases.values()
        )

        return RunDetail(
            run_id=run_id,
            feature_request=start_event.get("feature_request", ""),
            workflow_type=start_event.get("workflow_type", ""),
            status="failed" if has_failed else ("completed" if end_event else "running"),
            total_cost_usd=end_event.get("total_cost_usd", 0.0),
            phases=all_phases,
            events=events,
            timeline=timeline,
            interrupt_history=state.get("interrupt_history", []) if state else [],
        )

    def get_events(
        self, run_id: str, offset: int = 0, limit: int = 100,
    ) -> list[dict[str, Any]]:
        log_file = self.logs_dir / f"run-{run_id}.jsonl"
        if not log_file.exists():
            return []
        events = self._read_events(log_file)
        return events[offset : offset + limit]

    def tail_events(self, run_id: str, after_line: int = 0) -> list[dict[str, Any]]:
        log_file = self.logs_dir / f"run-{run_id}.jsonl"
        if not log_file.exists():
            return []
        events = self._read_events(log_file)
        return events[after_line:]

    def get_alert_history(self, limit: int = 100) -> list[dict[str, Any]]:
        alert_file = self.workspace / "alerts.jsonl"
        if not alert_file.exists():
            return []
        alerts: list[dict[str, Any]] = []
        for line in alert_file.read_text().strip().split("\n"):
            if line.strip():
                try:
                    alerts.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return list(reversed(alerts[:limit]))

    def get_metrics_summary(self) -> dict[str, Any]:
        """Aggregate metrics across all runs for dashboard charts."""
        runs = self.list_runs()
        total_cost = sum(r.total_cost_usd for r in runs)
        completed = sum(1 for r in runs if r.status == "completed")
        failed = sum(1 for r in runs if r.status == "failed")
        running = sum(1 for r in runs if r.status == "running")

        # Collect cost-per-run data for charts
        cost_series: list[dict[str, Any]] = []
        model_usage: dict[str, int] = {}
        error_count = 0

        for run_summary in runs:
            state_path = self.manager.find_run_state(run_summary.run_id)
            if not state_path:
                continue
            logs_dir = state_path.parent / "logs"
            log_file = logs_dir / f"run-{run_summary.run_id}.jsonl"
            if not log_file.exists():
                log_file = logs_dir / "run.jsonl"
            
            events = self._read_events(log_file)
            for ev in events:
                if ev.get("event") == "agent_invoke":
                    model = ev.get("model", "unknown")
                    model_usage[model] = model_usage.get(model, 0) + 1
                if ev.get("event") == "agent_result" and not ev.get("success"):
                    error_count += 1
            cost_series.append({
                "run_id": run_summary.run_id,
                "cost": run_summary.total_cost_usd,
                "time": run_summary.start_time,
            })

        return {
            "total_runs": len(runs),
            "completed": completed,
            "failed": failed,
            "running": running,
            "total_cost_usd": round(total_cost, 4),
            "cost_series": cost_series,
            "model_usage": model_usage,
            "error_count": error_count,
        }

    # --- Internal helpers ---

    def _load_timeline(self, run_id: str) -> dict[str, Any] | None:
        state_path = self.manager.find_run_state(run_id)
        if not state_path:
            return None
        timeline_path = state_path.parent / "timeline.json"
        if not timeline_path.exists():
            return None
        try:
            return json.loads(timeline_path.read_text())
        except (json.JSONDecodeError, OSError):
            return None

    def _read_events(self, log_file: Path) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        try:
            for line in log_file.read_text().strip().split("\n"):
                if line.strip():
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError:
            pass
        return events
