"""Tests for TASK-010: cost analytics dashboard page and API endpoints.

Acceptance criteria verified:
  1.  APIRouter in routes/cost.py has exactly 2 endpoints.
  2.  GET /cost-analytics returns HTML with Chart.js visualisations.
  3.  GET /api/v1/cost-analytics returns JSON with cost_trend, by_agent,
      by_model, burn_rate.
  4.  get_cost_analytics() aggregates cost from workspace/runs/*/state.json.
  5.  get_cost_by_agent() groups costs by agent with counts.
  6.  get_cost_by_model() groups costs by model with counts.
  7.  get_burn_rate() computes from runs in the last 1h window.
  8.  Auth middleware updated to protect all dashboard HTML pages.
  9.  Router registered in create_app() via app.include_router().
  10. Dashboard renders without errors with a valid DASHBOARD_TOKEN.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient

from orchestrator.dashboard.data import RunDataReader
from orchestrator.dashboard.routes.cost import create_cost_router

# ---------------------------------------------------------------------------
# Constants & helpers
# ---------------------------------------------------------------------------

TEMPLATES_DIR = (
    Path(__file__).parent.parent
    / "src"
    / "orchestrator"
    / "dashboard"
    / "templates"
)


def _make_reader(workspace_root: Path, project_name: str = "test-project") -> RunDataReader:
    """Create a RunDataReader backed by *workspace_root*."""
    from orchestrator.workspace_manager import WorkspaceManager

    manager = WorkspaceManager(workspace_root, project_name)
    return RunDataReader(manager)


def _make_cost_app(reader: RunDataReader) -> FastAPI:
    """Create a minimal FastAPI app with only the cost router mounted."""
    app = FastAPI()
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    router = create_cost_router(templates, reader)
    app.include_router(router)
    return app


def _make_dashboard_app(workspace_root: Path, project_name: str = "test-project") -> FastAPI:
    """Create the full dashboard app for integration-level tests."""
    from orchestrator.dashboard.app import create_app

    return create_app(workspace_root, project_name)


def _write_run_state(
    workspace_root: Path,
    run_id: str,
    *,
    total_cost_usd: float = 0.0,
    start_time: str | None = None,
    phases: dict[str, Any] | None = None,
) -> Path:
    """Write a minimal state.json for *run_id* into *workspace_root*.

    Returns the path to the state.json file.
    """
    project_dir = workspace_root / "test-project"
    run_dir = project_dir / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    state: dict[str, Any] = {
        "run_id": run_id,
        "feature_request": f"test feature {run_id}",
        "workflow_type": "feature_development",
        "total_cost_usd": total_cost_usd,
        # Pass start_time as-is when explicitly provided (even ""),
        # fall back to now only when the caller did not provide it (None).
        "start_time": start_time if start_time is not None else datetime.now(tz=timezone.utc).isoformat(),
        "phases": phases or {},
    }
    state_path = run_dir / "state.json"
    state_path.write_text(json.dumps(state))
    return state_path


def _write_run_events(
    workspace_root: Path,
    run_id: str,
    events: list[dict[str, Any]],
) -> Path:
    """Write JSONL event log for *run_id* and return the log file path."""
    project_dir = workspace_root / "test-project"
    run_dir = project_dir / "runs" / run_id
    logs_dir = run_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / f"run-{run_id}.jsonl"
    log_file.write_text("\n".join(json.dumps(e) for e in events))
    return log_file


# ---------------------------------------------------------------------------
# 1. Router structure — 2 endpoints
# ---------------------------------------------------------------------------


class TestCostRouterStructure:
    """The cost router must expose exactly 2 routes."""

    def test_router_has_two_routes(self, tmp_path: Path) -> None:
        reader = _make_reader(tmp_path)
        templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
        router = create_cost_router(templates, reader)
        # Each route is an APIRoute; filter out non-route entries
        from fastapi.routing import APIRoute
        routes = [r for r in router.routes if isinstance(r, APIRoute)]
        assert len(routes) == 2

    def test_router_has_html_route(self, tmp_path: Path) -> None:
        reader = _make_reader(tmp_path)
        templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
        router = create_cost_router(templates, reader)
        from fastapi.routing import APIRoute
        paths = [r.path for r in router.routes if isinstance(r, APIRoute)]
        assert "/cost-analytics" in paths

    def test_router_has_api_route(self, tmp_path: Path) -> None:
        reader = _make_reader(tmp_path)
        templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
        router = create_cost_router(templates, reader)
        from fastapi.routing import APIRoute
        paths = [r.path for r in router.routes if isinstance(r, APIRoute)]
        assert "/api/v1/cost-analytics" in paths


# ---------------------------------------------------------------------------
# 2. GET /cost-analytics → HTML with Chart.js
# ---------------------------------------------------------------------------


class TestCostAnalyticsHtmlPage:
    """GET /cost-analytics must return an HTML response with Chart.js charts."""

    def test_returns_200(self, tmp_path: Path) -> None:
        reader = _make_reader(tmp_path)
        client = TestClient(_make_cost_app(reader))
        response = client.get("/cost-analytics")
        assert response.status_code == 200

    def test_content_type_is_html(self, tmp_path: Path) -> None:
        reader = _make_reader(tmp_path)
        client = TestClient(_make_cost_app(reader))
        response = client.get("/cost-analytics")
        assert "text/html" in response.headers["content-type"]

    def test_html_contains_chartjs_canvas(self, tmp_path: Path) -> None:
        reader = _make_reader(tmp_path)
        client = TestClient(_make_cost_app(reader))
        response = client.get("/cost-analytics")
        # The template must include Chart.js canvas elements
        assert b"<canvas" in response.content

    def test_html_contains_cost_trend_chart(self, tmp_path: Path) -> None:
        reader = _make_reader(tmp_path)
        client = TestClient(_make_cost_app(reader))
        response = client.get("/cost-analytics")
        assert b"costTrendChart" in response.content

    def test_html_contains_cost_by_agent_chart(self, tmp_path: Path) -> None:
        reader = _make_reader(tmp_path)
        client = TestClient(_make_cost_app(reader))
        response = client.get("/cost-analytics")
        assert b"costByAgentChart" in response.content

    def test_html_contains_cost_by_model_chart(self, tmp_path: Path) -> None:
        reader = _make_reader(tmp_path)
        client = TestClient(_make_cost_app(reader))
        response = client.get("/cost-analytics")
        assert b"costByModelChart" in response.content

    def test_html_contains_burn_rate_chart(self, tmp_path: Path) -> None:
        reader = _make_reader(tmp_path)
        client = TestClient(_make_cost_app(reader))
        response = client.get("/cost-analytics")
        assert b"burnRateChart" in response.content

    def test_html_renders_with_data(self, tmp_path: Path) -> None:
        """Page must render without errors when run data is present."""
        _write_run_state(tmp_path, "run-abc", total_cost_usd=0.05)
        reader = _make_reader(tmp_path)
        client = TestClient(_make_cost_app(reader))
        response = client.get("/cost-analytics")
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# 3. GET /api/v1/cost-analytics → JSON with required keys
# ---------------------------------------------------------------------------


class TestCostAnalyticsJsonApi:
    """GET /api/v1/cost-analytics must return a JSON dict with the 4 required keys."""

    def test_returns_200(self, tmp_path: Path) -> None:
        reader = _make_reader(tmp_path)
        client = TestClient(_make_cost_app(reader))
        response = client.get("/api/v1/cost-analytics")
        assert response.status_code == 200

    def test_response_is_json_object(self, tmp_path: Path) -> None:
        reader = _make_reader(tmp_path)
        client = TestClient(_make_cost_app(reader))
        response = client.get("/api/v1/cost-analytics")
        assert isinstance(response.json(), dict)

    def test_response_has_cost_trend_key(self, tmp_path: Path) -> None:
        reader = _make_reader(tmp_path)
        client = TestClient(_make_cost_app(reader))
        data = client.get("/api/v1/cost-analytics").json()
        assert "cost_trend" in data

    def test_response_has_by_agent_key(self, tmp_path: Path) -> None:
        reader = _make_reader(tmp_path)
        client = TestClient(_make_cost_app(reader))
        data = client.get("/api/v1/cost-analytics").json()
        assert "by_agent" in data

    def test_response_has_by_model_key(self, tmp_path: Path) -> None:
        reader = _make_reader(tmp_path)
        client = TestClient(_make_cost_app(reader))
        data = client.get("/api/v1/cost-analytics").json()
        assert "by_model" in data

    def test_response_has_burn_rate_key(self, tmp_path: Path) -> None:
        reader = _make_reader(tmp_path)
        client = TestClient(_make_cost_app(reader))
        data = client.get("/api/v1/cost-analytics").json()
        assert "burn_rate" in data

    def test_cost_trend_is_list(self, tmp_path: Path) -> None:
        reader = _make_reader(tmp_path)
        client = TestClient(_make_cost_app(reader))
        data = client.get("/api/v1/cost-analytics").json()
        assert isinstance(data["cost_trend"], list)

    def test_by_agent_is_dict(self, tmp_path: Path) -> None:
        reader = _make_reader(tmp_path)
        client = TestClient(_make_cost_app(reader))
        data = client.get("/api/v1/cost-analytics").json()
        assert isinstance(data["by_agent"], dict)

    def test_by_model_is_dict(self, tmp_path: Path) -> None:
        reader = _make_reader(tmp_path)
        client = TestClient(_make_cost_app(reader))
        data = client.get("/api/v1/cost-analytics").json()
        assert isinstance(data["by_model"], dict)

    def test_burn_rate_is_numeric(self, tmp_path: Path) -> None:
        reader = _make_reader(tmp_path)
        client = TestClient(_make_cost_app(reader))
        data = client.get("/api/v1/cost-analytics").json()
        assert isinstance(data["burn_rate"], (int, float))

    def test_all_four_required_keys_present(self, tmp_path: Path) -> None:
        """Single assertion that all 4 required keys are present."""
        reader = _make_reader(tmp_path)
        client = TestClient(_make_cost_app(reader))
        data = client.get("/api/v1/cost-analytics").json()
        assert set(data.keys()) >= {"cost_trend", "by_agent", "by_model", "burn_rate"}


# ---------------------------------------------------------------------------
# 4. get_cost_analytics() — unit tests
# ---------------------------------------------------------------------------


class TestGetCostAnalytics:
    """Unit tests for RunDataReader.get_cost_analytics()."""

    def test_returns_dict(self, tmp_path: Path) -> None:
        reader = _make_reader(tmp_path)
        result = reader.get_cost_analytics()
        assert isinstance(result, dict)

    def test_all_keys_present(self, tmp_path: Path) -> None:
        reader = _make_reader(tmp_path)
        result = reader.get_cost_analytics()
        assert "cost_trend" in result
        assert "by_agent" in result
        assert "by_model" in result
        assert "burn_rate" in result

    def test_aggregates_cost_from_state_json(self, tmp_path: Path) -> None:
        _write_run_state(tmp_path, "run-01", total_cost_usd=0.10)
        _write_run_state(tmp_path, "run-02", total_cost_usd=0.20)
        reader = _make_reader(tmp_path)
        result = reader.get_cost_analytics()
        total = sum(d["cost"] for d in result["cost_trend"])
        assert abs(total - 0.30) < 1e-9

    def test_empty_workspace_returns_defaults(self, tmp_path: Path) -> None:
        reader = _make_reader(tmp_path)
        result = reader.get_cost_analytics()
        assert result["cost_trend"] == []
        assert result["by_agent"] == {}
        assert result["by_model"] == {}
        assert result["burn_rate"] == 0.0


# ---------------------------------------------------------------------------
# 5. get_cost_by_agent() — unit tests
# ---------------------------------------------------------------------------


class TestGetCostByAgent:
    """Unit tests for RunDataReader.get_cost_by_agent()."""

    def test_returns_dict(self, tmp_path: Path) -> None:
        reader = _make_reader(tmp_path)
        result = reader.get_cost_by_agent()
        assert isinstance(result, dict)

    def test_empty_when_no_runs(self, tmp_path: Path) -> None:
        reader = _make_reader(tmp_path)
        assert reader.get_cost_by_agent() == {}

    def test_groups_by_phase_name(self, tmp_path: Path) -> None:
        """Phase keys in state.json should map to agent names."""
        phases = {
            "pm": {"status": "completed", "cost_usd": 0.05},
            "architect": {"status": "completed", "cost_usd": 0.10},
        }
        _write_run_state(tmp_path, "run-001", phases=phases)
        reader = _make_reader(tmp_path)
        result = reader.get_cost_by_agent()
        assert "pm" in result
        assert "architect" in result

    def test_cost_values_are_accumulated(self, tmp_path: Path) -> None:
        """Costs from multiple runs should be summed per agent."""
        phases_a = {"pm": {"status": "completed", "cost_usd": 0.05}}
        phases_b = {"pm": {"status": "completed", "cost_usd": 0.03}}
        _write_run_state(tmp_path, "run-001", phases=phases_a)
        _write_run_state(tmp_path, "run-002", phases=phases_b)
        reader = _make_reader(tmp_path)
        result = reader.get_cost_by_agent()
        assert abs(result["pm"]["cost"] - 0.08) < 1e-9

    def test_includes_count_field(self, tmp_path: Path) -> None:
        phases = {"backend_engineer": {"status": "completed", "cost_usd": 0.07}}
        _write_run_state(tmp_path, "run-001", phases=phases)
        reader = _make_reader(tmp_path)
        result = reader.get_cost_by_agent()
        assert "count" in result["backend_engineer"]
        assert result["backend_engineer"]["count"] > 0

    def test_reads_agent_result_events(self, tmp_path: Path) -> None:
        """agent_result JSONL events with cost_usd should be included."""
        _write_run_state(tmp_path, "run-001", total_cost_usd=0.0)
        events = [
            {"event": "agent_result", "agent": "qa_engineer", "cost_usd": 0.04, "success": True},
        ]
        _write_run_events(tmp_path, "run-001", events)
        reader = _make_reader(tmp_path)
        result = reader.get_cost_by_agent()
        assert "qa_engineer" in result
        assert abs(result["qa_engineer"]["cost"] - 0.04) < 1e-9

    def test_ignores_events_without_cost(self, tmp_path: Path) -> None:
        """agent_result events with no cost_usd should not create agent entries."""
        _write_run_state(tmp_path, "run-001", total_cost_usd=0.0)
        events = [
            {"event": "agent_result", "agent": "nocharge", "success": True},
        ]
        _write_run_events(tmp_path, "run-001", events)
        reader = _make_reader(tmp_path)
        result = reader.get_cost_by_agent()
        # Should not create an entry for an agent with zero cost from events
        # (if phases is empty) OR it should create one with cost 0 — either is
        # acceptable; the important thing is cost is 0
        if "nocharge" in result:
            assert result["nocharge"]["cost"] == 0.0


# ---------------------------------------------------------------------------
# 6. get_cost_by_model() — unit tests
# ---------------------------------------------------------------------------


class TestGetCostByModel:
    """Unit tests for RunDataReader.get_cost_by_model()."""

    def test_returns_dict(self, tmp_path: Path) -> None:
        reader = _make_reader(tmp_path)
        assert isinstance(reader.get_cost_by_model(), dict)

    def test_empty_when_no_events(self, tmp_path: Path) -> None:
        _write_run_state(tmp_path, "run-001", total_cost_usd=0.10)
        reader = _make_reader(tmp_path)
        assert reader.get_cost_by_model() == {}

    def test_groups_by_model_name(self, tmp_path: Path) -> None:
        _write_run_state(tmp_path, "run-001")
        events = [
            {"event": "agent_invoke", "agent": "pm", "model": "claude-3-5-sonnet", "cost_usd": 0.02},
            {"event": "agent_invoke", "agent": "architect", "model": "claude-opus-4", "cost_usd": 0.05},
        ]
        _write_run_events(tmp_path, "run-001", events)
        reader = _make_reader(tmp_path)
        result = reader.get_cost_by_model()
        assert "claude-3-5-sonnet" in result
        assert "claude-opus-4" in result

    def test_cost_accumulated_per_model(self, tmp_path: Path) -> None:
        _write_run_state(tmp_path, "run-001")
        events = [
            {"event": "agent_invoke", "agent": "pm", "model": "claude-3-5-sonnet", "cost_usd": 0.02},
            {"event": "agent_result", "agent": "pm", "model": "claude-3-5-sonnet", "cost_usd": 0.03},
        ]
        _write_run_events(tmp_path, "run-001", events)
        reader = _make_reader(tmp_path)
        result = reader.get_cost_by_model()
        assert abs(result["claude-3-5-sonnet"]["cost"] - 0.05) < 1e-9

    def test_count_field_present(self, tmp_path: Path) -> None:
        _write_run_state(tmp_path, "run-001")
        events = [
            {"event": "agent_invoke", "agent": "pm", "model": "gpt-4o", "cost_usd": 0.01},
        ]
        _write_run_events(tmp_path, "run-001", events)
        reader = _make_reader(tmp_path)
        result = reader.get_cost_by_model()
        assert "count" in result["gpt-4o"]
        assert result["gpt-4o"]["count"] == 1

    def test_unknown_model_when_missing(self, tmp_path: Path) -> None:
        """Events without a model field should fall under 'unknown'."""
        _write_run_state(tmp_path, "run-001")
        events = [{"event": "agent_invoke", "agent": "pm", "cost_usd": 0.01}]
        _write_run_events(tmp_path, "run-001", events)
        reader = _make_reader(tmp_path)
        result = reader.get_cost_by_model()
        assert "unknown" in result


# ---------------------------------------------------------------------------
# 7. get_burn_rate() — unit tests
# ---------------------------------------------------------------------------


class TestGetBurnRate:
    """Unit tests for RunDataReader.get_burn_rate()."""

    def test_returns_float(self, tmp_path: Path) -> None:
        reader = _make_reader(tmp_path)
        assert isinstance(reader.get_burn_rate(), float)

    def test_zero_when_no_runs(self, tmp_path: Path) -> None:
        reader = _make_reader(tmp_path)
        assert reader.get_burn_rate() == 0.0

    def test_includes_runs_in_last_hour(self, tmp_path: Path) -> None:
        """Runs that started within the last 1h must contribute to burn rate."""
        recent = datetime.now(tz=timezone.utc).isoformat()
        _write_run_state(tmp_path, "run-recent", total_cost_usd=0.07, start_time=recent)
        reader = _make_reader(tmp_path)
        assert reader.get_burn_rate() == 0.07

    def test_excludes_runs_older_than_one_hour(self, tmp_path: Path) -> None:
        """Runs older than 1h must NOT contribute to burn rate."""
        old_time = (datetime.now(tz=timezone.utc) - timedelta(hours=2)).isoformat()
        _write_run_state(tmp_path, "run-old", total_cost_usd=0.99, start_time=old_time)
        reader = _make_reader(tmp_path)
        assert reader.get_burn_rate() == 0.0

    def test_sums_multiple_recent_runs(self, tmp_path: Path) -> None:
        recent = datetime.now(tz=timezone.utc).isoformat()
        _write_run_state(tmp_path, "run-a", total_cost_usd=0.04, start_time=recent)
        _write_run_state(tmp_path, "run-b", total_cost_usd=0.06, start_time=recent)
        reader = _make_reader(tmp_path)
        assert abs(reader.get_burn_rate() - 0.10) < 1e-9

    def test_mixes_recent_and_old_runs(self, tmp_path: Path) -> None:
        recent = datetime.now(tz=timezone.utc).isoformat()
        old = (datetime.now(tz=timezone.utc) - timedelta(hours=3)).isoformat()
        _write_run_state(tmp_path, "run-recent", total_cost_usd=0.05, start_time=recent)
        _write_run_state(tmp_path, "run-old", total_cost_usd=5.00, start_time=old)
        reader = _make_reader(tmp_path)
        assert abs(reader.get_burn_rate() - 0.05) < 1e-9

    def test_handles_runs_without_start_time(self, tmp_path: Path) -> None:
        """Runs without start_time must be silently skipped."""
        _write_run_state(tmp_path, "run-notime", total_cost_usd=0.03, start_time="")
        reader = _make_reader(tmp_path)
        assert reader.get_burn_rate() == 0.0


# ---------------------------------------------------------------------------
# 8. Auth middleware — protects dashboard HTML pages
# ---------------------------------------------------------------------------


class TestAuthMiddlewareUpdated:
    """Auth middleware must now protect all dashboard pages, not just /api."""

    def test_html_page_blocked_without_token(self, tmp_path: Path) -> None:
        with patch.dict(os.environ, {"ORCHESTRATOR_DASHBOARD_TOKEN": "secret"}, clear=False):
            # Rebuild the module-level constant inside app
            with patch("orchestrator.dashboard.app.DASHBOARD_TOKEN", "secret"):
                app = _make_dashboard_app(tmp_path)
                client = TestClient(app, raise_server_exceptions=False)
                response = client.get("/runs")
                assert response.status_code == 401

    def test_cost_analytics_html_blocked_without_token(self, tmp_path: Path) -> None:
        with patch("orchestrator.dashboard.app.DASHBOARD_TOKEN", "secret"):
            app = _make_dashboard_app(tmp_path)
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/cost-analytics")
            assert response.status_code == 401

    def test_cost_analytics_api_blocked_without_token(self, tmp_path: Path) -> None:
        with patch("orchestrator.dashboard.app.DASHBOARD_TOKEN", "secret"):
            app = _make_dashboard_app(tmp_path)
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/api/v1/cost-analytics")
            assert response.status_code == 401

    def test_health_endpoint_not_blocked(self, tmp_path: Path) -> None:
        with patch("orchestrator.dashboard.app.DASHBOARD_TOKEN", "secret"):
            app = _make_dashboard_app(tmp_path)
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/healthz")
            assert response.status_code == 200

    def test_static_endpoint_not_blocked(self, tmp_path: Path) -> None:
        with patch("orchestrator.dashboard.app.DASHBOARD_TOKEN", "secret"):
            app = _make_dashboard_app(tmp_path)
            client = TestClient(app, raise_server_exceptions=False)
            # /static routes are excluded from auth
            response = client.get("/static/style.css")
            # 200 OK or 404 (file may not exist in tmp) but NOT 401
            assert response.status_code != 401

    def test_cost_analytics_accessible_with_valid_token(self, tmp_path: Path) -> None:
        with patch("orchestrator.dashboard.app.DASHBOARD_TOKEN", "mytoken"):
            app = _make_dashboard_app(tmp_path)
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get(
                "/cost-analytics",
                headers={"Authorization": "Bearer mytoken"},
            )
            assert response.status_code == 200

    def test_no_auth_required_when_token_unset(self, tmp_path: Path) -> None:
        with patch("orchestrator.dashboard.app.DASHBOARD_TOKEN", ""):
            app = _make_dashboard_app(tmp_path)
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/cost-analytics")
            assert response.status_code == 200


# ---------------------------------------------------------------------------
# 9. Router registered in create_app()
# ---------------------------------------------------------------------------


class TestRouterRegisteredInCreateApp:
    """The cost router must be reachable from the full dashboard app."""

    def test_full_app_has_cost_analytics_html_route(self, tmp_path: Path) -> None:
        app = _make_dashboard_app(tmp_path)
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/cost-analytics")
        # Route exists → not 404
        assert response.status_code != 404

    def test_full_app_has_cost_analytics_api_route(self, tmp_path: Path) -> None:
        app = _make_dashboard_app(tmp_path)
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/v1/cost-analytics")
        assert response.status_code != 404


# ---------------------------------------------------------------------------
# 10. Dashboard renders without errors with valid DASHBOARD_TOKEN
# ---------------------------------------------------------------------------


class TestDashboardWithToken:
    """Dashboard cost analytics page must render correctly with a valid token."""

    def test_html_page_renders_with_data_and_token(self, tmp_path: Path) -> None:
        recent = datetime.now(tz=timezone.utc).isoformat()
        phases = {
            "pm": {"status": "completed", "cost_usd": 0.05},
            "architect": {"status": "completed", "cost_usd": 0.10},
        }
        _write_run_state(
            tmp_path, "run-xyz", total_cost_usd=0.15, start_time=recent, phases=phases
        )
        events = [
            {"event": "agent_invoke", "agent": "pm", "model": "claude-3-5-sonnet", "cost_usd": 0.05},
        ]
        _write_run_events(tmp_path, "run-xyz", events)

        with patch("orchestrator.dashboard.app.DASHBOARD_TOKEN", "tok"):
            app = _make_dashboard_app(tmp_path)
            client = TestClient(app, raise_server_exceptions=True)
            response = client.get(
                "/cost-analytics", headers={"Authorization": "Bearer tok"}
            )
        assert response.status_code == 200
        assert b"costTrendChart" in response.content
        assert b"Cost Analytics" in response.content

    def test_api_returns_data_with_token(self, tmp_path: Path) -> None:
        recent = datetime.now(tz=timezone.utc).isoformat()
        _write_run_state(tmp_path, "run-abc", total_cost_usd=0.08, start_time=recent)

        with patch("orchestrator.dashboard.app.DASHBOARD_TOKEN", "tok"):
            app = _make_dashboard_app(tmp_path)
            client = TestClient(app, raise_server_exceptions=True)
            response = client.get(
                "/api/v1/cost-analytics",
                headers={"Authorization": "Bearer tok"},
            )
        assert response.status_code == 200
        data = response.json()
        assert len(data["cost_trend"]) >= 1
        assert abs(data["burn_rate"] - 0.08) < 1e-9
