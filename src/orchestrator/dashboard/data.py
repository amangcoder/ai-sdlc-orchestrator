"""Data access layer — reads workspace files for the dashboard."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.error
import urllib.request
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


# ---------------------------------------------------------------------------
# Simple TTL cache (no external dependencies)
# ---------------------------------------------------------------------------

class _TTLCache:
    """Lightweight TTL cache backed by a plain dict + monotonic clock.

    Thread-safety: relies on CPython's GIL for dict operations; adequate for
    dashboard use-cases where concurrent writers are unlikely.  Not suitable
    for multi-process deployments.

    Args:
        ttl: Seconds before a cached entry expires.
        maxsize: Maximum number of entries before oldest is evicted (LRU-lite).
    """

    def __init__(self, ttl: float, maxsize: int = 512) -> None:
        self._ttl = ttl
        self._maxsize = maxsize
        # key → (expiry_monotonic, value)
        self._data: dict[Any, tuple[float, Any]] = {}

    def get(self, key: Any) -> tuple[Any, bool]:
        """Return ``(value, True)`` on hit, ``(None, False)`` on miss/expired."""
        entry = self._data.get(key)
        if entry is None:
            return None, False
        expiry, value = entry
        if time.monotonic() > expiry:
            self._data.pop(key, None)
            return None, False
        return value, True

    def set(self, key: Any, value: Any) -> None:
        """Store *value* under *key* with the configured TTL."""
        if len(self._data) >= self._maxsize:
            # Evict the oldest insertion (dict preserves insertion order in 3.7+)
            oldest = next(iter(self._data))
            del self._data[oldest]
        self._data[key] = (time.monotonic() + self._ttl, value)

    def invalidate(self, key: Any) -> None:
        """Remove a single entry if present."""
        self._data.pop(key, None)

    def clear(self) -> None:
        """Remove all entries."""
        self._data.clear()


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

    # ------------------------------------------------------------------
    # Monitoring service defaults (port → service name)
    # ------------------------------------------------------------------
    _DEFAULT_SERVICES: list[tuple[str, int]] = [
        ("grafana",    3000),
        ("prometheus", 9090),
        ("loki",       3100),
        ("jaeger",    16686),
        ("alertmanager", 9093),
    ]

    def __init__(self, workspace_manager: WorkspaceManager) -> None:
        self.manager = workspace_manager
        self.workspace = workspace_manager.project_workspace
        self.logs_dir = self.workspace / "logs"

        # Per-instance caches — shared across concurrent SSE connections on the
        # same reader instance (which is the common deployment pattern).
        #
        # _events_cache: keyed on (str(log_file_path), mtime_ns) so we only
        #   re-parse when the file actually changes.  No time-based expiry is
        #   needed because mtime invalidation is exact; maxsize caps memory.
        self._events_cache: _TTLCache = _TTLCache(ttl=300.0, maxsize=64)

        # _metrics_cache: full get_metrics_summary() result, 30 s TTL.
        self._metrics_cache: _TTLCache = _TTLCache(ttl=30.0, maxsize=4)

        # _overview_cache: get_dashboard_overview() result, 4 s TTL.
        self._overview_cache: _TTLCache = _TTLCache(ttl=4.0, maxsize=4)

    # ------------------------------------------------------------------
    # Run listing
    # ------------------------------------------------------------------

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

            # Start time from state if available, else use mtime as fallback
            start_time = data.get("start_time") or ""

            summaries.append(RunSummary(
                run_id=run_id,
                feature_request=data.get("feature_request", "")[:120],
                workflow_type=data.get("workflow_type", "unknown"),
                status="failed" if has_failed else (
                    "completed" if completed == total and total > 0 else "running"
                ),
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

        # In new style, logs might be named just "run.jsonl" or follow old pattern
        if not log_file.exists():
            log_file = logs_dir / "run.jsonl"

        events = self._read_events(log_file) if log_file.exists() else []

        feature_request = state.get("feature_request", "")
        workflow_type = state.get("workflow_type", "")

        has_failed = any(
            p.get("status") == "failed" for p in state.get("phases", {}).values()
        )
        is_completed = all(
            p.get("status") == "completed" for p in state.get("phases", {}).values()
        ) and bool(state.get("phases"))

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

    # ------------------------------------------------------------------
    # Events — read + tail
    # ------------------------------------------------------------------

    def get_events(
        self, run_id: str, offset: int = 0, limit: int = 100,
    ) -> list[dict[str, Any]]:
        log_file = self._find_log_file(run_id)
        if not log_file:
            return []
        events = self._read_events(log_file)
        return events[offset : offset + limit]

    def tail_events(self, run_id: str, after_line: int = 0) -> list[dict[str, Any]]:
        """Return events after *after_line* using the mtime-keyed cache.

        Efficient for repeated polling: the cache avoids re-parsing the log
        file if it has not changed since the last call.
        """
        log_file = self._find_log_file(run_id)
        if not log_file:
            return []
        events = self._read_events(log_file)
        return events[after_line:]

    def tail_events_by_offset(
        self, run_id: str, byte_offset: int = 0
    ) -> tuple[list[dict[str, Any]], int]:
        """Return new events since *byte_offset* in the JSONL log, O(delta).

        Unlike :meth:`tail_events`, this method seeks directly to
        *byte_offset* in the log file and reads **only the new bytes**,
        making each SSE poll proportional to new data rather than total
        file size.

        Args:
            run_id: Run identifier.
            byte_offset: Byte position to start reading from (0 = beginning).

        Returns:
            ``(events, new_byte_offset)`` where *new_byte_offset* is the
            position at end-of-file after this read — pass it as
            *byte_offset* on the next call.
        """
        log_file = self._find_log_file(run_id)
        if not log_file:
            return [], byte_offset

        try:
            with log_file.open("rb") as fh:
                fh.seek(byte_offset)
                raw = fh.read()
                new_offset = byte_offset + len(raw)
        except OSError:
            return [], byte_offset

        events: list[dict[str, Any]] = []
        for line in raw.split(b"\n"):
            stripped = line.strip()
            if stripped:
                try:
                    events.append(json.loads(stripped))
                except (json.JSONDecodeError, ValueError):
                    continue

        return events, new_offset

    async def tail_events_async(
        self, run_id: str, byte_offset: int = 0
    ) -> tuple[list[dict[str, Any]], int]:
        """Async variant of :meth:`tail_events_by_offset`.

        Wraps the blocking file I/O in :func:`asyncio.to_thread` so that
        SSE generators do not block the event loop.

        Returns:
            ``(events, new_byte_offset)`` — same contract as
            :meth:`tail_events_by_offset`.
        """
        return await asyncio.to_thread(
            self.tail_events_by_offset, run_id, byte_offset
        )

    # ------------------------------------------------------------------
    # Alerts
    # ------------------------------------------------------------------

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

    def _count_active_alerts(self) -> int:
        """Return the count of unresolved (non-resolved) alerts."""
        alert_file = self.workspace / "alerts.jsonl"
        if not alert_file.exists():
            return 0
        count = 0
        try:
            for line in alert_file.read_text().strip().split("\n"):
                if not line.strip():
                    continue
                try:
                    alert = json.loads(line)
                    if alert.get("status", "").lower() != "resolved":
                        count += 1
                except json.JSONDecodeError:
                    continue
        except OSError:
            pass
        return count

    # ------------------------------------------------------------------
    # Metrics summary (with 30 s TTL cache)
    # ------------------------------------------------------------------

    def get_metrics_summary(self) -> dict[str, Any]:
        """Aggregate metrics across all runs for dashboard charts.

        Results are cached for 30 seconds so concurrent SSE connections
        on the same reader instance share a single file scan.
        """
        cache_key = "metrics_summary"
        cached, hit = self._metrics_cache.get(cache_key)
        if hit:
            return cached  # type: ignore[return-value]

        result = self._compute_metrics_summary()
        self._metrics_cache.set(cache_key, result)
        return result

    def _compute_metrics_summary(self) -> dict[str, Any]:
        runs = self.list_runs()
        total_cost = sum(r.total_cost_usd for r in runs)
        completed = sum(1 for r in runs if r.status == "completed")
        failed = sum(1 for r in runs if r.status == "failed")
        running = sum(1 for r in runs if r.status == "running")

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

    # ------------------------------------------------------------------
    # Dashboard overview (aggregated KPIs, 4 s TTL cache)
    # ------------------------------------------------------------------

    def get_dashboard_overview(self) -> dict[str, Any]:
        """Return a consolidated real-time overview suitable for the /dashboard page.

        Aggregates the following KPIs by reusing existing methods:

        * ``active_runs`` — count of runs currently in "running" status.
        * ``runs_today`` — count of runs started today (UTC).
        * ``cost_today`` — total cost (USD) of runs started today (UTC).
        * ``burn_rate`` — current USD/hour burn rate from :meth:`get_burn_rate`.
        * ``slo_summary`` — SLO passing status from :meth:`get_slo_report`
          (``all_passing`` bool + SLI list condensed to name/passing pairs).
        * ``active_alerts`` — count of unresolved alerts.

        Results are cached for 4 seconds to prevent redundant file scans
        across concurrent SSE connections.

        Returns:
            Dict with keys: active_runs, runs_today, cost_today, burn_rate,
            slo_summary, active_alerts.
        """
        cache_key = "dashboard_overview"
        cached, hit = self._overview_cache.get(cache_key)
        if hit:
            return cached  # type: ignore[return-value]

        result = self._compute_dashboard_overview()
        self._overview_cache.set(cache_key, result)
        return result

    def _compute_dashboard_overview(self) -> dict[str, Any]:
        runs = self.list_runs()
        now_utc = datetime.now(tz=timezone.utc)
        today_prefix = now_utc.strftime("%Y-%m-%d")

        active_runs = 0
        runs_today = 0
        cost_today = 0.0

        for run in runs:
            if run.status == "running":
                active_runs += 1
            if run.start_time and run.start_time.startswith(today_prefix):
                runs_today += 1
                cost_today += run.total_cost_usd

        burn_rate = self.get_burn_rate()
        active_alerts = self._count_active_alerts()

        # SLO summary — lightweight: just all_passing + per-SLI name/passing
        slo_data = get_slo_report()
        report = slo_data.get("report")
        slo_summary: dict[str, Any] = {
            "all_passing": False,
            "data_available": slo_data.get("data_available", False),
            "slis": [],
        }
        if report is not None:
            try:
                slo_summary["all_passing"] = bool(report.all_passing)
                slo_summary["slis"] = [
                    {"name": sli.name, "passing": sli.passing}
                    for sli in report.slis
                ]
            except AttributeError:
                pass  # report has unexpected shape — leave defaults

        return {
            "active_runs": active_runs,
            "runs_today": runs_today,
            "cost_today": round(cost_today, 4),
            "burn_rate": burn_rate,
            "slo_summary": slo_summary,
            "active_alerts": active_alerts,
        }

    # ------------------------------------------------------------------
    # Global artifact search
    # ------------------------------------------------------------------

    def search_artifacts_global(
        self,
        query: str,
        artifact_type: Optional[str] = None,
        agent: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Search for artifacts matching *query* across **all** runs.

        Scans each run's ``artifacts/.index.json`` for entries whose
        ``name`` or ``schema_name`` matches *query* (case-insensitive
        substring).  Optional *artifact_type* and *agent* parameters
        narrow the results.

        Args:
            query: Case-insensitive substring to match against artifact name
                and schema name.
            artifact_type: If provided, only return artifacts whose
                ``schema_name`` equals this value.
            agent: If provided, only return artifacts saved by this agent.
            limit: Maximum number of results to return.

        Returns:
            List of dicts with keys: name, run_id, schema, agent, version,
            updated_at, size_bytes.
        """
        results: list[dict[str, Any]] = []
        query_lower = query.lower().strip()

        runs_dir = self.workspace / "runs"
        if not runs_dir.exists():
            return results

        for run_dir in sorted(runs_dir.iterdir()):
            if not run_dir.is_dir():
                continue
            index_path = run_dir / "artifacts" / ".index.json"
            if not index_path.exists():
                continue

            try:
                index_data = json.loads(index_path.read_text())
            except (json.JSONDecodeError, OSError):
                continue

            entries: list[dict[str, Any]] = index_data.get("artifacts", [])
            if not isinstance(entries, list):
                # Some index formats store artifacts as a dict keyed by name
                entries = [
                    {"name": k, **v}
                    for k, v in entries.items()
                    if isinstance(v, dict)
                ] if isinstance(entries, dict) else []

            run_id_from_dir = run_dir.name

            for entry in entries:
                if not isinstance(entry, dict):
                    continue

                name: str = entry.get("name", "")
                schema_name: str = entry.get("schema_name", "")
                entry_agent: str = entry.get("agent", "") or ""
                run_id: str = entry.get("run_id", run_id_from_dir)

                # Apply filters
                if artifact_type and schema_name != artifact_type:
                    continue
                if agent and entry_agent != agent:
                    continue

                # Apply query match
                if query_lower and (
                    query_lower not in name.lower()
                    and query_lower not in schema_name.lower()
                ):
                    continue

                results.append({
                    "name": name,
                    "run_id": run_id,
                    "schema": schema_name,
                    "agent": entry_agent or None,
                    "version": entry.get("current_version", 1),
                    "updated_at": entry.get("updated_at", ""),
                    "size_bytes": entry.get("size_bytes", 0),
                })

                if len(results) >= limit:
                    return results

        return results

    # ------------------------------------------------------------------
    # Artifact diff
    # ------------------------------------------------------------------

    def get_artifact_diff(
        self,
        run_id_1: str,
        run_id_2: str,
        artifact_name: str,
    ) -> dict[str, Any]:
        """Compute a key-level diff of *artifact_name* between two runs.

        Loads the current version of the named artifact from each run's
        artifacts directory and compares their top-level keys.

        Args:
            run_id_1: First run (treated as the "before" side of the diff).
            run_id_2: Second run (treated as the "after" side).
            artifact_name: Artifact file name, e.g. ``"prd.json"``.

        Returns:
            Dict with keys:

            * ``added`` — keys present in *run_id_2* but not *run_id_1*.
            * ``removed`` — keys present in *run_id_1* but not *run_id_2*.
            * ``changed`` — keys present in both but with different values,
              each entry being ``{"key": k, "old": v1, "new": v2}``.
            * ``run_id_1``, ``run_id_2``, ``artifact_name`` — echo of inputs.
            * ``error`` — present only when an artifact cannot be loaded.
        """
        base: dict[str, Any] = {
            "run_id_1": run_id_1,
            "run_id_2": run_id_2,
            "artifact_name": artifact_name,
            "added": [],
            "removed": [],
            "changed": [],
        }

        def _load(run_id: str) -> dict[str, Any] | None:
            arts_dir = self.manager.artifacts_dir(run_id)
            # Strip path separators from artifact name to prevent traversal
            safe_name = Path(artifact_name).name
            artifact_path = arts_dir / safe_name
            if not artifact_path.exists():
                return None
            try:
                data = json.loads(artifact_path.read_text())
                return data if isinstance(data, dict) else {"_value": data}
            except (json.JSONDecodeError, OSError):
                return None

        data1 = _load(run_id_1)
        data2 = _load(run_id_2)

        if data1 is None or data2 is None:
            missing = []
            if data1 is None:
                missing.append(run_id_1)
            if data2 is None:
                missing.append(run_id_2)
            base["error"] = f"Artifact '{artifact_name}' not found for run(s): {', '.join(missing)}"
            return base

        keys1 = set(data1.keys())
        keys2 = set(data2.keys())

        base["added"] = sorted(keys2 - keys1)
        base["removed"] = sorted(keys1 - keys2)
        base["changed"] = [
            {"key": k, "old": data1[k], "new": data2[k]}
            for k in sorted(keys1 & keys2)
            if data1[k] != data2[k]
        ]

        return base

    # ------------------------------------------------------------------
    # Log analysis
    # ------------------------------------------------------------------

    def get_log_analysis(self, run_id: Optional[str] = None) -> dict[str, Any]:
        """Delegate to :func:`log_analyzer.analyze_runs` and return a dict.

        Args:
            run_id: When provided, analyse only the specified run; otherwise
                    analyse all runs in the logs directory.

        Returns:
            Dict with keys:

            * ``run_ids`` — list of run IDs analysed.
            * ``patterns`` — list of detected patterns (redundant reads,
              repeated tool calls, prompt fragments).
            * ``errors`` — list of error-related findings.
            * ``recommendations`` — list of improvement suggestions.
            * ``raw`` — full serialised :class:`AnalysisReport` for advanced
              consumers.
            * ``error`` — present only when analysis fails.
        """
        try:
            from orchestrator.log_analyzer import AnalysisReport, analyze_runs, format_report_json
        except ImportError as exc:
            return {"error": f"log_analyzer not available: {exc}", "run_ids": [], "patterns": [], "errors": [], "recommendations": []}

        # Determine which log directory to analyse
        logs_dir: Path
        if run_id:
            log_file = self._find_log_file(run_id)
            if log_file:
                logs_dir = log_file.parent
            else:
                logs_dir = self.logs_dir
        else:
            logs_dir = self.logs_dir

        try:
            report: AnalysisReport = analyze_runs(
                log_dir=logs_dir,
                run_id=run_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("log analysis failed: %s", exc)
            return {"error": str(exc), "run_ids": [], "patterns": [], "errors": [], "recommendations": []}

        # Flatten into a plain dict for JSON serialisation
        patterns: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        recommendations: list[dict[str, Any]] = []

        if report.repeated_tasks:
            patterns.append({"type": "repeated_tasks", "items": report.repeated_tasks})
        if report.duplicate_role_steps:
            patterns.append({"type": "duplicate_role_steps", "items": report.duplicate_role_steps})
        if getattr(report, "redundant_file_reads", None):
            patterns.append({"type": "redundant_file_reads", "items": report.redundant_file_reads})
        if getattr(report, "repeated_tool_calls", None):
            patterns.append({"type": "repeated_tool_calls", "items": report.repeated_tool_calls})

        # Top-cost steps become recommendations
        if getattr(report, "top_cost_steps", None):
            recommendations.extend([
                {"type": "high_cost_step", **step}
                for step in report.top_cost_steps[:5]
            ])
        if getattr(report, "suggestions", None):
            recommendations.extend([
                {"type": "suggestion", "text": s} for s in report.suggestions
            ])

        try:
            raw_json = format_report_json(report)
        except Exception:  # noqa: BLE001
            raw_json = "{}"

        return {
            "run_ids": list(report.run_ids),
            "patterns": patterns,
            "errors": errors,
            "recommendations": recommendations,
            "raw": json.loads(raw_json),
        }

    # ------------------------------------------------------------------
    # Monitoring health
    # ------------------------------------------------------------------

    def get_monitoring_health(
        self,
        host: str = "localhost",
        services: Optional[list[tuple[str, int]]] = None,
        timeout_s: float = 2.0,
    ) -> list[dict[str, Any]]:
        """Probe monitoring service endpoints and return their health status.

        Makes a lightweight HTTP GET request to each service using the
        standard Python ``urllib.request`` module (no extra dependencies).

        Args:
            host: Hostname/IP to probe (default ``"localhost"``).
            services: List of ``(service_name, port)`` tuples.  When
                ``None``, probes :attr:`_DEFAULT_SERVICES`.
            timeout_s: Per-request timeout in seconds (default 2 s).

        Returns:
            List of dicts with keys:

            * ``service`` — service name.
            * ``port`` — port number.
            * ``status`` — ``"up"`` or ``"down"``.
            * ``response_time_ms`` — round-trip latency in milliseconds, or
              ``-1`` when the probe failed.
            * ``http_status`` — HTTP status code on success, or ``-1``.
        """
        if services is None:
            services = self._DEFAULT_SERVICES

        results: list[dict[str, Any]] = []

        for service_name, port in services:
            url = f"http://{host}:{port}/"
            t_start = time.monotonic()
            http_status = -1
            status = "down"

            try:
                with urllib.request.urlopen(url, timeout=timeout_s) as resp:
                    http_status = resp.status
                    status = "up" if 100 <= http_status < 500 else "down"
            except urllib.error.HTTPError as exc:
                # HTTPError means the server *responded* (even with 4xx/5xx)
                http_status = exc.code
                status = "up" if http_status < 500 else "down"
            except (urllib.error.URLError, OSError, TimeoutError):
                pass  # status stays "down"

            elapsed_ms = round((time.monotonic() - t_start) * 1000, 1)

            results.append({
                "service": service_name,
                "port": port,
                "status": status,
                "response_time_ms": elapsed_ms if status == "up" else -1,
                "http_status": http_status,
            })

        return results

    # ------------------------------------------------------------------
    # Paginated run listing
    # ------------------------------------------------------------------

    def list_runs_paginated(
        self,
        page: int = 1,
        per_page: int = 20,
        status_filter: Optional[str] = None,
        workflow_filter: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> dict[str, Any]:
        """List runs with server-side filtering and pagination.

        Active ("running") runs are **pinned to the top** of the result set
        regardless of start time, so they are always visible.

        Args:
            page: 1-based page number (default 1).
            per_page: Rows per page (default 20, max 200).
            status_filter: If provided, only include runs with this status
                (``"running"``, ``"completed"``, ``"failed"``).
            workflow_filter: If provided, only include runs whose
                ``workflow_type`` matches this value.
            date_from: ISO-8601 date string ``"YYYY-MM-DD"``; exclude runs
                started before this date.
            date_to: ISO-8601 date string ``"YYYY-MM-DD"``; exclude runs
                started after this date.

        Returns:
            Dict with keys:

            * ``total_count`` — total matching runs before pagination.
            * ``page`` — echoed page number (clamped to valid range).
            * ``per_page`` — echoed rows per page.
            * ``total_pages`` — total number of pages.
            * ``runs`` — list of :class:`RunSummary` instances for this page.
        """
        # Clamp per_page
        per_page = max(1, min(per_page, 200))
        page = max(1, page)

        all_runs = self.list_runs()

        # Apply filters
        filtered: list[RunSummary] = []
        for run in all_runs:
            if status_filter and run.status != status_filter:
                continue
            if workflow_filter and run.workflow_type != workflow_filter:
                continue
            if date_from and run.start_time and run.start_time[:10] < date_from:
                continue
            if date_to and run.start_time and run.start_time[:10] > date_to:
                continue
            filtered.append(run)

        # Pin active runs to top, then sort rest newest-first
        active = [r for r in filtered if r.status == "running"]
        inactive = [r for r in filtered if r.status != "running"]

        # Sort inactive by start_time descending (newest first)
        inactive.sort(key=lambda r: r.start_time or "", reverse=True)

        ordered = active + inactive
        total_count = len(ordered)
        total_pages = max(1, (total_count + per_page - 1) // per_page)

        # Clamp page to valid range
        page = min(page, total_pages)

        start = (page - 1) * per_page
        end = start + per_page
        page_runs = ordered[start:end]

        return {
            "total_count": total_count,
            "page": page,
            "per_page": per_page,
            "total_pages": total_pages,
            "runs": page_runs,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

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
        """Parse a JSONL log file into a list of event dicts.

        Results are cached keyed on ``(str(log_file), mtime_ns)`` so that
        repeated calls for an unchanged file are O(1) — no disk I/O.
        Multiple concurrent SSE connections on the same
        :class:`RunDataReader` instance therefore share a single parse.

        The underlying I/O is *synchronous*.  For async callers that must
        not block the event loop, use :meth:`tail_events_async` instead.
        """
        if not log_file.exists():
            return []

        try:
            mtime_ns = log_file.stat().st_mtime_ns
        except OSError:
            return []

        cache_key = (str(log_file), mtime_ns)
        cached, hit = self._events_cache.get(cache_key)
        if hit:
            return cached  # type: ignore[return-value]

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

        self._events_cache.set(cache_key, events)
        return events

    async def _read_events_async(self, log_file: Path) -> list[dict[str, Any]]:
        """Async wrapper around :meth:`_read_events` using asyncio.to_thread.

        Avoids blocking the event loop when reading large log files from
        an async context (e.g. SSE generators in FastAPI).
        """
        return await asyncio.to_thread(self._read_events, log_file)

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
