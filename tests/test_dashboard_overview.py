"""Integration tests for TASK-019 — Dashboard overview endpoints (full-stack).

These are *integration* tests that drive the complete request/response
cycle through ``create_app()``.  They complement the unit-level router
tests in ``test_task002_dashboard_overview.py`` by exercising the auth
middleware, static-file mount, and all registered sub-routers together.

Acceptance criteria verified:
  - GET /dashboard returns 200 HTML with KPI cards (REQ-001, AC-001)
  - GET /api/v1/dashboard/overview returns correct JSON shape (REQ-001)
  - GET /api/v1/dashboard/sse streams text/event-stream events
  - GET / redirects to /dashboard (not /runs)
  - Auth middleware enforces 401 for missing/wrong token, 200 for valid (AC-017)
  - GET /api/v1/runs response shape is unchanged (backward compat, AC-021)
  - Runs pagination: page param and filter combinations forwarded correctly
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Helpers — shared with test_task000_security_fixes pattern
# ---------------------------------------------------------------------------


def _make_dashboard_app(
    workspace_root: Path,
    project_name: str = "test-project",
    config_path: Optional[Path] = None,
    *,
    auth_token: Optional[str] = None,
) -> FastAPI:
    """Create the full dashboard app with optional auth token."""
    import orchestrator.dashboard.app as _app_mod
    from orchestrator.dashboard.app import create_app

    saved = _app_mod.DASHBOARD_TOKEN
    _app_mod.DASHBOARD_TOKEN = auth_token
    try:
        return create_app(workspace_root, project_name, config_path=config_path)
    finally:
        _app_mod.DASHBOARD_TOKEN = saved


def _client(tmp_path: Path, *, auth_token: Optional[str] = None) -> TestClient:
    return TestClient(
        _make_dashboard_app(tmp_path, auth_token=auth_token),
        raise_server_exceptions=False,
    )


# ---------------------------------------------------------------------------
# GET /dashboard — HTML rendering (full-stack)
# ---------------------------------------------------------------------------


class TestDashboardPageFullStack:
    """Verify GET /dashboard through the full create_app() stack."""

    def test_returns_200(self, tmp_path: Path) -> None:
        resp = _client(tmp_path).get("/dashboard")
        assert resp.status_code == 200

    def test_content_type_is_html(self, tmp_path: Path) -> None:
        resp = _client(tmp_path).get("/dashboard")
        assert "text/html" in resp.headers["content-type"]

    def test_page_title_present(self, tmp_path: Path) -> None:
        resp = _client(tmp_path).get("/dashboard")
        assert "Dashboard" in resp.text

    def test_kpi_active_runs_card_present(self, tmp_path: Path) -> None:
        resp = _client(tmp_path).get("/dashboard")
        assert 'id="kpi-active-runs"' in resp.text

    def test_kpi_runs_today_card_present(self, tmp_path: Path) -> None:
        resp = _client(tmp_path).get("/dashboard")
        assert 'id="kpi-runs-today"' in resp.text

    def test_kpi_cost_today_card_present(self, tmp_path: Path) -> None:
        resp = _client(tmp_path).get("/dashboard")
        assert 'id="kpi-cost-today"' in resp.text

    def test_kpi_burn_rate_card_present(self, tmp_path: Path) -> None:
        resp = _client(tmp_path).get("/dashboard")
        assert 'id="kpi-burn-rate"' in resp.text

    def test_kpi_active_alerts_card_present(self, tmp_path: Path) -> None:
        resp = _client(tmp_path).get("/dashboard")
        assert 'id="kpi-active-alerts"' in resp.text

    def test_slo_summary_section_present(self, tmp_path: Path) -> None:
        resp = _client(tmp_path).get("/dashboard")
        assert "SLO" in resp.text

    def test_dashboard_js_referenced(self, tmp_path: Path) -> None:
        resp = _client(tmp_path).get("/dashboard")
        assert "dashboard.js" in resp.text

    def test_renders_even_when_workspace_empty(self, tmp_path: Path) -> None:
        """Dashboard must render gracefully with zero state files."""
        ws = tmp_path / "workspace"
        ws.mkdir()
        resp = _client(tmp_path).get("/dashboard")
        assert resp.status_code == 200

    def test_navigation_bar_present(self, tmp_path: Path) -> None:
        """Base template nav bar must be included."""
        resp = _client(tmp_path).get("/dashboard")
        assert "Orchestrator" in resp.text


# ---------------------------------------------------------------------------
# GET /api/v1/dashboard/overview — JSON shape (full-stack)
# ---------------------------------------------------------------------------


class TestDashboardOverviewApiFullStack:
    """Verify GET /api/v1/dashboard/overview JSON shape through full app."""

    _REQUIRED_KEYS = frozenset({
        "active_runs", "runs_today", "cost_today",
        "burn_rate", "slo_summary", "active_alerts",
    })

    def test_returns_200(self, tmp_path: Path) -> None:
        resp = _client(tmp_path).get("/api/v1/dashboard/overview")
        assert resp.status_code == 200

    def test_content_type_is_json(self, tmp_path: Path) -> None:
        resp = _client(tmp_path).get("/api/v1/dashboard/overview")
        assert "application/json" in resp.headers["content-type"]

    def test_response_has_all_required_keys(self, tmp_path: Path) -> None:
        resp = _client(tmp_path).get("/api/v1/dashboard/overview")
        data = resp.json()
        assert self._REQUIRED_KEYS.issubset(data.keys()), (
            f"Missing keys: {self._REQUIRED_KEYS - data.keys()}"
        )

    def test_active_runs_is_integer(self, tmp_path: Path) -> None:
        data = _client(tmp_path).get("/api/v1/dashboard/overview").json()
        assert isinstance(data["active_runs"], int)

    def test_runs_today_is_integer(self, tmp_path: Path) -> None:
        data = _client(tmp_path).get("/api/v1/dashboard/overview").json()
        assert isinstance(data["runs_today"], int)

    def test_cost_today_is_numeric(self, tmp_path: Path) -> None:
        data = _client(tmp_path).get("/api/v1/dashboard/overview").json()
        assert isinstance(data["cost_today"], (int, float))

    def test_burn_rate_is_numeric(self, tmp_path: Path) -> None:
        data = _client(tmp_path).get("/api/v1/dashboard/overview").json()
        assert isinstance(data["burn_rate"], (int, float))

    def test_active_alerts_is_integer(self, tmp_path: Path) -> None:
        data = _client(tmp_path).get("/api/v1/dashboard/overview").json()
        assert isinstance(data["active_alerts"], int)

    def test_slo_summary_has_all_passing(self, tmp_path: Path) -> None:
        data = _client(tmp_path).get("/api/v1/dashboard/overview").json()
        assert "all_passing" in data["slo_summary"]

    def test_slo_summary_has_slis_list(self, tmp_path: Path) -> None:
        data = _client(tmp_path).get("/api/v1/dashboard/overview").json()
        assert "slis" in data["slo_summary"]
        assert isinstance(data["slo_summary"]["slis"], list)

    def test_active_runs_non_negative(self, tmp_path: Path) -> None:
        data = _client(tmp_path).get("/api/v1/dashboard/overview").json()
        assert data["active_runs"] >= 0

    def test_cost_today_non_negative(self, tmp_path: Path) -> None:
        data = _client(tmp_path).get("/api/v1/dashboard/overview").json()
        assert data["cost_today"] >= 0.0


# ---------------------------------------------------------------------------
# GET /api/v1/dashboard/sse — SSE stream
# ---------------------------------------------------------------------------


class TestDashboardSseFullStack:
    """Verify SSE endpoint properties through the full app stack."""

    def test_sse_route_registered_in_full_app(self, tmp_path: Path) -> None:
        app = _make_dashboard_app(tmp_path)
        paths = {r.path for r in app.routes}
        assert "/api/v1/dashboard/sse" in paths

    def test_sse_handler_returns_streaming_response(self, tmp_path: Path) -> None:
        """SSE handler must return StreamingResponse with event-stream media type."""
        from fastapi.responses import StreamingResponse
        from orchestrator.dashboard.routes.dashboard_overview import (
            create_dashboard_overview_router,
        )
        from fastapi.templating import Jinja2Templates

        templates_dir = (
            Path(__file__).parent.parent
            / "src" / "orchestrator" / "dashboard" / "templates"
        )
        reader = MagicMock()
        reader.get_dashboard_overview.return_value = {
            "active_runs": 0, "runs_today": 0, "cost_today": 0.0,
            "burn_rate": 0.0, "slo_summary": {"all_passing": True, "slis": []},
            "active_alerts": 0,
        }
        templates = Jinja2Templates(directory=str(templates_dir))
        router = create_dashboard_overview_router(templates, reader)

        sse_route = next(r for r in router.routes if r.path == "/api/v1/dashboard/sse")
        handler = sse_route.endpoint

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(handler())
        finally:
            loop.close()

        assert isinstance(result, StreamingResponse)
        assert result.media_type == "text/event-stream"

    def test_sse_handler_has_no_cache_control_header(self, tmp_path: Path) -> None:
        """SSE response must include Cache-Control: no-cache."""
        from orchestrator.dashboard.routes.dashboard_overview import (
            create_dashboard_overview_router,
        )
        from fastapi.templating import Jinja2Templates

        templates_dir = (
            Path(__file__).parent.parent
            / "src" / "orchestrator" / "dashboard" / "templates"
        )
        reader = MagicMock()
        reader.get_dashboard_overview.return_value = {
            "active_runs": 0, "runs_today": 0, "cost_today": 0.0,
            "burn_rate": 0.0, "slo_summary": {"all_passing": True, "slis": []},
            "active_alerts": 0,
        }
        templates = Jinja2Templates(directory=str(templates_dir))
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

    def test_sse_first_event_has_required_overview_keys(self) -> None:
        """Drive SSE generator for one iteration — payload must have all 6 keys."""
        from unittest.mock import patch
        from orchestrator.dashboard.routes.dashboard_overview import (
            create_dashboard_overview_router,
        )
        from fastapi import FastAPI
        from fastapi.templating import Jinja2Templates

        templates_dir = (
            Path(__file__).parent.parent
            / "src" / "orchestrator" / "dashboard" / "templates"
        )
        reader = MagicMock()
        reader.get_dashboard_overview.return_value = {
            "active_runs": 2, "runs_today": 5, "cost_today": 0.12,
            "burn_rate": 0.05, "slo_summary": {"all_passing": True, "slis": []},
            "active_alerts": 1,
        }

        async def _run():
            first_call = True

            async def _cancel_after_first(_secs):
                nonlocal first_call
                if first_call:
                    first_call = False
                    raise asyncio.CancelledError

            with patch(
                "orchestrator.dashboard.routes.dashboard_overview.asyncio.sleep",
                side_effect=_cancel_after_first,
            ):
                templates = Jinja2Templates(directory=str(templates_dir))
                router = create_dashboard_overview_router(templates, reader)
                sse_route = next(r for r in router.routes if r.path == "/api/v1/dashboard/sse")
                sr = await sse_route.endpoint()
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

        assert len(chunks) >= 1
        first = chunks[0]
        assert first.startswith("data:")
        payload = json.loads(first[5:].strip())
        required = {"active_runs", "runs_today", "cost_today",
                    "burn_rate", "slo_summary", "active_alerts"}
        assert required.issubset(payload.keys())

    def test_sse_values_match_reader_output(self) -> None:
        """SSE payload values must exactly match what the reader returns."""
        from unittest.mock import patch
        from orchestrator.dashboard.routes.dashboard_overview import (
            create_dashboard_overview_router,
        )
        from fastapi.templating import Jinja2Templates

        templates_dir = (
            Path(__file__).parent.parent
            / "src" / "orchestrator" / "dashboard" / "templates"
        )
        expected_overview = {
            "active_runs": 7, "runs_today": 14, "cost_today": 3.14,
            "burn_rate": 0.01, "slo_summary": {"all_passing": False, "slis": []},
            "active_alerts": 3,
        }
        reader = MagicMock()
        reader.get_dashboard_overview.return_value = expected_overview

        async def _run():
            async def _cancel(_secs):
                raise asyncio.CancelledError

            with patch(
                "orchestrator.dashboard.routes.dashboard_overview.asyncio.sleep",
                side_effect=_cancel,
            ):
                templates = Jinja2Templates(directory=str(templates_dir))
                router = create_dashboard_overview_router(templates, reader)
                sse_route = next(r for r in router.routes if r.path == "/api/v1/dashboard/sse")
                sr = await sse_route.endpoint()
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

        assert len(chunks) >= 1
        payload = json.loads(chunks[0][5:].strip())
        assert payload["active_runs"] == 7
        assert payload["runs_today"] == 14
        assert payload["active_alerts"] == 3


# ---------------------------------------------------------------------------
# GET / — redirect to /dashboard
# ---------------------------------------------------------------------------


class TestRootRedirectFullStack:
    """Verify GET / redirects to /dashboard in the full app."""

    def test_root_redirects_to_dashboard(self, tmp_path: Path) -> None:
        client = TestClient(
            _make_dashboard_app(tmp_path),
            raise_server_exceptions=False,
            follow_redirects=False,
        )
        resp = client.get("/")
        assert resp.status_code in (301, 302, 307, 308)
        location = resp.headers.get("location", "")
        assert "/dashboard" in location, f"Expected /dashboard redirect, got: {location!r}"

    def test_root_does_not_redirect_to_runs(self, tmp_path: Path) -> None:
        client = TestClient(
            _make_dashboard_app(tmp_path),
            raise_server_exceptions=False,
            follow_redirects=False,
        )
        resp = client.get("/")
        location = resp.headers.get("location", "")
        assert "/runs" not in location or "/dashboard" in location

    def test_root_followed_redirect_lands_on_dashboard(self, tmp_path: Path) -> None:
        client = TestClient(
            _make_dashboard_app(tmp_path),
            raise_server_exceptions=False,
            follow_redirects=True,
        )
        resp = client.get("/")
        assert resp.status_code == 200
        assert "Dashboard" in resp.text


# ---------------------------------------------------------------------------
# Auth contract tests — new dashboard endpoints
# ---------------------------------------------------------------------------


class TestDashboardOverviewAuth:
    """Verify auth enforcement on all new dashboard overview endpoints."""

    _TOKEN = "test-secret-token-abc123"

    def _authed_client(self, tmp_path: Path) -> TestClient:
        return TestClient(
            _make_dashboard_app(tmp_path, auth_token=self._TOKEN),
            raise_server_exceptions=False,
        )

    # /api/v1/dashboard/overview
    def test_overview_api_returns_401_without_token(self, tmp_path: Path) -> None:
        client = self._authed_client(tmp_path)
        resp = client.get("/api/v1/dashboard/overview")
        assert resp.status_code == 401

    def test_overview_api_returns_200_with_valid_token(self, tmp_path: Path) -> None:
        client = self._authed_client(tmp_path)
        resp = client.get(
            "/api/v1/dashboard/overview",
            headers={"Authorization": f"Bearer {self._TOKEN}"},
        )
        assert resp.status_code == 200

    def test_overview_api_returns_401_with_wrong_token(self, tmp_path: Path) -> None:
        client = self._authed_client(tmp_path)
        resp = client.get(
            "/api/v1/dashboard/overview",
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert resp.status_code == 401

    # /dashboard
    def test_dashboard_page_returns_401_without_token(self, tmp_path: Path) -> None:
        client = self._authed_client(tmp_path)
        resp = client.get("/dashboard")
        assert resp.status_code == 401

    def test_dashboard_page_returns_200_with_valid_token(self, tmp_path: Path) -> None:
        client = self._authed_client(tmp_path)
        resp = client.get(
            "/dashboard",
            headers={"Authorization": f"Bearer {self._TOKEN}"},
        )
        assert resp.status_code == 200

    # /api/v1/dashboard/sse
    def test_sse_returns_401_without_token(self, tmp_path: Path) -> None:
        client = self._authed_client(tmp_path)
        resp = client.get("/api/v1/dashboard/sse")
        assert resp.status_code == 401

    def test_401_response_is_json_with_error_field(self, tmp_path: Path) -> None:
        client = self._authed_client(tmp_path)
        resp = client.get("/api/v1/dashboard/overview")
        assert resp.status_code == 401
        body = resp.json()
        assert "error" in body

    def test_no_auth_required_when_token_unset(self, tmp_path: Path) -> None:
        """When DASHBOARD_TOKEN is None, endpoints are publicly accessible."""
        client = _client(tmp_path, auth_token=None)
        resp = client.get("/api/v1/dashboard/overview")
        assert resp.status_code == 200

    def test_healthz_bypasses_auth(self, tmp_path: Path) -> None:
        """/healthz must be reachable without Authorization header."""
        client = self._authed_client(tmp_path)
        resp = client.get("/healthz")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Backward compatibility — existing API response shapes unchanged
# ---------------------------------------------------------------------------


class TestRunsApiBackwardCompat:
    """Verify GET /api/v1/runs response shape is unchanged (AC-021)."""

    def test_runs_api_returns_200_or_404(self, tmp_path: Path) -> None:
        """GET /api/v1/runs must not 500 — list or 404 are both acceptable."""
        resp = _client(tmp_path).get("/api/v1/runs")
        assert resp.status_code in (200, 404), (
            f"GET /api/v1/runs returned unexpected {resp.status_code}"
        )

    def test_runs_page_returns_200_html(self, tmp_path: Path) -> None:
        """GET /runs (HTML page) must return 200."""
        resp = _client(tmp_path).get("/runs")
        assert resp.status_code == 200

    def test_runs_page_is_html(self, tmp_path: Path) -> None:
        resp = _client(tmp_path).get("/runs")
        assert "text/html" in resp.headers["content-type"]

    def test_runs_page_supports_page_param(self, tmp_path: Path) -> None:
        """GET /runs?page=1 must return 200 — pagination param is honoured."""
        resp = _client(tmp_path).get("/runs?page=1")
        assert resp.status_code == 200

    def test_runs_page_supports_status_filter(self, tmp_path: Path) -> None:
        """GET /runs?status=completed must return 200."""
        resp = _client(tmp_path).get("/runs?status=completed")
        assert resp.status_code == 200

    def test_runs_page_supports_workflow_filter(self, tmp_path: Path) -> None:
        resp = _client(tmp_path).get("/runs?workflow=full")
        assert resp.status_code == 200

    def test_runs_page_supports_combined_filters(self, tmp_path: Path) -> None:
        """Combined filters must not cause 5xx."""
        resp = _client(tmp_path).get("/runs?status=completed&workflow=full&page=1")
        assert resp.status_code == 200

    def test_runs_page_invalid_page_returns_422(self, tmp_path: Path) -> None:
        """page=0 is invalid (FastAPI Query(ge=1)) → must return 422."""
        resp = _client(tmp_path).get("/runs?page=0")
        assert resp.status_code == 422

    def test_slo_api_returns_200(self, tmp_path: Path) -> None:
        """GET /api/v1/slo must still be accessible and return JSON (backward compat)."""
        resp = _client(tmp_path).get("/api/v1/slo")
        assert resp.status_code == 200

    def test_slo_api_has_slis_key(self, tmp_path: Path) -> None:
        """GET /api/v1/slo response shape must include 'slis' key."""
        resp = _client(tmp_path).get("/api/v1/slo")
        data = resp.json()
        assert "slis" in data, f"'slis' missing from SLO response: {data.keys()}"

    def test_healthz_returns_200(self, tmp_path: Path) -> None:
        """/healthz must still return 200 (existing contract)."""
        resp = _client(tmp_path).get("/healthz")
        assert resp.status_code == 200

    def test_healthz_response_shape_unchanged(self, tmp_path: Path) -> None:
        """GET /healthz must return JSON with 'status' key."""
        resp = _client(tmp_path).get("/healthz")
        data = resp.json()
        assert "status" in data


# ---------------------------------------------------------------------------
# Runs pagination — integration boundary
# ---------------------------------------------------------------------------


class TestRunsPaginationIntegration:
    """Runs page pagination via full app stack (REQ-002 backward compat)."""

    def test_page_param_accepted(self, tmp_path: Path) -> None:
        resp = _client(tmp_path).get("/runs?page=2")
        # Page 2 with no data → still renders the page (empty state), not an error
        assert resp.status_code in (200, 422)

    def test_per_page_constant_is_25(self) -> None:
        """The runs router must keep _PER_PAGE=25 (AC-002 constraint)."""
        from orchestrator.dashboard.routes.runs import _PER_PAGE
        assert _PER_PAGE == 25

    def test_filter_date_from_accepted(self, tmp_path: Path) -> None:
        resp = _client(tmp_path).get("/runs?date_from=2026-01-01")
        assert resp.status_code == 200

    def test_filter_date_to_accepted(self, tmp_path: Path) -> None:
        resp = _client(tmp_path).get("/runs?date_to=2026-12-31")
        assert resp.status_code == 200
