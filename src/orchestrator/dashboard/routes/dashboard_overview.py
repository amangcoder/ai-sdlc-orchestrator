"""Dashboard overview page, JSON API, and SSE endpoint.

Endpoints
---------
GET /api/v1/dashboard/overview
    JSON snapshot of the consolidated real-time KPIs returned by
    ``RunDataReader.get_dashboard_overview()``.

GET /dashboard
    HTML page with four KPI cards (Active Runs, Runs Today, Cost Today,
    Burn Rate), an SLO compliance summary row, and an active-alert count
    card.  KPI values auto-update via the SSE endpoint below.

GET /api/v1/dashboard/sse
    Server-sent event stream that pushes a JSON overview payload every 5
    seconds.  Client subscribes via ``EventSource`` and patches the KPI
    card ``textContent`` values without a full page reload.

Auth is enforced by the parent application's HTTP middleware; these routes
do not perform auth themselves.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

if TYPE_CHECKING:
    from orchestrator.dashboard.data import RunDataReader

logger = logging.getLogger(__name__)

# Maximum SSE stream duration (30 minutes) to prevent resource exhaustion.
_MAX_SSE_DURATION_SECS = 30 * 60

# Polling interval for the SSE overview generator.
_SSE_POLL_INTERVAL_SECS = 5


def create_dashboard_overview_router(
    templates: Jinja2Templates,
    reader: "RunDataReader",
) -> APIRouter:
    """Return an :class:`~fastapi.APIRouter` with the dashboard overview endpoints.

    Args:
        templates: Jinja2Templates instance shared by the parent app.
        reader:    :class:`~orchestrator.dashboard.data.RunDataReader` instance
                   used to fetch real-time workspace data.

    Returns:
        Configured :class:`~fastapi.APIRouter` with three routes:

        * ``GET /api/v1/dashboard/overview`` — JSON KPI snapshot
        * ``GET /dashboard``                 — HTML overview page
        * ``GET /api/v1/dashboard/sse``      — SSE push stream (5-second poll)
    """
    router = APIRouter()

    # ------------------------------------------------------------------
    # 1. JSON overview endpoint
    # ------------------------------------------------------------------

    @router.get("/api/v1/dashboard/overview")
    async def api_dashboard_overview() -> dict[str, Any]:
        """Return the consolidated real-time dashboard overview as JSON.

        Response shape::

            {
                "active_runs": 2,
                "runs_today": 5,
                "cost_today": 0.1234,
                "burn_rate": 0.05,
                "slo_summary": {
                    "all_passing": true,
                    "slis": [{"name": "pipeline_success_rate", "passing": true}, ...]
                },
                "active_alerts": 0
            }
        """
        return reader.get_dashboard_overview()

    # ------------------------------------------------------------------
    # 2. HTML page
    # ------------------------------------------------------------------

    @router.get("/dashboard", response_class=HTMLResponse)
    async def dashboard_page(request: Request) -> HTMLResponse:
        """Render the real-time overview dashboard page.

        The page renders with an initial server-side snapshot so the first
        paint shows real data.  Client-side JavaScript then subscribes to the
        SSE endpoint and patches KPI values within 5 seconds of each change.
        """
        try:
            overview = reader.get_dashboard_overview()
        except Exception:
            logger.exception("Failed to fetch dashboard overview for initial render")
            overview = {
                "active_runs": 0,
                "runs_today": 0,
                "cost_today": 0.0,
                "burn_rate": 0.0,
                "slo_summary": {"all_passing": True, "slis": []},
                "active_alerts": 0,
            }

        # Flatten slo_summary for template convenience.
        slo_summary: dict[str, Any] = overview.get("slo_summary") or {}
        slo_all_passing: bool = bool(slo_summary.get("all_passing", True))
        slo_slis: list[dict[str, Any]] = slo_summary.get("slis", [])

        return templates.TemplateResponse(
            "dashboard.html",
            {
                "request": request,
                "page_title": "Dashboard",
                "active_runs": overview.get("active_runs", 0),
                "runs_today": overview.get("runs_today", 0),
                "cost_today": overview.get("cost_today", 0.0),
                "burn_rate": overview.get("burn_rate", 0.0),
                "slo_all_passing": slo_all_passing,
                "slo_slis": slo_slis,
                "active_alerts": overview.get("active_alerts", 0),
            },
        )

    # ------------------------------------------------------------------
    # 3. SSE push stream
    # ------------------------------------------------------------------

    @router.get("/api/v1/dashboard/sse")
    async def dashboard_sse() -> StreamingResponse:
        """Push overview JSON updates every 5 seconds via Server-Sent Events.

        The generator terminates automatically after
        ``_MAX_SSE_DURATION_SECS`` (30 minutes) to prevent runaway
        connections.  Clients should reconnect using exponential backoff
        when the stream ends.
        """
        import time as _time

        async def _event_generator():
            start = _time.monotonic()
            while True:
                elapsed = _time.monotonic() - start
                if elapsed >= _MAX_SSE_DURATION_SECS:
                    yield 'data: {"event": "stream_timeout"}\n\n'
                    break

                try:
                    payload = reader.get_dashboard_overview()
                    data = json.dumps(payload, default=str)
                except Exception as exc:
                    logger.warning("dashboard SSE: overview fetch failed: %s", exc)
                    data = json.dumps({"error": "fetch_failed"})

                yield f"data: {data}\n\n"
                await asyncio.sleep(_SSE_POLL_INTERVAL_SECS)

        return StreamingResponse(
            _event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    return router
