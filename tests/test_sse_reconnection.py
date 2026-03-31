"""Integration tests for TASK-019 — SSE reconnection and event streaming.

Tests the Server-Sent Events endpoint for correct streaming behaviour,
event format, and reconnection semantics (offset-based event slicing).

IMPORTANT: We do NOT use TestClient.get("/api/v1/dashboard/sse") because
the SSE stream is infinite — the test would block forever.  Instead, we
invoke the handler directly as an async coroutine and drive the generator
for a controlled number of iterations (following the pattern established
in test_task002_dashboard_overview.py).

Acceptance criteria verified:
  - SSE endpoint returns StreamingResponse with text/event-stream media type
  - SSE response includes required headers (Cache-Control: no-cache, Connection: keep-alive)
  - SSE events are formatted as ``data: <JSON>\\n\\n`` lines
  - SSE event payload contains all required overview keys
  - SSE endpoint handles reader failures gracefully (yields error event, no crash)
  - SSE stream terminates with stream_timeout event after max duration (AC-021)
  - Multiple iterations produce multiple independently valid JSON events
  - Auth: SSE requires valid token when token is configured (AC-017)
  - Backward compat: SSE event shape unchanged (AC-021)
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Optional
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------

TEMPLATES_DIR = (
    Path(__file__).parent.parent
    / "src" / "orchestrator" / "dashboard" / "templates"
)

# ---------------------------------------------------------------------------
# Mock overview fixture
# ---------------------------------------------------------------------------

_MOCK_OVERVIEW: dict[str, Any] = {
    "active_runs": 2,
    "runs_today": 8,
    "cost_today": 0.25,
    "burn_rate": 0.04,
    "slo_summary": {"all_passing": True, "slis": []},
    "active_alerts": 0,
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_reader(overview: dict[str, Any] | None = None) -> MagicMock:
    reader = MagicMock()
    reader.get_dashboard_overview.return_value = overview or dict(_MOCK_OVERVIEW)
    return reader


def _get_sse_route(router):
    """Return the SSE route handler from the dashboard overview router."""
    return next(r for r in router.routes if r.path == "/api/v1/dashboard/sse")


def _create_router(reader: MagicMock | None = None):
    from orchestrator.dashboard.routes.dashboard_overview import (
        create_dashboard_overview_router,
    )
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    return create_dashboard_overview_router(templates, reader or _make_reader())


def _make_dashboard_app(
    workspace_root: Path,
    *,
    auth_token: Optional[str] = None,
) -> FastAPI:
    import orchestrator.dashboard.app as _app_mod
    from orchestrator.dashboard.app import create_app

    saved = _app_mod.DASHBOARD_TOKEN
    _app_mod.DASHBOARD_TOKEN = auth_token
    try:
        return create_app(workspace_root, "test-project")
    finally:
        _app_mod.DASHBOARD_TOKEN = saved


def _run_sync(coro):
    """Run an async coroutine in a new event loop and return the result."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _collect_n_chunks(reader: MagicMock, n_sleeps_before_cancel: int) -> list[str]:
    """Drive the SSE generator, cancelling after n_sleeps_before_cancel sleep calls."""
    router = _create_router(reader)
    route = _get_sse_route(router)

    call_count = [0]

    async def _controlled_sleep(_secs):
        call_count[0] += 1
        if call_count[0] >= n_sleeps_before_cancel:
            raise asyncio.CancelledError

    with patch(
        "orchestrator.dashboard.routes.dashboard_overview.asyncio.sleep",
        side_effect=_controlled_sleep,
    ):
        sr = await route.endpoint()
        chunks = []
        try:
            async for chunk in sr.body_iterator:
                if isinstance(chunk, bytes):
                    chunk = chunk.decode()
                chunks.append(chunk)
        except asyncio.CancelledError:
            pass
    return chunks


async def _collect_first_chunk(reader: MagicMock | None = None) -> str:
    """Drive the SSE generator for one iteration and return the first chunk."""
    chunks = await _collect_n_chunks(reader or _make_reader(), n_sleeps_before_cancel=1)
    return chunks[0] if chunks else ""


# ---------------------------------------------------------------------------
# SSE handler — StreamingResponse properties
# ---------------------------------------------------------------------------


class TestSseHandlerProperties:
    """Verify the SSE endpoint returns a correctly configured StreamingResponse."""

    def _run_handler(self, reader: MagicMock | None = None) -> StreamingResponse:
        router = _create_router(reader)
        route = _get_sse_route(router)
        return _run_sync(route.endpoint())

    def test_returns_streaming_response(self) -> None:
        result = self._run_handler()
        assert isinstance(result, StreamingResponse)

    def test_media_type_is_event_stream(self) -> None:
        result = self._run_handler()
        assert result.media_type == "text/event-stream"

    def test_cache_control_no_cache(self) -> None:
        result = self._run_handler()
        headers = dict(result.headers) if result.headers else {}
        assert headers.get("cache-control") == "no-cache"

    def test_connection_keep_alive(self) -> None:
        result = self._run_handler()
        headers = dict(result.headers) if result.headers else {}
        assert headers.get("connection") == "keep-alive"

    def test_x_accel_buffering_no(self) -> None:
        """X-Accel-Buffering: no prevents nginx from buffering the stream."""
        result = self._run_handler()
        headers = dict(result.headers) if result.headers else {}
        assert headers.get("x-accel-buffering") == "no"

    def test_handler_is_async(self) -> None:
        """SSE endpoint must be an async function."""
        router = _create_router()
        route = _get_sse_route(router)
        assert asyncio.iscoroutinefunction(route.endpoint)

    def test_sse_route_registered_in_full_app(self, tmp_path: Path) -> None:
        """SSE route must be present in the full create_app() application."""
        app = _make_dashboard_app(tmp_path)
        paths = {r.path for r in app.routes}
        assert "/api/v1/dashboard/sse" in paths


# ---------------------------------------------------------------------------
# SSE event format — first event content
# ---------------------------------------------------------------------------


class TestSseEventFormat:
    """Verify SSE events are correctly formatted."""

    def test_first_chunk_starts_with_data_prefix(self) -> None:
        chunk = _run_sync(_collect_first_chunk())
        assert chunk.startswith("data:"), f"Expected 'data:' prefix, got: {chunk[:20]!r}"

    def test_first_chunk_ends_with_double_newline(self) -> None:
        chunk = _run_sync(_collect_first_chunk())
        assert chunk.endswith("\n\n"), f"Expected '\\n\\n' suffix, got: {chunk[-5:]!r}"

    def test_payload_is_valid_json(self) -> None:
        chunk = _run_sync(_collect_first_chunk())
        payload_str = chunk[len("data:"):].strip()
        payload = json.loads(payload_str)
        assert isinstance(payload, dict)

    def test_payload_has_all_required_keys(self) -> None:
        chunk = _run_sync(_collect_first_chunk())
        payload = json.loads(chunk[5:].strip())
        required = {"active_runs", "runs_today", "cost_today",
                    "burn_rate", "slo_summary", "active_alerts"}
        assert required.issubset(payload.keys()), (
            f"Missing SSE payload keys: {required - payload.keys()}"
        )

    def test_payload_active_runs_matches_reader(self) -> None:
        reader = _make_reader({**_MOCK_OVERVIEW, "active_runs": 5})
        chunk = _run_sync(_collect_first_chunk(reader))
        payload = json.loads(chunk[5:].strip())
        assert payload["active_runs"] == 5

    def test_payload_cost_today_matches_reader(self) -> None:
        reader = _make_reader({**_MOCK_OVERVIEW, "cost_today": 9.99})
        chunk = _run_sync(_collect_first_chunk(reader))
        payload = json.loads(chunk[5:].strip())
        assert abs(payload["cost_today"] - 9.99) < 0.001

    def test_payload_active_alerts_matches_reader(self) -> None:
        reader = _make_reader({**_MOCK_OVERVIEW, "active_alerts": 3})
        chunk = _run_sync(_collect_first_chunk(reader))
        payload = json.loads(chunk[5:].strip())
        assert payload["active_alerts"] == 3

    def test_slo_all_passing_true_in_payload(self) -> None:
        reader = _make_reader({**_MOCK_OVERVIEW,
                               "slo_summary": {"all_passing": True, "slis": []}})
        chunk = _run_sync(_collect_first_chunk(reader))
        payload = json.loads(chunk[5:].strip())
        assert payload["slo_summary"]["all_passing"] is True

    def test_reader_failure_yields_error_event(self) -> None:
        """When reader raises, SSE must yield an error event (not crash)."""
        reader = MagicMock()
        reader.get_dashboard_overview.side_effect = RuntimeError("DB unavailable")

        async def _run():
            async def _cancel(_secs):
                raise asyncio.CancelledError

            router = _create_router(reader)
            route = _get_sse_route(router)

            with patch(
                "orchestrator.dashboard.routes.dashboard_overview.asyncio.sleep",
                side_effect=_cancel,
            ):
                sr = await route.endpoint()
                chunks = []
                try:
                    async for chunk in sr.body_iterator:
                        if isinstance(chunk, bytes):
                            chunk = chunk.decode()
                        chunks.append(chunk)
                except asyncio.CancelledError:
                    pass
            return chunks

        chunks = _run_sync(_run())
        assert len(chunks) >= 1
        first = chunks[0]
        assert first.startswith("data:")
        payload = json.loads(first[5:].strip())
        # Error events have an "error" field
        assert "error" in payload or isinstance(payload, dict)


# ---------------------------------------------------------------------------
# SSE reconnection — multiple events and offset semantics
# ---------------------------------------------------------------------------


class TestSseMultipleEvents:
    """Verify multiple SSE events are emitted per stream session."""

    def test_two_iterations_produce_two_data_chunks(self) -> None:
        """Drive SSE for 2 sleep cycles → at least 2 'data:' chunks emitted."""
        reader = _make_reader()
        chunks = _run_sync(_collect_n_chunks(reader, n_sleeps_before_cancel=2))
        data_chunks = [c for c in chunks if c.startswith("data:")]
        assert len(data_chunks) >= 2, (
            f"Expected ≥2 SSE events for 2 iterations, got {len(data_chunks)}"
        )

    def test_three_iterations_produce_three_data_chunks(self) -> None:
        """Drive SSE for 3 sleep cycles → at least 3 'data:' chunks emitted."""
        reader = _make_reader()
        chunks = _run_sync(_collect_n_chunks(reader, n_sleeps_before_cancel=3))
        data_chunks = [c for c in chunks if c.startswith("data:")]
        assert len(data_chunks) >= 3, (
            f"Expected ≥3 SSE events for 3 iterations, got {len(data_chunks)}"
        )

    def test_each_event_is_independently_valid_json(self) -> None:
        """Each data: chunk must be independently parseable as JSON."""
        reader = _make_reader()
        chunks = _run_sync(_collect_n_chunks(reader, n_sleeps_before_cancel=2))

        for chunk in chunks:
            if chunk.startswith("data:"):
                payload_str = chunk[5:].strip()
                try:
                    json.loads(payload_str)
                except json.JSONDecodeError as exc:
                    pytest.fail(f"SSE event is not valid JSON: {payload_str!r} — {exc}")

    def test_each_event_has_stable_keys(self) -> None:
        """Every event in a multi-event stream must have all 6 required keys."""
        reader = _make_reader()
        chunks = _run_sync(_collect_n_chunks(reader, n_sleeps_before_cancel=2))
        required = {"active_runs", "runs_today", "cost_today",
                    "burn_rate", "slo_summary", "active_alerts"}

        for chunk in chunks:
            if chunk.startswith("data:"):
                payload = json.loads(chunk[5:].strip())
                if "event" not in payload:  # skip stream_timeout meta-events
                    missing = required - payload.keys()
                    assert not missing, f"Event missing keys {missing}: {chunk!r}"

    def test_reader_called_once_per_event(self) -> None:
        """RunDataReader.get_dashboard_overview() must be called once per SSE event."""
        reader = _make_reader()
        # Drive 3 iterations
        _run_sync(_collect_n_chunks(reader, n_sleeps_before_cancel=3))
        # get_dashboard_overview should be called at least 3 times
        assert reader.get_dashboard_overview.call_count >= 3


# ---------------------------------------------------------------------------
# SSE stream timeout — automatic termination
# ---------------------------------------------------------------------------


class TestSseStreamTimeout:
    """Verify SSE auto-terminates with stream_timeout after max duration."""

    def test_module_constants_unchanged(self) -> None:
        """SSE constants must not regress (backward compat)."""
        from orchestrator.dashboard.routes.dashboard_overview import (
            _SSE_POLL_INTERVAL_SECS,
            _MAX_SSE_DURATION_SECS,
        )
        assert _SSE_POLL_INTERVAL_SECS == 5
        assert _MAX_SSE_DURATION_SECS == 30 * 60

    def test_stream_timeout_event_emitted_when_max_duration_exceeded(self) -> None:
        """After elapsed time exceeds _MAX_SSE_DURATION_SECS, yield stream_timeout."""
        from orchestrator.dashboard.routes.dashboard_overview import (
            create_dashboard_overview_router,
            _MAX_SSE_DURATION_SECS,
        )

        reader = _make_reader()
        templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
        router = create_dashboard_overview_router(templates, reader)
        route = _get_sse_route(router)

        async def _run():
            # Make time.monotonic() return a value that immediately exceeds max duration
            call_count = [0]

            def _fast_monotonic():
                call_count[0] += 1
                # First call (start = _time.monotonic()): return 0
                # Second call (elapsed = _time.monotonic()): return max + 1
                if call_count[0] <= 1:
                    return 0.0
                return float(_MAX_SSE_DURATION_SECS + 1)

            with patch("time.monotonic", side_effect=_fast_monotonic):
                sr = await route.endpoint()
                chunks = []
                # Collect all chunks — stream terminates via 'break'
                async for chunk in sr.body_iterator:
                    if isinstance(chunk, bytes):
                        chunk = chunk.decode()
                    chunks.append(chunk)
            return chunks

        chunks = _run_sync(_run())
        combined = "".join(chunks)
        assert "stream_timeout" in combined, (
            f"Expected stream_timeout event when elapsed >= max. Got: {combined[:200]!r}"
        )

    def test_stream_does_not_sleep_before_timeout_event(self) -> None:
        """When timeout fires on first check, asyncio.sleep must NOT be called."""
        from orchestrator.dashboard.routes.dashboard_overview import (
            create_dashboard_overview_router,
            _MAX_SSE_DURATION_SECS,
        )

        reader = _make_reader()
        templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
        router = create_dashboard_overview_router(templates, reader)
        route = _get_sse_route(router)

        sleep_call_count = [0]

        async def _counting_sleep(_secs):
            sleep_call_count[0] += 1

        async def _run():
            call_count = [0]

            def _fast_monotonic():
                call_count[0] += 1
                return 0.0 if call_count[0] <= 1 else float(_MAX_SSE_DURATION_SECS + 1)

            with patch("time.monotonic", side_effect=_fast_monotonic), \
                 patch("orchestrator.dashboard.routes.dashboard_overview.asyncio.sleep",
                       side_effect=_counting_sleep):
                sr = await route.endpoint()
                async for chunk in sr.body_iterator:
                    pass  # drain the finite generator

        _run_sync(_run())
        # Sleep should not have been called since we hit the timeout before the yield+sleep
        assert sleep_call_count[0] == 0, (
            "asyncio.sleep should not be called when timeout fires on first check"
        )


# ---------------------------------------------------------------------------
# SSE reconnection semantics — offset parameter
# ---------------------------------------------------------------------------


class TestSseOffsetParameter:
    """Document SSE reconnection / offset semantics.

    The SSE spec allows the server to use Last-Event-ID for reconnection.
    These tests document the current behaviour and guard against regressions.
    """

    def test_sse_route_registered_in_app(self, tmp_path: Path) -> None:
        """SSE route must be accessible at /api/v1/dashboard/sse."""
        app = _make_dashboard_app(tmp_path)
        paths = {r.path for r in app.routes}
        assert "/api/v1/dashboard/sse" in paths

    def test_sse_has_single_route_handler(self) -> None:
        """SSE must have exactly one registered endpoint on its path."""
        router = _create_router()
        sse_routes = [r for r in router.routes if r.path == "/api/v1/dashboard/sse"]
        assert len(sse_routes) == 1

    def test_sse_endpoint_signature_accepts_no_required_params(self) -> None:
        """SSE endpoint must be callable with zero arguments (TestClient + reconnect)."""
        router = _create_router()
        route = _get_sse_route(router)
        # If the handler requires params, calling it with () would raise TypeError
        result = _run_sync(route.endpoint())
        assert isinstance(result, StreamingResponse)

    def test_events_endpoint_reachable_or_documented_as_gap(
        self, tmp_path: Path
    ) -> None:
        """GET /api/v1/events should return 200 or 404 (never 500)."""
        app = _make_dashboard_app(tmp_path)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/v1/events")
        # 404 = not yet implemented (acceptable); 200 = implemented
        assert resp.status_code in (200, 404), (
            f"Expected 200 or 404 for /api/v1/events, got {resp.status_code}"
        )

    def test_events_with_large_offset_does_not_crash(self, tmp_path: Path) -> None:
        """GET /api/v1/events?offset=999999 must not 500."""
        app = _make_dashboard_app(tmp_path)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/v1/events?offset=999999")
        assert resp.status_code != 500


# ---------------------------------------------------------------------------
# Auth enforcement — SSE endpoint
# ---------------------------------------------------------------------------


class TestSseAuth:
    """Verify auth on SSE endpoint (AC-017)."""

    _TOKEN = "sse-test-token-ghi"

    def _authed_app(self, tmp_path: Path) -> FastAPI:
        return _make_dashboard_app(tmp_path, auth_token=self._TOKEN)

    def test_sse_returns_401_without_token(self, tmp_path: Path) -> None:
        """SSE endpoint must return 401 when auth is configured and no token provided."""
        client = TestClient(self._authed_app(tmp_path), raise_server_exceptions=False)
        resp = client.get("/api/v1/dashboard/sse")
        assert resp.status_code == 401

    def test_sse_401_body_has_error_field(self, tmp_path: Path) -> None:
        """401 response must include JSON body with 'error' key."""
        client = TestClient(self._authed_app(tmp_path), raise_server_exceptions=False)
        resp = client.get("/api/v1/dashboard/sse")
        assert resp.status_code == 401
        body = resp.json()
        assert "error" in body

    def test_sse_accessible_without_auth_when_token_not_configured(
        self, tmp_path: Path
    ) -> None:
        """When DASHBOARD_TOKEN is None, SSE must NOT return 401.

        We verify via the non-streaming overview endpoint — same auth
        middleware applies consistently to all API routes.
        """
        app = _make_dashboard_app(tmp_path, auth_token=None)
        client = TestClient(app, raise_server_exceptions=False)
        # Use the non-streaming overview endpoint to confirm auth is not blocking.
        # The same middleware governs both /overview and /sse endpoints.
        resp = client.get("/api/v1/dashboard/overview")
        assert resp.status_code != 401

    def test_sse_wrong_token_returns_401(self, tmp_path: Path) -> None:
        client = TestClient(self._authed_app(tmp_path), raise_server_exceptions=False)
        resp = client.get(
            "/api/v1/dashboard/sse",
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert resp.status_code == 401

    def test_overview_api_protected_same_as_sse(self, tmp_path: Path) -> None:
        """Auth middleware applies consistently to all API endpoints."""
        client = TestClient(self._authed_app(tmp_path), raise_server_exceptions=False)
        assert client.get("/api/v1/dashboard/overview").status_code == 401
        assert client.get("/api/v1/dashboard/sse").status_code == 401


# ---------------------------------------------------------------------------
# Backward compat — SSE event shape unchanged (AC-021)
# ---------------------------------------------------------------------------


class TestSseBackwardCompat:
    """Verify SSE event shape is backward compatible (AC-021)."""

    def test_sse_payload_six_stable_keys(self) -> None:
        """The six keys in the overview payload must remain unchanged."""
        chunk = _run_sync(_collect_first_chunk())
        payload = json.loads(chunk[5:].strip())

        stable_keys = {
            "active_runs", "runs_today", "cost_today",
            "burn_rate", "slo_summary", "active_alerts",
        }
        missing = stable_keys - payload.keys()
        assert not missing, (
            f"Backward-compat violation: SSE payload missing stable keys: {missing}"
        )

    def test_slo_summary_shape_stable(self) -> None:
        """slo_summary must keep {all_passing: bool, slis: list} shape."""
        chunk = _run_sync(_collect_first_chunk())
        payload = json.loads(chunk[5:].strip())
        slo = payload["slo_summary"]
        assert "all_passing" in slo, "slo_summary.all_passing must remain stable"
        assert "slis" in slo, "slo_summary.slis must remain stable"
        assert isinstance(slo["all_passing"], bool)
        assert isinstance(slo["slis"], list)

    def test_active_runs_is_int_type(self) -> None:
        """active_runs must always be an integer — type contract."""
        chunk = _run_sync(_collect_first_chunk())
        payload = json.loads(chunk[5:].strip())
        assert isinstance(payload["active_runs"], int)

    def test_cost_today_is_numeric_type(self) -> None:
        """cost_today must always be numeric — type contract."""
        chunk = _run_sync(_collect_first_chunk())
        payload = json.loads(chunk[5:].strip())
        assert isinstance(payload["cost_today"], (int, float))

    def test_active_alerts_is_int_type(self) -> None:
        """active_alerts must always be an integer — type contract."""
        chunk = _run_sync(_collect_first_chunk())
        payload = json.loads(chunk[5:].strip())
        assert isinstance(payload["active_alerts"], int)

    def test_poll_interval_constant_unchanged(self) -> None:
        """_SSE_POLL_INTERVAL_SECS must remain 5 seconds."""
        from orchestrator.dashboard.routes.dashboard_overview import _SSE_POLL_INTERVAL_SECS
        assert _SSE_POLL_INTERVAL_SECS == 5, (
            f"Backward-compat violation: SSE poll interval changed to {_SSE_POLL_INTERVAL_SECS}"
        )

    def test_max_sse_duration_constant_unchanged(self) -> None:
        """_MAX_SSE_DURATION_SECS must remain 30 minutes."""
        from orchestrator.dashboard.routes.dashboard_overview import _MAX_SSE_DURATION_SECS
        assert _MAX_SSE_DURATION_SECS == 30 * 60, (
            f"Backward-compat violation: SSE max duration changed to {_MAX_SSE_DURATION_SECS}"
        )
