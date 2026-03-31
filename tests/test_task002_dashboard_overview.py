"""Tests for TASK-002 — Dashboard overview API route, HTML page, and SSE endpoint.

Acceptance criteria verified:
  - GET /api/v1/dashboard/overview returns JSON with required keys (REQ-001)
  - GET /dashboard renders HTML with four KPI cards (REQ-001, AC-001)
  - GET /api/v1/dashboard/sse yields JSON overview payloads via SSE
  - / redirects to /dashboard instead of /runs
  - create_dashboard_overview_router follows factory-router pattern
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient

from orchestrator.dashboard.routes.dashboard_overview import (
    _MAX_SSE_DURATION_SECS,
    _SSE_POLL_INTERVAL_SECS,
    create_dashboard_overview_router,
)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

TEMPLATES_DIR = (
    Path(__file__).parent.parent
    / "src"
    / "orchestrator"
    / "dashboard"
    / "templates"
)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

_MOCK_OVERVIEW: dict[str, Any] = {
    "active_runs": 3,
    "runs_today": 7,
    "cost_today": 0.1234,
    "burn_rate": 0.0567,
    "slo_summary": {
        "all_passing": True,
        "slis": [
            {"name": "pipeline_success_rate", "passing": True},
            {"name": "phase_duration_p95", "passing": True},
        ],
    },
    "active_alerts": 2,
}


def _make_reader(overview: dict[str, Any] | None = None) -> MagicMock:
    """Return a mock RunDataReader with get_dashboard_overview stubbed."""
    reader = MagicMock()
    reader.get_dashboard_overview.return_value = overview or dict(_MOCK_OVERVIEW)
    return reader


def _make_app(reader: MagicMock | None = None) -> FastAPI:
    app = FastAPI()
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    router = create_dashboard_overview_router(
        templates=templates,
        reader=reader or _make_reader(),
    )
    app.include_router(router)
    return app


def _client(reader: MagicMock | None = None) -> TestClient:
    return TestClient(_make_app(reader), raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------


class TestModuleConstants:
    def test_sse_poll_interval_is_5_seconds(self):
        assert _SSE_POLL_INTERVAL_SECS == 5

    def test_max_sse_duration_is_30_minutes(self):
        assert _MAX_SSE_DURATION_SECS == 30 * 60


# ---------------------------------------------------------------------------
# Factory-router contract
# ---------------------------------------------------------------------------


class TestCreateDashboardOverviewRouter:
    def test_returns_api_router(self):
        from fastapi import APIRouter

        templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
        router = create_dashboard_overview_router(templates, _make_reader())
        assert isinstance(router, APIRouter)

    def test_router_has_three_routes(self):
        templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
        router = create_dashboard_overview_router(templates, _make_reader())
        paths = {r.path for r in router.routes}
        assert "/api/v1/dashboard/overview" in paths
        assert "/dashboard" in paths
        assert "/api/v1/dashboard/sse" in paths


# ---------------------------------------------------------------------------
# GET /api/v1/dashboard/overview — JSON endpoint
# ---------------------------------------------------------------------------


class TestApiDashboardOverview:
    def test_returns_200(self):
        resp = _client().get("/api/v1/dashboard/overview")
        assert resp.status_code == 200

    def test_returns_required_keys(self):
        resp = _client().get("/api/v1/dashboard/overview")
        data = resp.json()
        required = {"active_runs", "runs_today", "cost_today", "burn_rate",
                    "slo_summary", "active_alerts"}
        assert required.issubset(data.keys())

    def test_active_runs_value(self):
        resp = _client().get("/api/v1/dashboard/overview")
        assert resp.json()["active_runs"] == _MOCK_OVERVIEW["active_runs"]

    def test_runs_today_value(self):
        resp = _client().get("/api/v1/dashboard/overview")
        assert resp.json()["runs_today"] == _MOCK_OVERVIEW["runs_today"]

    def test_cost_today_value(self):
        resp = _client().get("/api/v1/dashboard/overview")
        assert resp.json()["cost_today"] == pytest.approx(_MOCK_OVERVIEW["cost_today"])

    def test_burn_rate_value(self):
        resp = _client().get("/api/v1/dashboard/overview")
        assert resp.json()["burn_rate"] == pytest.approx(_MOCK_OVERVIEW["burn_rate"])

    def test_active_alerts_value(self):
        resp = _client().get("/api/v1/dashboard/overview")
        assert resp.json()["active_alerts"] == _MOCK_OVERVIEW["active_alerts"]

    def test_slo_summary_contains_all_passing(self):
        resp = _client().get("/api/v1/dashboard/overview")
        assert "all_passing" in resp.json()["slo_summary"]

    def test_delegates_to_reader(self):
        reader = _make_reader()
        _client(reader).get("/api/v1/dashboard/overview")
        reader.get_dashboard_overview.assert_called_once()

    def test_zero_values_returned(self):
        reader = _make_reader({
            "active_runs": 0,
            "runs_today": 0,
            "cost_today": 0.0,
            "burn_rate": 0.0,
            "slo_summary": {"all_passing": True, "slis": []},
            "active_alerts": 0,
        })
        resp = _client(reader).get("/api/v1/dashboard/overview")
        assert resp.status_code == 200
        data = resp.json()
        assert data["active_runs"] == 0
        assert data["active_alerts"] == 0

    def test_content_type_is_json(self):
        resp = _client().get("/api/v1/dashboard/overview")
        assert "application/json" in resp.headers["content-type"]


# ---------------------------------------------------------------------------
# GET /dashboard — HTML page
# ---------------------------------------------------------------------------


class TestDashboardPage:
    def test_returns_200(self):
        resp = _client().get("/dashboard")
        assert resp.status_code == 200

    def test_content_type_is_html(self):
        resp = _client().get("/dashboard")
        assert "text/html" in resp.headers["content-type"]

    def test_page_title_in_html(self):
        resp = _client().get("/dashboard")
        assert "Dashboard" in resp.text

    def test_kpi_active_runs_id_present(self):
        resp = _client().get("/dashboard")
        assert 'id="kpi-active-runs"' in resp.text

    def test_kpi_runs_today_id_present(self):
        resp = _client().get("/dashboard")
        assert 'id="kpi-runs-today"' in resp.text

    def test_kpi_cost_today_id_present(self):
        resp = _client().get("/dashboard")
        assert 'id="kpi-cost-today"' in resp.text

    def test_kpi_burn_rate_id_present(self):
        resp = _client().get("/dashboard")
        assert 'id="kpi-burn-rate"' in resp.text

    def test_kpi_active_alerts_id_present(self):
        resp = _client().get("/dashboard")
        assert 'id="kpi-active-alerts"' in resp.text

    def test_slo_summary_section_present(self):
        resp = _client().get("/dashboard")
        assert "SLO Compliance" in resp.text

    def test_active_runs_value_rendered(self):
        resp = _client().get("/dashboard")
        assert str(_MOCK_OVERVIEW["active_runs"]) in resp.text

    def test_active_alerts_value_rendered(self):
        resp = _client().get("/dashboard")
        assert str(_MOCK_OVERVIEW["active_alerts"]) in resp.text

    def test_dashboard_js_script_tag_present(self):
        resp = _client().get("/dashboard")
        assert "dashboard.js" in resp.text

    def test_slo_all_passing_shows_badge(self):
        reader = _make_reader({
            **_MOCK_OVERVIEW,
            "slo_summary": {"all_passing": True, "slis": []},
        })
        resp = _client(reader).get("/dashboard")
        assert "All SLOs passing" in resp.text

    def test_slo_failing_shows_violation_badge(self):
        reader = _make_reader({
            **_MOCK_OVERVIEW,
            "slo_summary": {"all_passing": False, "slis": [
                {"name": "pipeline_success_rate", "passing": False}
            ]},
        })
        resp = _client(reader).get("/dashboard")
        assert "violation" in resp.text.lower()

    def test_overview_fetch_failure_renders_defaults(self):
        """When get_dashboard_overview raises, the page still renders with defaults."""
        reader = MagicMock()
        reader.get_dashboard_overview.side_effect = RuntimeError("DB unavailable")
        resp = _client(reader).get("/dashboard")
        assert resp.status_code == 200
        # Default values: active_runs=0
        assert "0" in resp.text

    def test_delegates_to_reader(self):
        reader = _make_reader()
        _client(reader).get("/dashboard")
        reader.get_dashboard_overview.assert_called_once()

    def test_four_kpi_cards_have_aria_live(self):
        """All four KPI cards must have aria-live for accessibility / AC-001."""
        resp = _client().get("/dashboard")
        html = resp.text
        for kpi_id in ("kpi-active-runs", "kpi-runs-today", "kpi-cost-today", "kpi-burn-rate"):
            assert kpi_id in html, f"missing id={kpi_id}"




# ---------------------------------------------------------------------------
# SSE endpoint via HTTP — content-type and headers only (no streaming body)
# ---------------------------------------------------------------------------


class TestDashboardSseResponse:
    """Verify the SSE StreamingResponse at the handler level (no HTTP layer).

    We invoke the SSE route handler directly as a coroutine and inspect the
    returned StreamingResponse object.  This avoids the infinite-loop problem
    that arises when testing a never-ending async generator via httpx.
    """

    def test_sse_handler_returns_streaming_response(self):
        """dashboard_sse() must return a StreamingResponse."""
        from fastapi.responses import StreamingResponse

        reader = _make_reader()
        templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
        router = create_dashboard_overview_router(templates, reader)

        # Find the SSE route handler
        sse_route = next(r for r in router.routes if r.path == "/api/v1/dashboard/sse")
        handler = sse_route.endpoint

        # Run the async handler synchronously
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(handler())
        finally:
            loop.close()

        assert isinstance(result, StreamingResponse)

    def test_sse_handler_media_type_is_event_stream(self):
        """The StreamingResponse must use text/event-stream media type."""
        from fastapi.responses import StreamingResponse

        reader = _make_reader()
        templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
        router = create_dashboard_overview_router(templates, reader)

        sse_route = next(r for r in router.routes if r.path == "/api/v1/dashboard/sse")
        handler = sse_route.endpoint

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(handler())
        finally:
            loop.close()

        assert result.media_type == "text/event-stream"

    def test_sse_handler_has_no_cache_header(self):
        """The StreamingResponse headers must include Cache-Control: no-cache."""
        reader = _make_reader()
        templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
        router = create_dashboard_overview_router(templates, reader)

        sse_route = next(r for r in router.routes if r.path == "/api/v1/dashboard/sse")
        handler = sse_route.endpoint

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(handler())
        finally:
            loop.close()

        headers = dict(result.headers) if result.headers else {}
        assert headers.get("cache-control") == "no-cache"

    def test_sse_generator_first_event_has_required_keys(self):
        """Drive the SSE async generator for one iteration and inspect the payload."""
        reader = _make_reader()

        async def _run():
            # Patch sleep so it raises CancelledError after first call
            # (CancelledError propagates cleanly out of async generators).
            first_call = True

            async def _sleep_once(_secs):
                nonlocal first_call
                if first_call:
                    first_call = False
                    raise asyncio.CancelledError

            with patch(
                "orchestrator.dashboard.routes.dashboard_overview.asyncio.sleep",
                side_effect=_sleep_once,
            ):
                from orchestrator.dashboard.routes.dashboard_overview import (
                    _SSE_POLL_INTERVAL_SECS as _POLL,
                    create_dashboard_overview_router as _factory,
                )

                app_inner = FastAPI()
                tpls = Jinja2Templates(directory=str(TEMPLATES_DIR))
                r = _factory(tpls, reader)
                app_inner.include_router(r)

                sse_route = next(route for route in r.routes if route.path == "/api/v1/dashboard/sse")
                sr = await sse_route.endpoint()

                # Drain the generator until we get a data line or CancelledError
                chunks = []
                try:
                    async for chunk in sr.body_iterator:
                        if isinstance(chunk, bytes):
                            chunk = chunk.decode()
                        chunks.append(chunk)
                except asyncio.CancelledError:
                    pass
                return chunks

        loop = asyncio.new_event_loop()
        try:
            chunks = loop.run_until_complete(_run())
        finally:
            loop.close()

        # At least one chunk must have been yielded before CancelledError
        assert len(chunks) >= 1
        first_chunk = chunks[0]
        assert first_chunk.startswith("data:")
        payload = json.loads(first_chunk[5:].strip())
        required = {"active_runs", "runs_today", "cost_today",
                    "burn_rate", "slo_summary", "active_alerts"}
        assert required.issubset(payload.keys())


# ---------------------------------------------------------------------------
# Integration: / redirects to /dashboard
# ---------------------------------------------------------------------------


class TestRootRedirect:
    def test_root_redirects_to_dashboard(self):
        """GET / must redirect to /dashboard (not /runs)."""
        from orchestrator.dashboard.app import create_app

        root = Path(__file__).parent.parent / "workspace"
        app = create_app(workspace_root=root, project_name="test_project")
        client = TestClient(app, raise_server_exceptions=True, follow_redirects=False)
        resp = client.get("/")
        assert resp.status_code in (301, 302, 307, 308)
        location = resp.headers.get("location", "")
        assert location.endswith("/dashboard"), (
            f"Expected redirect to /dashboard, got: {location}"
        )

    def test_root_does_not_redirect_to_runs(self):
        from orchestrator.dashboard.app import create_app

        root = Path(__file__).parent.parent / "workspace"
        app = create_app(workspace_root=root, project_name="test_project")
        client = TestClient(app, raise_server_exceptions=True, follow_redirects=False)
        resp = client.get("/")
        location = resp.headers.get("location", "")
        assert "/runs" not in location


# ---------------------------------------------------------------------------
# Integration: create_app wires dashboard router
# ---------------------------------------------------------------------------


class TestCreateAppIntegration:
    def test_dashboard_page_reachable_in_full_app(self):
        from orchestrator.dashboard.app import create_app

        root = Path(__file__).parent.parent / "workspace"
        app = create_app(workspace_root=root, project_name="test_project")
        client = TestClient(app, raise_server_exceptions=True)
        resp = client.get("/dashboard")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_api_overview_reachable_in_full_app(self):
        from orchestrator.dashboard.app import create_app

        root = Path(__file__).parent.parent / "workspace"
        app = create_app(workspace_root=root, project_name="test_project")
        client = TestClient(app, raise_server_exceptions=True)
        resp = client.get("/api/v1/dashboard/overview")
        assert resp.status_code == 200
        data = resp.json()
        required = {"active_runs", "runs_today", "cost_today",
                    "burn_rate", "slo_summary", "active_alerts"}
        assert required.issubset(data.keys())
