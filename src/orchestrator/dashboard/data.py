"""Data access layer — reads workspace files for the dashboard."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


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

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.logs_dir = workspace / "logs"
        self.state_path = workspace / "state.json"

    def list_runs(self) -> list[RunSummary]:
        runs: list[RunSummary] = []
        if not self.logs_dir.exists():
            return runs

        for log_file in sorted(self.logs_dir.glob("run-*.jsonl"), reverse=True):
            run_id = log_file.stem.replace("run-", "")
            summary = self._parse_run_summary(log_file, run_id)
            if summary:
                runs.append(summary)
        return runs

    def get_run(self, run_id: str) -> RunDetail | None:
        log_file = self.logs_dir / f"run-{run_id}.jsonl"
        if not log_file.exists():
            return None

        events = self._read_events(log_file)
        if not events:
            return None

        # Extract info from events
        start_event = next((e for e in events if e.get("event") == "run_start"), {})
        end_event = next((e for e in events if e.get("event") == "run_complete"), {})

        # Try loading state.json for richer data
        state = self._load_state()
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
            log_file = self.logs_dir / f"run-{run_summary.run_id}.jsonl"
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

    def _parse_run_summary(self, log_file: Path, run_id: str) -> RunSummary | None:
        events = self._read_events(log_file)
        if not events:
            return None

        start = next((e for e in events if e.get("event") == "run_start"), None)
        end = next((e for e in events if e.get("event") == "run_complete"), None)

        if not start:
            return None

        phases = end.get("phases", {}) if end else {}
        has_failed = any(p.get("status") == "failed" for p in phases.values())

        return RunSummary(
            run_id=run_id,
            feature_request=start.get("feature_request", "")[:120],
            workflow_type=start.get("workflow_type", "unknown"),
            status="failed" if has_failed else ("completed" if end else "running"),
            total_cost_usd=end.get("total_cost_usd", 0.0) if end else 0.0,
            start_time=start.get("ts", ""),
            end_time=end.get("ts") if end else None,
            steps_completed=len([p for p in phases.values() if p.get("status") == "completed"]),
            steps_total=len(phases),
        )

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

    def _load_state(self) -> dict[str, Any] | None:
        if not self.state_path.exists():
            return None
        try:
            return json.loads(self.state_path.read_text())
        except (json.JSONDecodeError, OSError):
            return None

    def _load_timeline(self, run_id: str) -> dict[str, Any] | None:
        timeline_path = self.workspace / "timeline.json"
        if not timeline_path.exists():
            return None
        try:
            return json.loads(timeline_path.read_text())
        except (json.JSONDecodeError, OSError):
            return None
