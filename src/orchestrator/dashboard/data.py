"""Data access layer — reads workspace files for the dashboard."""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

from orchestrator.workspace_manager import WorkspaceManager

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Observability URL helpers
# ---------------------------------------------------------------------------

#: Grafana dashboard UIDs — must match the JSON files in
#: infra/monitoring/grafana/dashboards/
_GRAFANA_DASHBOARDS: list[tuple[str, str, str]] = [
    ("run_overview", "run-overview", "Run Overview"),
    ("cost_analysis", "cost-analysis", "Cost Analysis"),
    ("agent_performance", "agent-performance", "Agent Performance"),
    ("error_analysis", "error-analysis", "Error Analysis"),
    ("slo_overview", "slo-overview", "SLO Overview"),
]


def get_observability_urls(
    run_id: Optional[str] = None,
    grafana_url: Optional[str] = None,
    jaeger_ui_url: Optional[str] = None,
    loki_endpoint: Optional[str] = None,
) -> dict[str, Optional[str]]:
    """Construct deep-link URLs for Grafana, Jaeger, and Loki.

    Builds 8 URLs in total:
      - 5 Grafana dashboard links (pre-filtered by run_id when supplied)
      - 1 Jaeger trace-search link
      - 1 Grafana Explore link targeting the Loki datasource (LogQL)
      - 1 Direct Loki query-range API link

    When a base URL is *not* configured the corresponding dict values are
    ``None`` — callers / templates must handle that case gracefully.

    Args:
        run_id: Optional pipeline run identifier used to pre-filter all links.
        grafana_url: Base URL of the Grafana instance, e.g. ``http://localhost:3000``.
        jaeger_ui_url: Base URL of the Jaeger UI, e.g. ``http://localhost:16686``.
        loki_endpoint: Base URL of the Loki push/query API, e.g. ``http://localhost:3100``.

    Returns:
        Dict with exactly 8 keys.  Values are ``str`` when the corresponding
        service is configured, or ``None`` otherwise.
    """
    run_param: str = run_id or ""

    # ------------------------------------------------------------------
    # Helper: build one Grafana dashboard URL
    # ------------------------------------------------------------------
    def _grafana_dashboard(uid: str) -> Optional[str]:
        if not grafana_url:
            return None
        base = grafana_url.rstrip("/")
        url = f"{base}/d/{uid}/{uid}?orgId=1"
        if run_param:
            url += f"&var-run_id={quote(run_param, safe='')}"
        return url

    # ------------------------------------------------------------------
    # Jaeger trace-search URL
    # Per-spec format: {jaeger_ui_url}/search?service=orchestrator&tags=run_id:{run_id}
    # ------------------------------------------------------------------
    jaeger_search: Optional[str] = None
    if jaeger_ui_url:
        base = jaeger_ui_url.rstrip("/")
        if run_param:
            jaeger_search = (
                f"{base}/search?service=orchestrator"
                f"&tags=run_id%3A{quote(run_param, safe='')}"
            )
        else:
            jaeger_search = f"{base}/search?service=orchestrator"

    # ------------------------------------------------------------------
    # LogQL query string shared by the two Loki URLs
    # Per-spec: {job="orchestrator", run_id="{run_id}"}
    # ------------------------------------------------------------------
    if run_param:
        _logql = f'{{job="orchestrator", run_id="{run_param}"}}'
    else:
        _logql = '{job="orchestrator"}'

    # Grafana Explore → Loki datasource
    loki_explore: Optional[str] = None
    if grafana_url:
        base = grafana_url.rstrip("/")
        explore_state = json.dumps(
            {
                "datasource": "Loki",
                "queries": [{"expr": _logql, "refId": "A"}],
                "range": {"from": "now-1h", "to": "now"},
            },
            separators=(",", ":"),
        )
        loki_explore = f"{base}/explore?orgId=1&left={quote(explore_state, safe='')}"

    # Direct Loki query-range API URL
    loki_query_api: Optional[str] = None
    if loki_endpoint:
        base = loki_endpoint.rstrip("/")
        loki_query_api = (
            f"{base}/loki/api/v1/query_range"
            f"?query={quote(_logql, safe='')}&limit=100"
        )

    return {
        "grafana_run_overview": _grafana_dashboard("run-overview"),
        "grafana_cost_analysis": _grafana_dashboard("cost-analysis"),
        "grafana_agent_performance": _grafana_dashboard("agent-performance"),
        "grafana_error_analysis": _grafana_dashboard("error-analysis"),
        "grafana_slo_overview": _grafana_dashboard("slo-overview"),
        "jaeger_trace_search": jaeger_search,
        "loki_explore": loki_explore,
        "loki_query_api": loki_query_api,
    }


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
        self.logs_dir = self.workspace / "logs"

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

    def _find_log_file(self, run_id: str) -> Path | None:
        """Locate the JSONL log file for a run.

        Checks:
        1. Per-run directory: project/runs/{run_id}/logs/run-{run_id}.jsonl
        2. Per-run directory: project/runs/{run_id}/logs/run.jsonl
        3. Flat project logs: project/logs/run-{run_id}.jsonl
        4. Global run registry: ~/.orchestrator/runs/{run_id}.json -> jsonl_path
        """
        # 1+2. Per-run directory
        run_dir = self.manager.run_workspace(run_id)
        if run_dir.exists():
            candidate = run_dir / "logs" / f"run-{run_id}.jsonl"
            if candidate.exists():
                return candidate
            candidate = run_dir / "logs" / "run.jsonl"
            if candidate.exists():
                return candidate

        # 3. Flat project logs dir
        candidate = self.logs_dir / f"run-{run_id}.jsonl"
        if candidate.exists():
            return candidate

        # 4. Global registry — discovers CLI-started runs
        from orchestrator.run_registry import lookup_run
        entry = lookup_run(run_id)
        if entry:
            jsonl_path = Path(entry["jsonl_path"])
            if jsonl_path.exists():
                return jsonl_path

        return None

    def get_events(
        self, run_id: str, offset: int = 0, limit: int = 100,
    ) -> list[dict[str, Any]]:
        log_file = self._find_log_file(run_id)
        if not log_file:
            return []
        events = self._read_events(log_file)
        return events[offset : offset + limit]

    def tail_events(self, run_id: str, after_line: int = 0) -> list[dict[str, Any]]:
        log_file = self._find_log_file(run_id)
        if not log_file:
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

    # ---------------------------------------------------------------------------
    # Cost analytics
    # ---------------------------------------------------------------------------

    def get_cost_analytics(self) -> dict[str, Any]:
        """Aggregate cost analytics from workspace/runs/*/state.json.

        Returns:
            Dict with keys:
            - ``cost_trend``: list of {date, cost} sorted by date
            - ``by_agent``: dict mapping agent name → {cost, count}
            - ``by_model``: dict mapping model name → {cost, count}
            - ``burn_rate``: float USD/hour over the last 1-hour window
        """
        runs = self.list_runs()
        cost_trend = self._build_cost_trend(runs)
        by_agent = self.get_cost_by_agent()
        by_model = self.get_cost_by_model()
        burn_rate = self.get_burn_rate()
        return {
            "cost_trend": cost_trend,
            "by_agent": by_agent,
            "by_model": by_model,
            "burn_rate": burn_rate,
        }

    def _build_cost_trend(self, runs: list[RunSummary]) -> list[dict[str, Any]]:
        """Build daily cost trend data from run summaries."""
        daily: dict[str, float] = defaultdict(float)
        for run in runs:
            if run.start_time and len(run.start_time) >= 10:
                date = run.start_time[:10]
                daily[date] += run.total_cost_usd
        return [{"date": d, "cost": round(c, 4)} for d, c in sorted(daily.items())]

    def get_cost_by_agent(self) -> dict[str, dict[str, Any]]:
        """Group costs by agent across all runs.

        Reads phase-level cost data from each run's state.json (each phase
        key is an agent role such as ``pm``, ``architect``, etc.).  Also
        scans JSONL event logs for ``agent_result`` events that carry a
        ``cost_usd`` field, adding any cost not already captured in phases.

        Returns:
            Dict mapping agent name → {cost: float, count: int}.
        """
        agent_costs: dict[str, dict[str, Any]] = {}

        runs = self.list_runs()
        for run_summary in runs:
            # --- Phase-level cost from state.json ---
            state_path = self.manager.find_run_state(run_summary.run_id)
            if state_path and state_path.exists():
                try:
                    state = json.loads(state_path.read_text())
                    for agent_name, phase_data in state.get("phases", {}).items():
                        if not isinstance(phase_data, dict):
                            continue
                        cost = float(phase_data.get("cost_usd") or 0.0)
                        rec = agent_costs.setdefault(
                            agent_name, {"cost": 0.0, "count": 0}
                        )
                        rec["cost"] += cost
                        rec["count"] += 1
                except (json.JSONDecodeError, OSError, TypeError, ValueError):
                    pass

            # --- Event-level cost from JSONL ---
            log_file = self._find_log_file(run_summary.run_id)
            if log_file:
                for ev in self._read_events(log_file):
                    if ev.get("event") != "agent_result":
                        continue
                    agent = str(ev.get("agent") or "unknown")
                    cost = float(ev.get("cost_usd") or 0.0)
                    if cost <= 0.0:
                        continue
                    rec = agent_costs.setdefault(agent, {"cost": 0.0, "count": 0})
                    rec["cost"] += cost
                    rec["count"] += 1

        # Round all accumulated costs
        for rec in agent_costs.values():
            rec["cost"] = round(rec["cost"], 4)
        return agent_costs

    def get_cost_by_model(self) -> dict[str, dict[str, Any]]:
        """Group costs by model across all runs' JSONL event logs.

        Scans ``agent_invoke`` and ``agent_result`` events for ``model`` and
        ``cost_usd`` fields.

        Returns:
            Dict mapping model name → {cost: float, count: int}.
        """
        model_data: dict[str, dict[str, Any]] = {}

        runs = self.list_runs()
        for run_summary in runs:
            log_file = self._find_log_file(run_summary.run_id)
            if not log_file:
                continue
            for ev in self._read_events(log_file):
                if ev.get("event") not in ("agent_invoke", "agent_result"):
                    continue
                model = str(ev.get("model") or "unknown")
                cost = float(ev.get("cost_usd") or 0.0)
                rec = model_data.setdefault(model, {"cost": 0.0, "count": 0})
                rec["cost"] += cost
                rec["count"] += 1

        for rec in model_data.values():
            rec["cost"] = round(rec["cost"], 4)
        return model_data

    def get_burn_rate(self) -> float:
        """Return estimated cost burn rate in USD/hour.

        Computes burn rate from runs whose ``start_time`` falls within the
        last 1-hour window.  If no runs are found in that window, returns
        ``0.0``.

        Returns:
            Estimated cost in USD accumulated over the last hour.
        """
        now_ts = datetime.now(tz=timezone.utc).timestamp()
        one_hour_ago = now_ts - 3600.0
        total_cost = 0.0
        for run in self.list_runs():
            if not run.start_time:
                continue
            try:
                # Accept ISO-8601 with or without 'Z' / offset
                ts_str = run.start_time.replace("Z", "+00:00")
                ts = datetime.fromisoformat(ts_str).timestamp()
            except (ValueError, TypeError):
                continue
            if ts >= one_hour_ago:
                total_cost += run.total_cost_usd
        return round(total_cost, 4)


# ---------------------------------------------------------------------------
# SLO report helper
# ---------------------------------------------------------------------------

def _default_slo_report() -> Any:
    """Return a neutral SLOReport used when no SLO tracker is available.

    Uses the same default target values as :class:`SLOConfig` so the page
    shows meaningful targets even without real run data.  All actuals are
    set to the neutral/safe value that each SLI exhibits with zero runs
    (matching :meth:`SLOTracker._compute_in_memory_actuals` behaviour on a
    fresh tracker).
    """
    try:
        from orchestrator.monitoring.slo import SLIResult, SLOReport  # local to avoid heavy dep at import time
        slis = [
            SLIResult(name="pipeline_success_rate", target=0.95, actual=0.0,
                      passing=False, error_budget_remaining_pct=100.0),
            SLIResult(name="phase_duration_p95", target=300.0, actual=0.0,
                      passing=True, error_budget_remaining_pct=100.0),
            SLIResult(name="cost_per_run_p50", target=1.0, actual=0.0,
                      passing=True, error_budget_remaining_pct=100.0),
            SLIResult(name="artifact_validation_rate", target=0.99, actual=1.0,
                      passing=True, error_budget_remaining_pct=100.0),
            SLIResult(name="error_rate", target=5.0, actual=0.0,
                      passing=True, error_budget_remaining_pct=100.0),
            SLIResult(name="recovery_success_rate", target=0.90, actual=1.0,
                      passing=True, error_budget_remaining_pct=100.0),
        ]
        return SLOReport(
            evaluated_at=datetime.now(tz=timezone.utc).isoformat(),
            evaluation_window_hours=24,
            slis=slis,
            all_passing=False,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not build default SLOReport: %s", exc)
        return None


def get_slo_report(monitoring_stack: Any = None) -> dict[str, Any]:
    """Evaluate SLOs via *monitoring_stack.slo_tracker* when available.

    Calls :meth:`SLOTracker.evaluate_slos` on the tracker attached to
    *monitoring_stack*.  Falls back to a neutral default report when the
    SLO tracker is not enabled or *monitoring_stack* is ``None``.

    Args:
        monitoring_stack: Optional ``MonitoringStack`` instance.  When
            provided and its ``slo_tracker`` property is not ``None``,
            ``evaluate_slos()`` is called to obtain real SLI values.

    Returns:
        Dict with keys:

        * ``report`` — :class:`~orchestrator.monitoring.slo.SLOReport`
          instance (real or default).
        * ``data_available`` — ``True`` when real SLO data was evaluated,
          ``False`` when the default/neutral report is used.
    """
    report = None
    data_available = False

    if monitoring_stack is not None:
        try:
            slo_tracker = monitoring_stack.slo_tracker
            if slo_tracker is not None:
                report = slo_tracker.evaluate_slos()
                data_available = True
        except Exception as exc:  # noqa: BLE001
            logger.debug("SLO evaluation failed — using default report: %s", exc)

    if report is None:
        report = _default_slo_report()

    return {
        "report": report,
        "data_available": data_available,
    }
