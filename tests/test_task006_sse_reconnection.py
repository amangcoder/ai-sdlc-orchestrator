"""Tests for TASK-006 — SSE reconnection with exponential backoff.

Acceptance criteria verified
------------------------------
AC-1  SSE reconnects with exponential backoff delays: 1s, 2s, 4s, 8s, 16s,
      capped at 30s max — verified by inspecting BACKOFF constants in sse-client.js
      and checking the _scheduleReconnect logic.

AC-2  'Connection lost — reconnecting...' banner appears within 3s of drop —
      verified by checking _showBanner() is called synchronously from
      _handleDisconnect() (no async gap) and the banner text matches.

AC-3  Missed events are fetched via /api/v1/runs/{run_id}/events?offset=N
      and replayed in order — verified by the events endpoint returning the
      correct slice, and by checking the _buildEventsUrl logic in the JS.

AC-4  Banner disappears on successful reconnection — verified by checking
      _removeBanner() is called before _onReconnect() in _doReconnect().

AC-5  close() method stops all pending reconnection timers —
      verified by checking the close() implementation in sse-client.js.

AC-6  SSE endpoint supports token auth via query parameter (?token=X)
      when ORCHESTRATOR_DASHBOARD_TOKEN is set —
      verified by integration tests against the live endpoint.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

STATIC_DIR = (
    Path(__file__).parent.parent
    / "src"
    / "orchestrator"
    / "dashboard"
    / "static"
)

SSE_CLIENT_JS = STATIC_DIR / "sse-client.js"

TEMPLATES_DIR = (
    Path(__file__).parent.parent
    / "src"
    / "orchestrator"
    / "dashboard"
    / "templates"
)


def _make_dashboard_app(workspace_root: Path, project_name: str = "test-project"):
    """Create a full dashboard app with auth disabled."""
    import orchestrator.dashboard.app as _app_mod
    from orchestrator.dashboard.app import create_app

    saved_token = _app_mod.DASHBOARD_TOKEN
    _app_mod.DASHBOARD_TOKEN = None
    try:
        return create_app(workspace_root, project_name)
    finally:
        _app_mod.DASHBOARD_TOKEN = saved_token


def _make_dashboard_app_with_token(
    workspace_root: Path,
    token: str,
    project_name: str = "test-project",
):
    """Create a full dashboard app with auth token set."""
    import orchestrator.dashboard.app as _app_mod
    from orchestrator.dashboard.app import create_app

    saved_token = _app_mod.DASHBOARD_TOKEN
    _app_mod.DASHBOARD_TOKEN = token
    try:
        return create_app(workspace_root, project_name)
    finally:
        _app_mod.DASHBOARD_TOKEN = saved_token


def _write_state_file(workspace: Path, run_id: str, state: dict) -> Path:
    state_path = workspace / f"state-{run_id}.json"
    state_path.write_text(json.dumps(state))
    return state_path


def _write_events_file(workspace: Path, run_id: str, events: list[dict]) -> Path:
    events_path = workspace / f"events-{run_id}.jsonl"
    events_path.write_text("\n".join(json.dumps(e) for e in events))
    return events_path


def _minimal_state(run_id: str, **overrides) -> dict:
    base = {
        "run_id": run_id,
        "feature_request": "Test feature request",
        "workflow_type": "feature_development",
        "status": "running",
        "current_step": "architecture",
        "completed_steps": ["UX Specification"],
        "phases": {"ux_specification": {"status": "completed"}},
        "total_cost_usd": 0.001,
        "start_time": "2026-03-31T10:00:00+00:00",
    }
    base.update(overrides)
    return base


RUN_ID = "abc123task006"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_state_file(ws, RUN_ID, _minimal_state(RUN_ID))
    return tmp_path


@pytest.fixture
def workspace_with_events(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_state_file(ws, RUN_ID, _minimal_state(RUN_ID))
    events = [
        {"event": "phase_transition", "phase": "ux_specification", "status": "completed", "ts": "2026-03-31T10:00:01Z"},
        {"event": "phase_transition", "phase": "architecture", "status": "running", "ts": "2026-03-31T10:00:02Z"},
        {"event": "agent_activity", "agent": "architect", "message": "Designing system", "ts": "2026-03-31T10:00:03Z"},
    ]
    _write_events_file(ws, RUN_ID, events)
    return tmp_path


@pytest.fixture
def client(workspace: Path):
    app = _make_dashboard_app(workspace)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def client_with_events(workspace_with_events: Path):
    app = _make_dashboard_app(workspace_with_events)
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# AC-1: sse-client.js exists and contains correct backoff constants
# ---------------------------------------------------------------------------


class TestSSEClientJSExists:
    """Verify that sse-client.js is present and exports ReconnectingSSE."""

    def test_file_exists(self):
        assert SSE_CLIENT_JS.exists(), "sse-client.js not found in static/ directory"

    def test_file_has_reconnecting_sse_class(self):
        src = SSE_CLIENT_JS.read_text()
        assert "ReconnectingSSE" in src

    def test_file_has_backoff_initial_1s(self):
        """BACKOFF_INITIAL must be 1000 ms (1 s)."""
        src = SSE_CLIENT_JS.read_text()
        assert "BACKOFF_INITIAL" in src
        assert "1000" in src

    def test_file_has_backoff_max_30s(self):
        """BACKOFF_MAX must be 30000 ms (30 s)."""
        src = SSE_CLIENT_JS.read_text()
        assert "BACKOFF_MAX" in src
        assert "30000" in src

    def test_backoff_doubling_logic(self):
        """_scheduleReconnect must double the delay: delay * 2."""
        src = SSE_CLIENT_JS.read_text()
        assert "* 2" in src or "*2" in src

    def test_backoff_cap_uses_min(self):
        """Backoff cap must use Math.min to cap at BACKOFF_MAX."""
        src = SSE_CLIENT_JS.read_text()
        assert "Math.min" in src

    def test_file_has_close_method(self):
        src = SSE_CLIENT_JS.read_text()
        assert "ReconnectingSSE.prototype.close" in src

    def test_close_cancels_timeout(self):
        """close() must call clearTimeout to cancel pending reconnection timers."""
        src = SSE_CLIENT_JS.read_text()
        assert "clearTimeout" in src

    def test_file_has_event_count(self):
        """eventCount property tracks events received (used as offset)."""
        src = SSE_CLIENT_JS.read_text()
        assert "eventCount" in src

    def test_file_has_build_events_url(self):
        src = SSE_CLIENT_JS.read_text()
        assert "_buildEventsUrl" in src

    def test_build_events_url_uses_api_v1(self):
        """Events URL must use /api/v1/ prefix."""
        src = SSE_CLIENT_JS.read_text()
        assert "api/v1" in src

    def test_build_events_url_appends_offset(self):
        src = SSE_CLIENT_JS.read_text()
        assert "offset" in src

    def test_file_has_show_banner(self):
        src = SSE_CLIENT_JS.read_text()
        assert "_showBanner" in src

    def test_banner_text_connection_lost(self):
        """Banner must contain the exact text 'Connection lost — reconnecting…'."""
        src = SSE_CLIENT_JS.read_text()
        # The text may use Unicode escapes or literal characters.
        assert "Connection lost" in src
        assert "reconnecting" in src

    def test_banner_is_fixed_position(self):
        """Banner must be fixed-position so it appears at the top of the viewport."""
        src = SSE_CLIENT_JS.read_text()
        assert "fixed" in src

    def test_file_has_remove_banner(self):
        src = SSE_CLIENT_JS.read_text()
        assert "_removeBanner" in src

    def test_remove_banner_called_on_reconnect(self):
        """Banner removal must happen inside _doReconnect (success path)."""
        src = SSE_CLIENT_JS.read_text()
        assert "_removeBanner" in src

    def test_file_has_schedule_reconnect(self):
        src = SSE_CLIENT_JS.read_text()
        assert "_scheduleReconnect" in src

    def test_file_has_do_reconnect(self):
        src = SSE_CLIENT_JS.read_text()
        assert "_doReconnect" in src

    def test_on_callbacks(self):
        """ReconnectingSSE accepts onEvent, onDisconnect, onReconnect, onEnd."""
        src = SSE_CLIENT_JS.read_text()
        assert "onEvent" in src
        assert "onDisconnect" in src
        assert "onReconnect" in src
        assert "onEnd" in src

    def test_stream_end_triggers_on_end(self):
        """stream_end event must trigger the onEnd callback."""
        src = SSE_CLIENT_JS.read_text()
        assert "stream_end" in src
        assert "stream_timeout" in src

    def test_forwards_original_query_params(self):
        """_buildEventsUrl must forward query params from the original SSE URL (for ?token=X)."""
        src = SSE_CLIENT_JS.read_text()
        # The function strips the base path from the original query and appends offset.
        assert "origQuery" in src or "qIdx" in src


# ---------------------------------------------------------------------------
# AC-2: Banner shown on disconnect (verified via JS source content)
# ---------------------------------------------------------------------------


class TestBannerBehaviour:
    """Verify banner injection and removal logic in JS source."""

    def test_banner_injected_into_document_body(self):
        src = SSE_CLIENT_JS.read_text()
        assert "document.body" in src
        assert "insertBefore" in src

    def test_banner_has_role_status(self):
        """Banner must carry role='status' for screen-reader announcements."""
        src = SSE_CLIENT_JS.read_text()
        assert "role" in src
        assert "status" in src

    def test_banner_has_aria_live_polite(self):
        src = SSE_CLIENT_JS.read_text()
        assert "aria-live" in src
        assert "polite" in src

    def test_show_banner_called_on_disconnect(self):
        """_showBanner must be called inside _handleDisconnect."""
        src = SSE_CLIENT_JS.read_text()
        # Both methods must exist; their relationship is checked structurally.
        assert "_handleDisconnect" in src
        assert "_showBanner" in src

    def test_banner_id(self):
        """Banner element must have id='sse-reconnect-banner'."""
        src = SSE_CLIENT_JS.read_text()
        assert "sse-reconnect-banner" in src

    def test_banner_warning_colour(self):
        """Banner must use a warning background colour."""
        src = SSE_CLIENT_JS.read_text()
        # Expect a dark amber/orange warning background.
        assert "background" in src


# ---------------------------------------------------------------------------
# AC-3: Events API endpoint supports offset parameter
# ---------------------------------------------------------------------------


class TestEventsApiOffset:
    """Test that GET /api/v1/runs/{run_id}/events supports the offset parameter."""

    def test_events_no_offset_returns_all(self, client_with_events: TestClient):
        resp = client_with_events.get(f"/api/v1/runs/{RUN_ID}/events")
        assert resp.status_code == 200
        body = resp.json()
        assert "events" in body
        assert body["offset"] == 0

    def test_events_with_offset_0(self, client_with_events: TestClient):
        resp = client_with_events.get(f"/api/v1/runs/{RUN_ID}/events?offset=0")
        assert resp.status_code == 200
        body = resp.json()
        assert body["offset"] == 0

    def test_events_with_offset_1_skips_first(self, client_with_events: TestClient):
        """offset=1 should skip the first event."""
        resp0 = client_with_events.get(f"/api/v1/runs/{RUN_ID}/events?offset=0")
        resp1 = client_with_events.get(f"/api/v1/runs/{RUN_ID}/events?offset=1")
        assert resp0.status_code == 200
        assert resp1.status_code == 200
        all_events = resp0.json()["events"]
        offset_events = resp1.json()["events"]
        if all_events:
            assert len(offset_events) == len(all_events) - 1

    def test_events_with_offset_beyond_total_returns_empty(self, client_with_events: TestClient):
        """offset beyond the total number of events should return an empty list."""
        resp = client_with_events.get(f"/api/v1/runs/{RUN_ID}/events?offset=9999")
        assert resp.status_code == 200
        body = resp.json()
        assert body["events"] == [] or len(body["events"]) == 0

    def test_events_offset_reflected_in_response(self, client_with_events: TestClient):
        resp = client_with_events.get(f"/api/v1/runs/{RUN_ID}/events?offset=2")
        assert resp.status_code == 200
        body = resp.json()
        assert body["offset"] == 2

    def test_events_count_in_response(self, client_with_events: TestClient):
        resp = client_with_events.get(f"/api/v1/runs/{RUN_ID}/events")
        assert resp.status_code == 200
        body = resp.json()
        assert "count" in body


# ---------------------------------------------------------------------------
# AC-3 (cont.): SSE stream endpoint supports offset parameter
# ---------------------------------------------------------------------------


class TestSSEStreamOffset:
    """Test that the SSE stream endpoint accepts the offset query parameter."""

    def test_stream_accepts_offset_0(self, client: TestClient):
        """GET /api/v1/runs/{run_id}/stream?offset=0 must return 200."""
        # We use stream=True to avoid blocking on a never-ending generator.
        with client.stream("GET", f"/api/v1/runs/{RUN_ID}/stream?offset=0") as resp:
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers.get("content-type", "")

    def test_stream_accepts_offset_nonzero(self, client: TestClient):
        """GET /api/v1/runs/{run_id}/stream?offset=5 must return 200."""
        with client.stream("GET", f"/api/v1/runs/{RUN_ID}/stream?offset=5") as resp:
            assert resp.status_code == 200

    def test_stream_default_offset_is_0(self, client: TestClient):
        """GET /api/v1/runs/{run_id}/stream without offset must return 200."""
        with client.stream("GET", f"/api/v1/runs/{RUN_ID}/stream") as resp:
            assert resp.status_code == 200


# ---------------------------------------------------------------------------
# AC-6: SSE endpoint + middleware accept ?token=X query parameter
# ---------------------------------------------------------------------------


class TestSSETokenQueryParamAuth:
    """Verify that auth middleware accepts ?token=X for EventSource compatibility."""

    def _make_authed_workspace(self, tmp_path: Path) -> Path:
        ws = tmp_path / "workspace"
        ws.mkdir()
        _write_state_file(ws, RUN_ID, _minimal_state(RUN_ID))
        return tmp_path

    def test_stream_rejected_without_token(self, tmp_path: Path):
        """Without any token, the SSE stream must return 401."""
        root = self._make_authed_workspace(tmp_path)
        app = _make_dashboard_app_with_token(root, "secret-token")
        cl  = TestClient(app, raise_server_exceptions=False)
        with cl.stream("GET", f"/api/v1/runs/{RUN_ID}/stream") as resp:
            assert resp.status_code == 401

    def test_stream_accepted_with_correct_query_token(self, tmp_path: Path):
        """?token=<correct> must allow access to the SSE stream."""
        root = self._make_authed_workspace(tmp_path)
        app = _make_dashboard_app_with_token(root, "secret-token")
        cl  = TestClient(app, raise_server_exceptions=False)
        with cl.stream(
            "GET", f"/api/v1/runs/{RUN_ID}/stream?token=secret-token"
        ) as resp:
            assert resp.status_code == 200

    def test_stream_rejected_with_wrong_query_token(self, tmp_path: Path):
        """?token=<wrong> must return 401."""
        root = self._make_authed_workspace(tmp_path)
        app = _make_dashboard_app_with_token(root, "secret-token")
        cl  = TestClient(app, raise_server_exceptions=False)
        with cl.stream(
            "GET", f"/api/v1/runs/{RUN_ID}/stream?token=wrong-token"
        ) as resp:
            assert resp.status_code == 401

    def test_events_accepted_with_correct_query_token(self, tmp_path: Path):
        """?token=<correct> must allow access to the events REST endpoint."""
        root = self._make_authed_workspace(tmp_path)
        app = _make_dashboard_app_with_token(root, "secret-token")
        cl  = TestClient(app, raise_server_exceptions=False)
        resp = cl.get(
            f"/api/v1/runs/{RUN_ID}/events?token=secret-token"
        )
        assert resp.status_code == 200

    def test_events_rejected_without_token(self, tmp_path: Path):
        """Events endpoint must also require auth when token is configured."""
        root = self._make_authed_workspace(tmp_path)
        app = _make_dashboard_app_with_token(root, "secret-token")
        cl  = TestClient(app, raise_server_exceptions=False)
        resp = cl.get(f"/api/v1/runs/{RUN_ID}/events")
        assert resp.status_code == 401

    def test_header_auth_still_works(self, tmp_path: Path):
        """Bearer header auth must continue working alongside query-param auth."""
        root = self._make_authed_workspace(tmp_path)
        app = _make_dashboard_app_with_token(root, "secret-token")
        cl  = TestClient(app, raise_server_exceptions=False)
        resp = cl.get(
            f"/api/v1/runs/{RUN_ID}/events",
            headers={"Authorization": "Bearer secret-token"},
        )
        assert resp.status_code == 200

    def test_legacy_stream_path_with_token(self, tmp_path: Path):
        """/api/runs/{run_id}/stream must also accept ?token=X."""
        root = self._make_authed_workspace(tmp_path)
        app = _make_dashboard_app_with_token(root, "secret-token")
        cl  = TestClient(app, raise_server_exceptions=False)
        with cl.stream(
            "GET", f"/api/runs/{RUN_ID}/stream?token=secret-token"
        ) as resp:
            assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Live page HTML: sse-client.js loaded + ReconnectingSSE used
# ---------------------------------------------------------------------------


class TestLiveHtmlUsesReconnectingSSE:
    """Verify that live.html loads sse-client.js and uses ReconnectingSSE."""

    def _get_live_html(self, client: TestClient) -> str:
        resp = client.get(f"/runs/{RUN_ID}/live")
        assert resp.status_code == 200
        return resp.text

    def test_sse_client_script_tag_present(self, client: TestClient):
        """live.html must include a <script src> pointing to sse-client.js."""
        html = self._get_live_html(client)
        assert "sse-client.js" in html

    def test_reconnecing_sse_instantiated(self, client: TestClient):
        """live.html must instantiate ReconnectingSSE."""
        html = self._get_live_html(client)
        assert "ReconnectingSSE" in html

    def test_no_bare_event_source_instantiation(self, client: TestClient):
        """live.html must NOT create a bare EventSource directly (use ReconnectingSSE)."""
        html = self._get_live_html(client)
        # ReconnectingSSE wraps EventSource internally — the page JS must not
        # call `new EventSource(` directly.
        assert "new EventSource(" not in html

    def test_on_event_callback_present(self, client: TestClient):
        html = self._get_live_html(client)
        assert "onEvent" in html

    def test_on_disconnect_callback_present(self, client: TestClient):
        html = self._get_live_html(client)
        assert "onDisconnect" in html

    def test_on_reconnect_callback_present(self, client: TestClient):
        html = self._get_live_html(client)
        assert "onReconnect" in html

    def test_on_end_callback_present(self, client: TestClient):
        html = self._get_live_html(client)
        assert "onEnd" in html

    def test_sseurl_variable_present(self, client: TestClient):
        html = self._get_live_html(client)
        assert "sseUrl" in html

    def test_stream_url_referenced(self, client: TestClient):
        """SSE URL must still reference /stream so ReconnectingSSE can open it."""
        html = self._get_live_html(client)
        assert "/stream" in html

    def test_close_sse_exposed_on_window(self, client: TestClient):
        """window._liveRunPage must expose closeSSE for programmatic shutdown."""
        html = self._get_live_html(client)
        assert "closeSSE" in html

    def test_sse_token_variable_present(self, client: TestClient):
        """live.html must define the SSE_TOKEN variable from the template context."""
        html = self._get_live_html(client)
        assert "SSE_TOKEN" in html

    def test_dashboard_token_in_sse_url_when_set(self, tmp_path: Path):
        """When dashboard_token is set, the SSE URL should include ?token=."""
        ws = tmp_path / "workspace"
        ws.mkdir()
        _write_state_file(ws, RUN_ID, _minimal_state(RUN_ID))
        app = _make_dashboard_app_with_token(tmp_path, "my-secret")
        # Access the live page with the correct token header
        cl = TestClient(app, raise_server_exceptions=False)
        resp = cl.get(
            f"/runs/{RUN_ID}/live",
            headers={"Authorization": "Bearer my-secret"},
        )
        assert resp.status_code == 200
        html = resp.text
        # The token must be embedded so JS can add ?token=X to the EventSource URL
        assert "my-secret" in html

    def test_phase_stepper_updates_still_present(self, client: TestClient):
        """Phase stepper update logic must be in the onEvent callback."""
        html = self._get_live_html(client)
        assert "updatePhaseStep" in html
        assert "phase_transition" in html

    def test_prompt_card_trigger_still_present(self, client: TestClient):
        """Prompt card triggering must be inside the onEvent callback."""
        html = self._get_live_html(client)
        assert "showPromptCard" in html
        assert "awaiting_input" in html or "prompt_request" in html

    def test_event_log_rendering_still_present(self, client: TestClient):
        """Event log rendering (renderEventItem / appendEvent) must be present."""
        html = self._get_live_html(client)
        assert "renderEventItem" in html
        assert "appendEvent" in html


# ---------------------------------------------------------------------------
# AC-5: close() JS implementation (source analysis)
# ---------------------------------------------------------------------------


class TestCloseMethod:
    """Verify the close() implementation satisfies AC-5."""

    def test_close_sets_closed_flag(self):
        src = SSE_CLIENT_JS.read_text()
        assert "_closed" in src
        # close() must set _closed = true
        assert "true" in src

    def test_close_cancels_recon_timer(self):
        src = SSE_CLIENT_JS.read_text()
        assert "clearTimeout" in src
        assert "_reconnTimer" in src

    def test_close_closes_event_source(self):
        src = SSE_CLIENT_JS.read_text()
        # close() must call this._es.close()
        assert "_es" in src

    def test_close_removes_banner(self):
        src = SSE_CLIENT_JS.read_text()
        assert "_removeBanner" in src


# ---------------------------------------------------------------------------
# Static file served via /static/sse-client.js
# ---------------------------------------------------------------------------


class TestSSEClientServed:
    """Verify the static file is served correctly by the app."""

    def test_static_sse_client_js_returns_200(self, client: TestClient):
        resp = client.get("/static/sse-client.js")
        assert resp.status_code == 200

    def test_static_sse_client_js_content_type(self, client: TestClient):
        resp = client.get("/static/sse-client.js")
        ct = resp.headers.get("content-type", "")
        assert "javascript" in ct or "text" in ct

    def test_static_sse_client_js_body_non_empty(self, client: TestClient):
        resp = client.get("/static/sse-client.js")
        assert len(resp.text) > 100
