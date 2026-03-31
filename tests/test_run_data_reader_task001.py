"""Tests for TASK-001 — RunDataReader extensions + performance improvements.

Acceptance criteria verified:
  AC-1  get_dashboard_overview() returns dict with keys: active_runs, runs_today,
        cost_today, burn_rate, slo_summary, active_alerts.
  AC-2  search_artifacts_global() returns list of matching artifacts with
        expected fields: name, run_id, schema, agent, version, updated_at,
        size_bytes.
  AC-3  get_artifact_diff() returns dict with added, removed, changed keys.
  AC-4  get_log_analysis() returns dict with run_ids, patterns, errors,
        recommendations, raw.
  AC-5  get_monitoring_health() returns list of service status dicts with
        service name, port, status, response_time_ms.
  AC-6  list_runs_paginated() returns paginated results with total_count, page,
        per_page, runs.  Active runs pinned to top.
  AC-7  _read_events() and tail reads do not re-parse unchanged files (cache hit).
  AC-8  get_dashboard_overview() and get_metrics_summary() use TTL caching.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from orchestrator.dashboard.data import RunDataReader, _TTLCache
from orchestrator.workspace_manager import WorkspaceManager


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_manager(tmp_path: Path, project: str = "test-proj") -> WorkspaceManager:
    return WorkspaceManager(tmp_path, project)


def _make_reader(tmp_path: Path, project: str = "test-proj") -> RunDataReader:
    return RunDataReader(_make_manager(tmp_path, project))


def _write_state(
    manager: WorkspaceManager,
    run_id: str,
    *,
    status: str = "completed",
    cost: float = 1.0,
    start_time: str = "2026-03-31T00:00:00+00:00",
    workflow_type: str = "sdlc",
    phases: dict | None = None,
) -> Path:
    """Create a minimal state.json for a run."""
    if phases is None:
        phases = {"pm": {"status": status, "cost_usd": cost}}

    run_dir = manager.run_workspace(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "run_id": run_id,
        "feature_request": f"Feature for {run_id}",
        "workflow_type": workflow_type,
        "status": status,
        "total_cost_usd": cost,
        "start_time": start_time,
        "phases": phases,
    }
    state_path = run_dir / "state.json"
    state_path.write_text(json.dumps(state))
    return state_path


def _write_events(manager: WorkspaceManager, run_id: str, events: list[dict]) -> Path:
    """Create a JSONL event log for a run."""
    logs_dir = manager.run_workspace(run_id) / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / f"run-{run_id}.jsonl"
    log_file.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    return log_file


def _write_artifact_index(
    manager: WorkspaceManager,
    run_id: str,
    artifacts: list[dict],
) -> Path:
    """Create a .index.json in the run's artifacts dir."""
    arts_dir = manager.artifacts_dir(run_id)
    arts_dir.mkdir(parents=True, exist_ok=True)
    index = {"artifacts": artifacts}
    index_path = arts_dir / ".index.json"
    index_path.write_text(json.dumps(index))
    return index_path


# ===========================================================================
# _TTLCache tests
# ===========================================================================


class TestTTLCache:
    def test_miss_on_empty_cache(self):
        cache = _TTLCache(ttl=10.0)
        value, hit = cache.get("key")
        assert not hit
        assert value is None

    def test_hit_after_set(self):
        cache = _TTLCache(ttl=10.0)
        cache.set("key", [1, 2, 3])
        value, hit = cache.get("key")
        assert hit
        assert value == [1, 2, 3]

    def test_miss_after_expiry(self):
        cache = _TTLCache(ttl=0.01)  # 10 ms TTL
        cache.set("key", "hello")
        time.sleep(0.02)
        value, hit = cache.get("key")
        assert not hit
        assert value is None

    def test_invalidate_removes_entry(self):
        cache = _TTLCache(ttl=10.0)
        cache.set("k", 42)
        cache.invalidate("k")
        _, hit = cache.get("k")
        assert not hit

    def test_maxsize_evicts_oldest(self):
        cache = _TTLCache(ttl=60.0, maxsize=3)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.set("c", 3)
        cache.set("d", 4)  # should evict "a"
        _, hit_a = cache.get("a")
        _, hit_d = cache.get("d")
        assert not hit_a
        assert hit_d

    def test_clear_removes_all(self):
        cache = _TTLCache(ttl=60.0)
        cache.set("x", 1)
        cache.set("y", 2)
        cache.clear()
        assert not cache.get("x")[1]
        assert not cache.get("y")[1]


# ===========================================================================
# _read_events caching tests (AC-7)
# ===========================================================================


class TestReadEventsCache:
    def test_cache_hit_avoids_re_parse(self, tmp_path: Path):
        manager = _make_manager(tmp_path)
        reader = _make_reader(tmp_path)
        run_id = "run-cache01"
        events_data = [{"event": "run_start", "run_id": run_id}]
        log_file = _write_events(manager, run_id, events_data)

        # First call — cache miss
        result1 = reader._read_events(log_file)
        assert len(result1) == 1

        # Truncate the file — if cache is working, second call returns stale
        # result from cache (mtime unchanged if we monkey-patch stat)
        original_stat = log_file.stat
        mtime_ns = log_file.stat().st_mtime_ns

        # Overwrite with MORE events but manually preserve mtime to simulate
        # cache hit scenario by patching stat()
        log_file.write_text(
            "\n".join(json.dumps(e) for e in [*events_data, {"event": "extra"}])
        )

        # Restore original mtime so cache key matches (preserve nanosecond precision)
        import os
        os.utime(str(log_file), ns=(mtime_ns, mtime_ns))

        result2 = reader._read_events(log_file)
        # Cache hit: should return original 1-event result
        assert len(result2) == 1

    def test_cache_miss_on_mtime_change(self, tmp_path: Path):
        manager = _make_manager(tmp_path)
        reader = _make_reader(tmp_path)
        run_id = "run-cache02"
        events_data = [{"event": "run_start"}]
        log_file = _write_events(manager, run_id, events_data)

        _ = reader._read_events(log_file)

        # Append a new event — mtime will change
        time.sleep(0.01)  # ensure mtime differs
        with log_file.open("a") as fh:
            fh.write(json.dumps({"event": "new_event"}) + "\n")

        result2 = reader._read_events(log_file)
        assert len(result2) == 2  # cache miss → fresh parse

    def test_returns_empty_for_nonexistent_file(self, tmp_path: Path):
        reader = _make_reader(tmp_path)
        result = reader._read_events(tmp_path / "nonexistent.jsonl")
        assert result == []


# ===========================================================================
# tail_events_by_offset tests (AC-7)
# ===========================================================================


class TestTailEventsByOffset:
    def test_offset_zero_returns_all_events(self, tmp_path: Path):
        manager = _make_manager(tmp_path)
        reader = _make_reader(tmp_path)
        run_id = "run-offset01"
        _write_state(manager, run_id)
        events = [{"event": "run_start"}, {"event": "phase_start", "phase": "pm"}]
        _write_events(manager, run_id, events)

        result, new_offset = reader.tail_events_by_offset(run_id, 0)
        assert len(result) == 2
        assert new_offset > 0

    def test_incremental_read_returns_only_new_events(self, tmp_path: Path):
        manager = _make_manager(tmp_path)
        reader = _make_reader(tmp_path)
        run_id = "run-offset02"
        _write_state(manager, run_id)
        log_file = _write_events(manager, run_id, [{"event": "run_start"}])

        # Read up to current end of file
        _, offset_after_first = reader.tail_events_by_offset(run_id, 0)

        # Append new event
        with log_file.open("a") as fh:
            fh.write(json.dumps({"event": "phase_done"}) + "\n")

        new_events, new_offset = reader.tail_events_by_offset(run_id, offset_after_first)
        assert len(new_events) == 1
        assert new_events[0]["event"] == "phase_done"
        assert new_offset > offset_after_first

    def test_returns_empty_for_unknown_run(self, tmp_path: Path):
        reader = _make_reader(tmp_path)
        result, offset = reader.tail_events_by_offset("run-nonexistent", 0)
        assert result == []
        assert offset == 0

    def test_offset_beyond_file_size_returns_empty(self, tmp_path: Path):
        manager = _make_manager(tmp_path)
        reader = _make_reader(tmp_path)
        run_id = "run-offset03"
        _write_state(manager, run_id)
        _write_events(manager, run_id, [{"event": "run_start"}])

        _, eof = reader.tail_events_by_offset(run_id, 0)
        result, new_offset = reader.tail_events_by_offset(run_id, eof)
        assert result == []
        assert new_offset == eof  # position unchanged


class TestTailEventsAsync:
    def test_async_returns_same_as_sync(self, tmp_path: Path):
        manager = _make_manager(tmp_path)
        reader = _make_reader(tmp_path)
        run_id = "run-async01"
        _write_state(manager, run_id)
        _write_events(manager, run_id, [{"event": "run_start"}, {"event": "run_end"}])

        async def _run():
            return await reader.tail_events_async(run_id, 0)

        events, offset = asyncio.get_event_loop().run_until_complete(_run())
        assert len(events) == 2
        assert offset > 0


# ===========================================================================
# get_dashboard_overview tests (AC-1)
# ===========================================================================


class TestGetDashboardOverview:
    def _setup_runs(self, manager: WorkspaceManager) -> None:
        today = time.strftime("%Y-%m-%dT%H:%M:%S+00:00")
        _write_state(manager, "run-active01", status="running",
                     cost=0.5, start_time=today)
        _write_state(manager, "run-done01", status="completed",
                     cost=1.0, start_time=today)
        _write_state(manager, "run-fail01", status="failed",
                     cost=0.2, start_time=today)

    def test_returns_required_keys(self, tmp_path: Path):
        manager = _make_manager(tmp_path)
        self._setup_runs(manager)
        reader = RunDataReader(manager)
        overview = reader.get_dashboard_overview()

        required = {"active_runs", "runs_today", "cost_today", "burn_rate",
                    "slo_summary", "active_alerts"}
        assert required <= set(overview.keys())

    def test_active_runs_count(self, tmp_path: Path):
        manager = _make_manager(tmp_path)
        self._setup_runs(manager)
        reader = RunDataReader(manager)
        overview = reader.get_dashboard_overview()
        assert overview["active_runs"] == 1

    def test_runs_today_count(self, tmp_path: Path):
        manager = _make_manager(tmp_path)
        self._setup_runs(manager)
        reader = RunDataReader(manager)
        overview = reader.get_dashboard_overview()
        assert overview["runs_today"] == 3

    def test_cost_today_is_sum(self, tmp_path: Path):
        manager = _make_manager(tmp_path)
        self._setup_runs(manager)
        reader = RunDataReader(manager)
        overview = reader.get_dashboard_overview()
        assert abs(overview["cost_today"] - 1.7) < 0.001

    def test_slo_summary_has_all_passing(self, tmp_path: Path):
        reader = _make_reader(tmp_path)
        overview = reader.get_dashboard_overview()
        assert "all_passing" in overview["slo_summary"]

    def test_active_alerts_is_int(self, tmp_path: Path):
        reader = _make_reader(tmp_path)
        overview = reader.get_dashboard_overview()
        assert isinstance(overview["active_alerts"], int)

    def test_result_is_cached(self, tmp_path: Path):
        reader = _make_reader(tmp_path)
        r1 = reader.get_dashboard_overview()
        r2 = reader.get_dashboard_overview()
        assert r1 is r2  # same object → cache hit

    def test_burn_rate_is_float(self, tmp_path: Path):
        reader = _make_reader(tmp_path)
        overview = reader.get_dashboard_overview()
        assert isinstance(overview["burn_rate"], float)


# ===========================================================================
# search_artifacts_global tests (AC-2)
# ===========================================================================


class TestSearchArtifactsGlobal:
    def test_returns_matching_artifact(self, tmp_path: Path):
        manager = _make_manager(tmp_path)
        _write_state(manager, "run-art01")
        _write_artifact_index(manager, "run-art01", [
            {
                "name": "prd.json",
                "schema_name": "prd",
                "agent": "pm",
                "run_id": "run-art01",
                "current_version": 1,
                "updated_at": "2026-03-31T00:00:00",
                "size_bytes": 512,
            }
        ])

        reader = RunDataReader(manager)
        results = reader.search_artifacts_global("prd")
        assert len(results) == 1
        assert results[0]["name"] == "prd.json"

    def test_returns_correct_fields(self, tmp_path: Path):
        manager = _make_manager(tmp_path)
        _write_state(manager, "run-art02")
        _write_artifact_index(manager, "run-art02", [
            {
                "name": "architecture.json",
                "schema_name": "architecture",
                "agent": "architect",
                "run_id": "run-art02",
                "current_version": 2,
                "updated_at": "2026-03-31T01:00:00",
                "size_bytes": 1024,
            }
        ])

        reader = RunDataReader(manager)
        results = reader.search_artifacts_global("architecture")
        assert len(results) == 1
        result = results[0]
        for field in ("name", "run_id", "schema", "agent", "version", "updated_at", "size_bytes"):
            assert field in result, f"Missing field: {field}"

    def test_empty_query_returns_all(self, tmp_path: Path):
        manager = _make_manager(tmp_path)
        for i in range(3):
            _write_state(manager, f"run-art-all-{i:02d}")
            _write_artifact_index(manager, f"run-art-all-{i:02d}", [
                {"name": f"artifact_{i}.json", "schema_name": f"schema_{i}",
                 "run_id": f"run-art-all-{i:02d}", "current_version": 1,
                 "updated_at": "", "size_bytes": 0}
            ])

        reader = RunDataReader(manager)
        results = reader.search_artifacts_global("")
        assert len(results) == 3

    def test_artifact_type_filter(self, tmp_path: Path):
        manager = _make_manager(tmp_path)
        _write_state(manager, "run-art-filter01")
        _write_artifact_index(manager, "run-art-filter01", [
            {"name": "prd.json", "schema_name": "prd",
             "run_id": "run-art-filter01", "current_version": 1,
             "updated_at": "", "size_bytes": 0},
            {"name": "tasks.json", "schema_name": "tasks",
             "run_id": "run-art-filter01", "current_version": 1,
             "updated_at": "", "size_bytes": 0},
        ])

        reader = RunDataReader(manager)
        results = reader.search_artifacts_global("", artifact_type="prd")
        assert all(r["schema"] == "prd" for r in results)
        assert len(results) == 1

    def test_agent_filter(self, tmp_path: Path):
        manager = _make_manager(tmp_path)
        _write_state(manager, "run-art-agf01")
        _write_artifact_index(manager, "run-art-agf01", [
            {"name": "prd.json", "schema_name": "prd", "agent": "pm",
             "run_id": "run-art-agf01", "current_version": 1,
             "updated_at": "", "size_bytes": 0},
            {"name": "architecture.json", "schema_name": "architecture",
             "agent": "architect",
             "run_id": "run-art-agf01", "current_version": 1,
             "updated_at": "", "size_bytes": 0},
        ])

        reader = RunDataReader(manager)
        results = reader.search_artifacts_global("", agent="pm")
        assert len(results) == 1
        assert results[0]["agent"] == "pm"

    def test_limit_respected(self, tmp_path: Path):
        manager = _make_manager(tmp_path)
        for i in range(10):
            _write_state(manager, f"run-art-lim-{i:02d}")
            _write_artifact_index(manager, f"run-art-lim-{i:02d}", [
                {"name": f"art_{i}.json", "schema_name": "prd",
                 "run_id": f"run-art-lim-{i:02d}", "current_version": 1,
                 "updated_at": "", "size_bytes": 0},
            ])

        reader = RunDataReader(manager)
        results = reader.search_artifacts_global("", limit=3)
        assert len(results) == 3

    def test_no_runs_returns_empty(self, tmp_path: Path):
        reader = _make_reader(tmp_path)
        results = reader.search_artifacts_global("anything")
        assert results == []

    def test_missing_index_skipped_gracefully(self, tmp_path: Path):
        manager = _make_manager(tmp_path)
        # Create run dir but no index
        run_dir = manager.run_workspace("run-noindex")
        run_dir.mkdir(parents=True, exist_ok=True)
        reader = RunDataReader(manager)
        results = reader.search_artifacts_global("")
        assert results == []


# ===========================================================================
# get_artifact_diff tests (AC-3)
# ===========================================================================


class TestGetArtifactDiff:
    def _write_artifact(
        self, manager: WorkspaceManager, run_id: str, name: str, data: dict
    ) -> None:
        arts_dir = manager.artifacts_dir(run_id)
        arts_dir.mkdir(parents=True, exist_ok=True)
        (arts_dir / name).write_text(json.dumps(data))

    def test_returns_required_keys(self, tmp_path: Path):
        manager = _make_manager(tmp_path)
        self._write_artifact(manager, "run-diff-a", "prd.json", {"title": "old", "scope": "narrow"})
        self._write_artifact(manager, "run-diff-b", "prd.json", {"title": "new", "goals": ["g1"]})

        reader = RunDataReader(manager)
        diff = reader.get_artifact_diff("run-diff-a", "run-diff-b", "prd.json")

        assert "added" in diff
        assert "removed" in diff
        assert "changed" in diff

    def test_added_keys_detected(self, tmp_path: Path):
        manager = _make_manager(tmp_path)
        self._write_artifact(manager, "run-add-a", "tasks.json", {"tasks": []})
        self._write_artifact(manager, "run-add-b", "tasks.json", {"tasks": [], "metadata": {}})

        reader = RunDataReader(manager)
        diff = reader.get_artifact_diff("run-add-a", "run-add-b", "tasks.json")
        assert "metadata" in diff["added"]

    def test_removed_keys_detected(self, tmp_path: Path):
        manager = _make_manager(tmp_path)
        self._write_artifact(manager, "run-rem-a", "prd.json", {"old_key": 1, "stable": 2})
        self._write_artifact(manager, "run-rem-b", "prd.json", {"stable": 2})

        reader = RunDataReader(manager)
        diff = reader.get_artifact_diff("run-rem-a", "run-rem-b", "prd.json")
        assert "old_key" in diff["removed"]

    def test_changed_keys_detected(self, tmp_path: Path):
        manager = _make_manager(tmp_path)
        self._write_artifact(manager, "run-chg-a", "prd.json", {"title": "v1"})
        self._write_artifact(manager, "run-chg-b", "prd.json", {"title": "v2"})

        reader = RunDataReader(manager)
        diff = reader.get_artifact_diff("run-chg-a", "run-chg-b", "prd.json")
        assert len(diff["changed"]) == 1
        changed_entry = diff["changed"][0]
        assert changed_entry["key"] == "title"
        assert changed_entry["old"] == "v1"
        assert changed_entry["new"] == "v2"

    def test_no_diff_identical_artifacts(self, tmp_path: Path):
        manager = _make_manager(tmp_path)
        data = {"same": 1, "content": [1, 2, 3]}
        self._write_artifact(manager, "run-same-a", "prd.json", data)
        self._write_artifact(manager, "run-same-b", "prd.json", data)

        reader = RunDataReader(manager)
        diff = reader.get_artifact_diff("run-same-a", "run-same-b", "prd.json")
        assert diff["added"] == []
        assert diff["removed"] == []
        assert diff["changed"] == []

    def test_error_returned_for_missing_artifact(self, tmp_path: Path):
        manager = _make_manager(tmp_path)
        self._write_artifact(manager, "run-miss-a", "prd.json", {"k": "v"})

        reader = RunDataReader(manager)
        diff = reader.get_artifact_diff("run-miss-a", "run-miss-b", "prd.json")
        assert "error" in diff

    def test_path_traversal_rejected(self, tmp_path: Path):
        manager = _make_manager(tmp_path)
        manager.artifacts_dir("run-trav-a").mkdir(parents=True, exist_ok=True)
        manager.artifacts_dir("run-trav-b").mkdir(parents=True, exist_ok=True)

        reader = RunDataReader(manager)
        # Path traversal attempt in artifact_name
        diff = reader.get_artifact_diff("run-trav-a", "run-trav-b", "../../etc/passwd")
        # Should not raise; error key present since file not found
        assert "error" in diff


# ===========================================================================
# get_log_analysis tests (AC-4)
# ===========================================================================


class TestGetLogAnalysis:
    def test_returns_required_keys(self, tmp_path: Path):
        reader = _make_reader(tmp_path)
        result = reader.get_log_analysis()
        for key in ("run_ids", "patterns", "errors", "recommendations"):
            assert key in result, f"Missing key: {key}"

    def test_run_ids_is_list(self, tmp_path: Path):
        reader = _make_reader(tmp_path)
        result = reader.get_log_analysis()
        assert isinstance(result["run_ids"], list)

    def test_patterns_is_list(self, tmp_path: Path):
        reader = _make_reader(tmp_path)
        result = reader.get_log_analysis()
        assert isinstance(result["patterns"], list)

    def test_errors_is_list(self, tmp_path: Path):
        reader = _make_reader(tmp_path)
        result = reader.get_log_analysis()
        assert isinstance(result["errors"], list)

    def test_recommendations_is_list(self, tmp_path: Path):
        reader = _make_reader(tmp_path)
        result = reader.get_log_analysis()
        assert isinstance(result["recommendations"], list)

    def test_with_run_id_returns_result(self, tmp_path: Path):
        manager = _make_manager(tmp_path)
        _write_state(manager, "run-log01")
        _write_events(manager, "run-log01", [
            {"event": "run_start"},
            {"event": "agent_invoke", "agent": "pm"},
            {"event": "agent_result", "agent": "pm", "success": True},
        ])
        reader = RunDataReader(manager)
        result = reader.get_log_analysis("run-log01")
        assert isinstance(result, dict)
        assert "run_ids" in result

    def test_handles_import_error_gracefully(self, tmp_path: Path):
        reader = _make_reader(tmp_path)
        with patch.dict("sys.modules", {"orchestrator.log_analyzer": None}):
            # Should return error dict, not raise
            result = reader.get_log_analysis()
            assert "error" in result or "run_ids" in result


# ===========================================================================
# get_monitoring_health tests (AC-5)
# ===========================================================================


class TestGetMonitoringHealth:
    def test_returns_list(self, tmp_path: Path):
        reader = _make_reader(tmp_path)
        # Probe against a port that will almost certainly be closed
        results = reader.get_monitoring_health(
            host="127.0.0.1",
            services=[("test-svc", 19999)],
            timeout_s=0.1,
        )
        assert isinstance(results, list)

    def test_each_entry_has_required_fields(self, tmp_path: Path):
        reader = _make_reader(tmp_path)
        results = reader.get_monitoring_health(
            host="127.0.0.1",
            services=[("test-svc", 19998)],
            timeout_s=0.1,
        )
        assert len(results) == 1
        entry = results[0]
        for field in ("service", "port", "status", "response_time_ms"):
            assert field in entry, f"Missing field: {field}"

    def test_closed_port_status_is_down(self, tmp_path: Path):
        reader = _make_reader(tmp_path)
        results = reader.get_monitoring_health(
            host="127.0.0.1",
            services=[("closed", 19997)],
            timeout_s=0.1,
        )
        assert results[0]["status"] == "down"

    def test_default_services_list_used_when_none(self, tmp_path: Path):
        reader = _make_reader(tmp_path)
        results = reader.get_monitoring_health(timeout_s=0.05)
        assert len(results) == len(reader._DEFAULT_SERVICES)
        service_names = {r["service"] for r in results}
        expected_names = {name for name, _ in reader._DEFAULT_SERVICES}
        assert service_names == expected_names

    def test_status_field_values(self, tmp_path: Path):
        reader = _make_reader(tmp_path)
        results = reader.get_monitoring_health(
            services=[("svc-a", 19996), ("svc-b", 19995)],
            timeout_s=0.05,
        )
        for r in results:
            assert r["status"] in ("up", "down")

    def test_up_service_mock(self, tmp_path: Path):
        """Verify behaviour when urllib.request.urlopen succeeds."""
        reader = _make_reader(tmp_path)

        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.status = 200

        with patch("urllib.request.urlopen", return_value=mock_resp):
            results = reader.get_monitoring_health(
                services=[("grafana", 3000)],
                timeout_s=1.0,
            )

        assert results[0]["status"] == "up"
        assert results[0]["http_status"] == 200
        assert results[0]["response_time_ms"] >= 0


# ===========================================================================
# list_runs_paginated tests (AC-6)
# ===========================================================================


class TestListRunsPaginated:
    def _create_runs(self, manager: WorkspaceManager) -> None:
        today = time.strftime("%Y-%m-%dT%H:%M:%S+00:00")
        yesterday = "2026-03-30T10:00:00+00:00"
        _write_state(manager, "run-p-active01", status="running",
                     workflow_type="sdlc", start_time=today)
        _write_state(manager, "run-p-done01", status="completed",
                     workflow_type="sdlc", start_time=today)
        _write_state(manager, "run-p-done02", status="completed",
                     workflow_type="mobile", start_time=yesterday)
        _write_state(manager, "run-p-fail01", status="failed",
                     workflow_type="sdlc", start_time=yesterday)

    def test_returns_required_keys(self, tmp_path: Path):
        manager = _make_manager(tmp_path)
        self._create_runs(manager)
        reader = RunDataReader(manager)
        result = reader.list_runs_paginated()
        for key in ("total_count", "page", "per_page", "total_pages", "runs"):
            assert key in result

    def test_total_count(self, tmp_path: Path):
        manager = _make_manager(tmp_path)
        self._create_runs(manager)
        reader = RunDataReader(manager)
        result = reader.list_runs_paginated()
        assert result["total_count"] == 4

    def test_active_runs_pinned_to_top(self, tmp_path: Path):
        manager = _make_manager(tmp_path)
        self._create_runs(manager)
        reader = RunDataReader(manager)
        result = reader.list_runs_paginated()
        # First run in results should be the active one
        assert result["runs"][0].status == "running"

    def test_status_filter(self, tmp_path: Path):
        manager = _make_manager(tmp_path)
        self._create_runs(manager)
        reader = RunDataReader(manager)
        result = reader.list_runs_paginated(status_filter="completed")
        assert result["total_count"] == 2
        assert all(r.status == "completed" for r in result["runs"])

    def test_workflow_filter(self, tmp_path: Path):
        manager = _make_manager(tmp_path)
        self._create_runs(manager)
        reader = RunDataReader(manager)
        result = reader.list_runs_paginated(workflow_filter="mobile")
        assert result["total_count"] == 1
        assert result["runs"][0].workflow_type == "mobile"

    def test_date_from_filter(self, tmp_path: Path):
        manager = _make_manager(tmp_path)
        self._create_runs(manager)
        reader = RunDataReader(manager)
        today_str = time.strftime("%Y-%m-%d")
        result = reader.list_runs_paginated(date_from=today_str)
        # Only today's runs: run-p-active01, run-p-done01
        assert result["total_count"] == 2

    def test_date_to_filter(self, tmp_path: Path):
        manager = _make_manager(tmp_path)
        self._create_runs(manager)
        reader = RunDataReader(manager)
        result = reader.list_runs_paginated(date_to="2026-03-30")
        # Only yesterday's runs: run-p-done02, run-p-fail01
        assert result["total_count"] == 2

    def test_pagination_slicing(self, tmp_path: Path):
        manager = _make_manager(tmp_path)
        self._create_runs(manager)
        reader = RunDataReader(manager)
        # 4 runs, per_page=2 → 2 pages
        page1 = reader.list_runs_paginated(page=1, per_page=2)
        page2 = reader.list_runs_paginated(page=2, per_page=2)

        assert len(page1["runs"]) == 2
        assert len(page2["runs"]) == 2
        assert page1["total_pages"] == 2
        # No overlap
        ids1 = {r.run_id for r in page1["runs"]}
        ids2 = {r.run_id for r in page2["runs"]}
        assert ids1.isdisjoint(ids2)

    def test_page_clamped_to_valid_range(self, tmp_path: Path):
        manager = _make_manager(tmp_path)
        self._create_runs(manager)
        reader = RunDataReader(manager)
        # Request page 999 → should return last valid page
        result = reader.list_runs_paginated(page=999, per_page=2)
        assert result["page"] == result["total_pages"]

    def test_empty_workspace(self, tmp_path: Path):
        reader = _make_reader(tmp_path)
        result = reader.list_runs_paginated()
        assert result["total_count"] == 0
        assert result["runs"] == []
        assert result["total_pages"] == 1  # minimum 1 page even when empty

    def test_per_page_clamped_to_200(self, tmp_path: Path):
        manager = _make_manager(tmp_path)
        self._create_runs(manager)
        reader = RunDataReader(manager)
        result = reader.list_runs_paginated(per_page=99999)
        assert result["per_page"] == 200


# ===========================================================================
# get_metrics_summary TTL cache tests (AC-8)
# ===========================================================================


class TestMetricsSummaryCache:
    def test_result_is_cached_on_repeat_call(self, tmp_path: Path):
        reader = _make_reader(tmp_path)
        r1 = reader.get_metrics_summary()
        r2 = reader.get_metrics_summary()
        assert r1 is r2  # same object → cache hit

    def test_cache_expires_and_refreshes(self, tmp_path: Path):
        reader = _make_reader(tmp_path)
        # Shorten the metrics cache TTL to near-zero for test speed
        reader._metrics_cache = _TTLCache(ttl=0.01)

        r1 = reader.get_metrics_summary()
        time.sleep(0.02)  # wait for expiry
        r2 = reader.get_metrics_summary()
        # After expiry, a new result is produced (different object id)
        assert r1 is not r2
