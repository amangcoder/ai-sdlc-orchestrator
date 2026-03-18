"""REST lifecycle endpoints for orchestration runs.

Endpoints:
  GET    /api/v1/runs                    — list runs (newest-first, capped at 20)
  GET    /api/v1/runs/{run_id}           — get run detail from per-run state file
  POST   /api/v1/runs                    — start a new run (rate-limited)
  POST   /api/v1/runs/{run_id}/cancel    — cancel active run (writes .interrupt)
  POST   /api/v1/runs/{run_id}/resume    — resume a failed/cancelled run

IMPORTANT: GET /runs/{id} reads workspace/state-{id}.json DIRECTLY, never
the global state.json. This preserves per-run isolation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from orchestrator.mobile_api.models import (
    CancelResponse,
    RunDetailResponse,
    RunStartRequest,
    RunStartResponse,
    RunSummaryResponse,
)
from orchestrator.mobile_api.rate_limit import RateLimiter

router = APIRouter()


def _read_per_run_state(workspace: Path, run_id: str) -> dict[str, Any] | None:
    """Read workspace/state-{run_id}.json (never state.json)."""
    state_file = workspace / f"state-{run_id}.json"
    if not state_file.exists():
        return None
    try:
        return json.loads(state_file.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _run_summary_from_state(state: dict[str, Any]) -> RunSummaryResponse:
    """Build a RunSummaryResponse from a per-run state dict."""
    return RunSummaryResponse(
        run_id=state.get("run_id", ""),
        feature_request=state.get("feature_request", ""),
        workflow_type=state.get("workflow_type", "unknown"),
        status=state.get("status", "unknown"),
        current_step=state.get("current_step"),
        total_cost_usd=float(state.get("total_cost_usd", 0.0)),
        start_time=state.get("start_time", ""),
        end_time=state.get("end_time"),
        steps_completed=int(state.get("steps_completed", 0)),
        steps_total=int(state.get("steps_total", 0)),
    )


# ── GET /api/v1/runs ───────────────────────────────────────────────────────

@router.get("/runs")
async def list_runs(request: Request) -> list[dict]:
    """List all runs, sorted newest-first, capped at 20.

    current_step is read from per-run state-{id}.json, NOT the global state.json.
    """
    reader = request.app.state.reader
    workspace: Path = reader.workspace

    # Get base summaries from JSONL log files
    summaries = reader.list_runs()

    results: list[RunSummaryResponse] = []
    for summary in summaries:
        # Augment with current_step from per-run state file
        state = _read_per_run_state(workspace, summary.run_id)

        if state:
            # Use the state file as the authoritative source
            resp = _run_summary_from_state(state)
        else:
            # Fall back to JSONL-derived summary (no current_step)
            resp = RunSummaryResponse(
                run_id=summary.run_id,
                feature_request=summary.feature_request,
                workflow_type=summary.workflow_type,
                status=summary.status,
                current_step=None,
                total_cost_usd=summary.total_cost_usd,
                start_time=summary.start_time,
                end_time=summary.end_time,
                steps_completed=summary.steps_completed,
                steps_total=summary.steps_total,
            )
        results.append(resp)

    # Sort newest-first by start_time, cap at 20
    results.sort(key=lambda r: r.start_time or "", reverse=True)
    results = results[:20]

    return [r.model_dump() for r in results]


# ── GET /api/v1/runs/{run_id} ──────────────────────────────────────────────

@router.get("/runs/{run_id}")
async def get_run(run_id: str, request: Request):
    """Get full run detail from workspace/state-{run_id}.json.

    Reads the per-run state file DIRECTLY — never the global state.json.
    HTTP 404 if the state file does not exist.
    """
    workspace: Path = request.app.state.reader.workspace
    state = _read_per_run_state(workspace, run_id)

    if state is None:
        return JSONResponse(
            status_code=404,
            content={"error": "Run not found"},
        )

    detail = RunDetailResponse(
        run_id=state.get("run_id", run_id),
        feature_request=state.get("feature_request", ""),
        workflow_type=state.get("workflow_type", "unknown"),
        status=state.get("status", "unknown"),
        current_step=state.get("current_step"),
        total_cost_usd=float(state.get("total_cost_usd", 0.0)),
        start_time=state.get("start_time"),
        end_time=state.get("end_time"),
        steps_completed=int(state.get("steps_completed", 0)),
        steps_total=int(state.get("steps_total", 0)),
        phases=state.get("phases", {}),
        workflow_tasks=state.get("workflow_tasks", []),
    )
    return detail.model_dump()


# ── POST /api/v1/runs ──────────────────────────────────────────────────────

@router.post("/runs", status_code=202)
async def start_run(run_request: RunStartRequest, request: Request):
    """Start a new orchestration run.

    Rate-limited: max 1 request per 5 seconds per source IP.
    Returns HTTP 409 if a run is already active.
    Returns HTTP 429 if rate limit exceeded.
    """
    # Rate limiting — use client host IP (don't trust X-Forwarded-For)
    # Rate limiter is stored per-app to avoid cross-test contamination
    _rate_limiter: RateLimiter = request.app.state.rate_limiter
    client_ip = request.client.host if request.client else "0.0.0.0"
    if not _rate_limiter.check_and_record(client_ip):
        return JSONResponse(
            status_code=429,
            content={"error": "Too many requests — please wait before starting another run"},
        )

    tracker = request.app.state.tracker

    # Build a RunRequest compatible with the existing RunTracker
    from orchestrator.dashboard.runner import RunRequest

    run_req = RunRequest(
        feature_request=run_request.feature_request,
        workflow_type=run_request.workflow_type,
        debate=run_request.debate,
        knowledge=run_request.knowledge,
        enhanced_perception=run_request.enhanced_perception,
        max_budget_usd=run_request.max_budget_usd,
        dry_run=run_request.dry_run,
        resume_run_id=run_request.resume_run_id,
    )

    try:
        run_id = await tracker.start_run(run_req)
    except ValueError as exc:
        # Extract active_run_id from the error message if possible
        err_msg = str(exc)
        active_run_id = ""
        # The RunTracker error format: "A run is already active ({run_id}). ..."
        import re
        match = re.search(r"\(([a-zA-Z0-9]+)\)", err_msg)
        if match:
            active_run_id = match.group(1)
        return JSONResponse(
            status_code=409,
            content={
                "error": "Run already active",
                "active_run_id": active_run_id,
            },
        )

    return JSONResponse(
        status_code=202,
        content=RunStartResponse(run_id=run_id, status="started").model_dump(),
    )


# ── POST /api/v1/runs/{run_id}/cancel ─────────────────────────────────────

@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str, request: Request):
    """Cancel an active run by writing the .interrupt sentinel file.

    CRITICAL: Only writes sentinel when the run_id is confirmed active.
    Returns HTTP 404 if run_id is not currently active (no sentinel written).
    This prevents cross-run sentinel contamination.
    """
    tracker = request.app.state.tracker

    # Check if run is active BEFORE doing anything
    if not tracker.is_active(run_id):
        return JSONResponse(
            status_code=404,
            content={"error": "Run not active"},
        )

    # Run is confirmed active — write sentinel file and cancel
    # Write the sentinel directly so it's guaranteed regardless of tracker implementation
    workspace: Path = request.app.state.reader.workspace
    sentinel = workspace / ".interrupt"
    try:
        sentinel.write_text("web_cancel")
    except OSError:
        pass  # Best-effort; tracker.cancel_run also writes it for real tracker
    await tracker.cancel_run(run_id)
    return CancelResponse(cancelled=True).model_dump()


# ── POST /api/v1/runs/{run_id}/resume ─────────────────────────────────────

@router.post("/runs/{run_id}/resume", status_code=202)
async def resume_run(run_id: str, request: Request):
    """Resume a failed or cancelled run.

    Creates a new run that resumes from the state of the given run_id.
    Returns HTTP 409 if another run is already active.
    """
    tracker = request.app.state.tracker

    # Build a resume RunRequest
    from orchestrator.dashboard.runner import RunRequest

    # Read the original run's details to pass the same feature request
    workspace: Path = request.app.state.reader.workspace
    state = _read_per_run_state(workspace, run_id)

    feature_request = ""
    workflow_type = "feature_development"
    if state:
        feature_request = state.get("feature_request", "")
        workflow_type = state.get("workflow_type", "feature_development")

    run_req = RunRequest(
        feature_request=feature_request or "Resume run",
        workflow_type=workflow_type,
        resume_run_id=run_id,
    )

    try:
        new_run_id = await tracker.start_run(run_req)
    except ValueError as exc:
        err_msg = str(exc)
        active_run_id = ""
        import re
        match = re.search(r"\(([a-zA-Z0-9]+)\)", err_msg)
        if match:
            active_run_id = match.group(1)
        return JSONResponse(
            status_code=409,
            content={
                "error": "Run already active",
                "active_run_id": active_run_id,
            },
        )

    return JSONResponse(
        status_code=202,
        content=RunStartResponse(run_id=new_run_id, status="started").model_dump(),
    )
