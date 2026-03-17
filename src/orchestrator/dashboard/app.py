"""FastAPI dashboard application."""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from orchestrator.dashboard.data import RunDataReader

STATIC_DIR = Path(__file__).parent / "static"
TEMPLATES_DIR = Path(__file__).parent / "templates"

# --- SSE limits ---
MAX_SSE_DURATION = 30 * 60  # 30 minutes
MAX_SSE_CONNECTIONS = 10

# --- Auth ---
DASHBOARD_TOKEN = os.environ.get("ORCHESTRATOR_DASHBOARD_TOKEN")

# Track active SSE connections
_active_sse_connections = 0


def create_app(workspace_dir: Path) -> FastAPI:
    app = FastAPI(title="Orchestrator Dashboard", version="0.1.0")
    reader = RunDataReader(workspace_dir)
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # --- Auth middleware ---

    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        if DASHBOARD_TOKEN and request.url.path.startswith("/api"):
            token = request.headers.get("Authorization", "").removeprefix("Bearer ")
            if token != DASHBOARD_TOKEN:
                return JSONResponse(status_code=401, content={"error": "Unauthorized"})
        return await call_next(request)

    # --- HTML pages ---

    @app.get("/", response_class=RedirectResponse)
    async def index():
        return RedirectResponse(url="/runs")

    @app.get("/runs", response_class=HTMLResponse)
    async def runs_page(request: Request):
        runs = reader.list_runs()
        return templates.TemplateResponse("runs.html", {
            "request": request,
            "runs": runs,
            "page_title": "All Runs",
        })

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    async def run_detail_page(request: Request, run_id: str):
        run = reader.get_run(run_id)
        if not run:
            return HTMLResponse("<h1>Run not found</h1>", status_code=404)
        return templates.TemplateResponse("run_detail.html", {
            "request": request,
            "run": run,
            "phases_json": json.dumps(run.phases, default=str),
            "page_title": f"Run {run_id}",
        })

    @app.get("/runs/{run_id}/live", response_class=HTMLResponse)
    async def run_live_page(request: Request, run_id: str):
        run = reader.get_run(run_id)
        if not run:
            return HTMLResponse("<h1>Run not found</h1>", status_code=404)
        return templates.TemplateResponse("live.html", {
            "request": request,
            "run": run,
            "page_title": f"Live: {run_id}",
        })

    @app.get("/metrics", response_class=HTMLResponse)
    async def metrics_page(request: Request):
        summary = reader.get_metrics_summary()
        return templates.TemplateResponse("metrics.html", {
            "request": request,
            "summary": summary,
            "summary_json": json.dumps(summary, default=str),
            "page_title": "Metrics",
        })

    @app.get("/alerts", response_class=HTMLResponse)
    async def alerts_page(request: Request):
        alerts = reader.get_alert_history()
        return templates.TemplateResponse("alerts.html", {
            "request": request,
            "alerts": alerts,
            "page_title": "Alerts",
        })

    # --- JSON API ---

    @app.get("/api/runs")
    async def api_runs():
        runs = reader.list_runs()
        return [
            {
                "run_id": r.run_id,
                "feature_request": r.feature_request,
                "workflow_type": r.workflow_type,
                "status": r.status,
                "total_cost_usd": r.total_cost_usd,
                "start_time": r.start_time,
                "end_time": r.end_time,
                "steps_completed": r.steps_completed,
                "steps_total": r.steps_total,
            }
            for r in runs
        ]

    @app.get("/api/runs/{run_id}")
    async def api_run_detail(run_id: str):
        run = reader.get_run(run_id)
        if not run:
            return JSONResponse({"error": "not found"}, status_code=404)
        return {
            "run_id": run.run_id,
            "feature_request": run.feature_request,
            "workflow_type": run.workflow_type,
            "status": run.status,
            "total_cost_usd": run.total_cost_usd,
            "phases": run.phases,
            "interrupt_history": run.interrupt_history,
        }

    @app.get("/api/runs/{run_id}/events")
    async def api_run_events(run_id: str, offset: int = 0, limit: int = 100):
        events = reader.get_events(run_id, offset, limit)
        return {"events": events, "offset": offset, "count": len(events)}

    @app.get("/api/runs/{run_id}/timeline")
    async def api_run_timeline(run_id: str):
        run = reader.get_run(run_id)
        if not run or not run.timeline:
            return JSONResponse({"error": "no timeline data"}, status_code=404)
        return run.timeline

    @app.get("/api/metrics")
    async def api_metrics():
        return reader.get_metrics_summary()

    @app.get("/api/alerts")
    async def api_alerts(limit: int = 100):
        return reader.get_alert_history(limit)

    # --- SSE for live updates ---

    @app.get("/api/runs/{run_id}/stream")
    async def sse_stream(run_id: str):
        global _active_sse_connections

        if _active_sse_connections >= MAX_SSE_CONNECTIONS:
            return JSONResponse(
                status_code=429,
                content={"error": f"Too many SSE connections (max {MAX_SSE_CONNECTIONS})"},
            )

        async def event_generator():
            global _active_sse_connections
            _active_sse_connections += 1
            start_time = time.monotonic()
            try:
                last_line = 0
                while True:
                    # Enforce max duration
                    elapsed = time.monotonic() - start_time
                    if elapsed >= MAX_SSE_DURATION:
                        yield "data: {\"event\": \"stream_timeout\"}\n\n"
                        break
                    events = reader.tail_events(run_id, after_line=last_line)
                    for event in events:
                        yield f"data: {json.dumps(event, default=str)}\n\n"
                        last_line += 1
                    # Check if run is complete
                    if any(e.get("event") == "run_complete" for e in events):
                        yield "data: {\"event\": \"stream_end\"}\n\n"
                        break
                    await asyncio.sleep(1)
            finally:
                _active_sse_connections -= 1

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )

    # --- Health check ---

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok", "service": "orchestrator-dashboard"}

    return app
