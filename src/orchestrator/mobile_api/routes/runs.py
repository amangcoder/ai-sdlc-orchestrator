"""REST lifecycle endpoints for orchestration runs.

Endpoints:
  GET    /api/v1/runs                            — list runs (newest-first, capped at 20)
  GET    /api/v1/runs/{run_id}                   — get run detail from per-run state file
  POST   /api/v1/runs                            — start a new run (rate-limited)
  POST   /api/v1/runs/{run_id}/cancel            — cancel active run (writes .interrupt)
  POST   /api/v1/runs/{run_id}/resume            — resume a failed/cancelled run
  GET    /api/v1/runs/{run_id}/pending-prompt    — get pending orchestrator prompt (if any)
  POST   /api/v1/runs/{run_id}/respond           — submit user response to a pending prompt

IMPORTANT: GET /runs/{id} reads workspace/state-{id}.json DIRECTLY, never
the global state.json. This preserves per-run isolation.
"""

from __future__ import annotations

import json
import os
import re
import secrets
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, Response

from orchestrator.mobile_api.models import (
    CancelResponse,
    PendingPromptResponse,
    RespondRequest,
    RespondResponse,
    RunDetailResponse,
    RunStartRequest,
    RunStartResponse,
    RunSummaryResponse,
    _RUN_ID_RE,
    PROMPT_FILE_PATTERN,
    RESPONSE_FILE_PATTERN,
)
from orchestrator.mobile_api.rate_limit import RateLimiter

router = APIRouter()


def _extract_active_run_id(exc: ValueError) -> str:
    """Extract the active run ID from a RunTracker ValueError message.

    The RunTracker error format is: "A run is already active ({run_id}). ..."
    Returns the extracted run_id string, or empty string if not found.
    """
    err_msg = str(exc)
    match = re.search(r"\(([a-zA-Z0-9]+)\)", err_msg)
    return match.group(1) if match else ""


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


def _resolve_workspace_path(workspace_id: str, request: Request) -> Path | None:
    """Resolve an opaque workspace_id to a validated filesystem Path.

    Tries frozen_dir_map first (O(1)), then falls back to DynamicDirectoryService
    rglob scan (O(n)) if projects_root is configured.

    This shared helper replaces the copy-pasted try-frozen_map-then-dynamic
    pattern that appeared in list_runs and start_run, preventing future divergence.

    Args:
        workspace_id: Opaque directory ID supplied by the mobile client.
        request: FastAPI Request (used to access app.state).

    Returns:
        Validated resolved Path, or None if the ID cannot be resolved safely.
    """
    # Try the frozen directory map first (startup-built, O(1))
    frozen_dir_map: dict = request.app.state.frozen_dir_map
    from orchestrator.mobile_api.directory_service import resolve_workspace_id
    resolved_path = resolve_workspace_id(workspace_id, frozen_dir_map)

    if resolved_path is None:
        # Fallback: try DynamicDirectoryService if projects_root is configured
        projects_root = getattr(request.app.state, "projects_root", None)
        if projects_root is not None:
            from orchestrator.mobile_api.dynamic_directory_service import (
                resolve_dynamic_id,
            )
            salt = request.app.state.directory_salt
            resolved_path = resolve_dynamic_id(workspace_id, projects_root, salt)

    return resolved_path


# ── GET /api/v1/runs ───────────────────────────────────────────────────────

@router.get("/runs")
async def list_runs(
    request: Request,
    workspace_id: str | None = Query(default=None),
) -> list[dict]:
    """List all runs, sorted newest-first.

    When workspace_id is provided:
      - Filter runs from app_workspace whose state file contains
        'workspace_id' == the provided value (consistent with projects.py
        run-count approach — TASK-017 fix).
      - Return up to 100 results.
    When absent: preserve existing behavior (read from app workspace, 20-run cap).

    current_step is read from per-run state-{id}.json, NOT the global state.json.
    """
    reader = request.app.state.reader
    app_workspace: Path = reader.workspace

    results: list[RunSummaryResponse] = []
    state_files = sorted(app_workspace.glob("state-*.json"), reverse=True)

    for state_file in state_files:
        try:
            state = json.loads(state_file.read_text())
        except (json.JSONDecodeError, OSError):
            continue

        # When workspace_id filter is active, skip runs not tagged with this ID.
        # Filter by the workspace_id JSON field — consistent with projects.py's
        # _build_run_count_index approach (TASK-017 LIST_RUNS INCONSISTENCY fix).
        if workspace_id is not None:
            if state.get("workspace_id") != workspace_id:
                continue

        resp = _run_summary_from_state(state)
        results.append(resp)

    # Sort newest-first by start_time
    results.sort(key=lambda r: r.start_time or "", reverse=True)

    # Cap: 100 when filtering by workspace_id, 20 otherwise (backward compat)
    cap = 100 if workspace_id is not None else 20
    results = results[:cap]

    return [r.model_dump() for r in results]


# ── GET /api/v1/runs/{run_id} ──────────────────────────────────────────────

@router.get("/runs/{run_id}")
async def get_run(run_id: str, request: Request):
    """Get full run detail from workspace/state-{run_id}.json.

    When run state includes workspace_id, reads from that project's workspace.
    Otherwise reads from app workspace.
    HTTP 400 if run_id format is invalid (path-traversal prevention).
    HTTP 404 if the state file does not exist in any workspace.
    """
    if not _validate_run_id_format(run_id):
        return JSONResponse(
            status_code=400,
            content={"error": "Invalid run_id format"},
        )

    app_workspace: Path = request.app.state.reader.workspace

    # First try app workspace
    state = _read_per_run_state(app_workspace, run_id)

    # If not found and we have projects_root, search all project workspaces
    if state is None:
        projects_root = getattr(request.app.state, "projects_root", None)
        if projects_root is not None:
            for project_dir in Path(projects_root).iterdir():
                if project_dir.is_dir():
                    project_workspace = project_dir / "workspace"
                    state = _read_per_run_state(project_workspace, run_id)
                    if state is not None:
                        break

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
    Returns HTTP 422 if workspace_id is provided but not in the allow-list.
    Returns HTTP 429 if rate limit exceeded.

    New in this version:
    - workspace_id now also falls back to DynamicDirectoryService if not
      found in frozen_map and app.state.projects_root is configured.
    - use_system_orchestrate=True invokes the system-installed orchestrate
      binary instead of the in-process RunTracker.
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

    # ── Workspace ID validation ────────────────────────────────────────────
    # IMPORTANT: resolve workspace_dir_override BEFORE building RunRequest so
    # RunTracker can apply it before engine initialisation reads config.workspace_dir.
    workspace_dir_override: str | None = None

    if run_request.workspace_id is not None:
        resolved_path = _resolve_workspace_path(run_request.workspace_id, request)

        if resolved_path is None:
            return JSONResponse(
                status_code=422,
                content={"detail": "workspace_id is not a recognised directory"},
            )

        workspace_dir_override = str(resolved_path)

    # workspace_dir_override is set BEFORE tracker.start_run() is called.
    # The engine reads config.workspace_dir during initialisation — the
    # RunTracker override block must execute before engine.__init__.

    # ── System orchestrate branch ──────────────────────────────────────────
    # When use_system_orchestrate=True, invoke the globally-installed binary
    # instead of the in-process RunTracker. This branch is ADDITIVE — the
    # in-process path is completely unchanged.
    if run_request.use_system_orchestrate:
        from orchestrator.mobile_api.system_runner import locate_binary, start_subprocess_run

        binary = locate_binary()
        if binary is None:
            return JSONResponse(
                status_code=422,
                content={"error": "orchestrate binary not found on PATH"},
            )

        # App workspace is always the reader's workspace (for state files + JSONL logs)
        app_workspace: Path = request.app.state.reader.workspace

        # project_path is the selected project directory (CLI --workspace arg)
        project_path: Path | None = None
        if workspace_dir_override is not None:
            project_path = Path(workspace_dir_override)

            # TOCTOU mitigation: re-verify the resolved project path is still
            # inside projects_root at the moment we're about to launch the
            # subprocess. This guards against a race where the directory was
            # moved or symlinked between resolve_dynamic_id and now.
            projects_root = getattr(request.app.state, "projects_root", None)
            if projects_root is not None:
                try:
                    project_path.resolve().relative_to(Path(projects_root).resolve())
                except ValueError:
                    return JSONResponse(
                        status_code=422,
                        content={"detail": "workspace_id resolved outside projects_root (TOCTOU)"},
                    )

        # Generate a run ID for this subprocess run
        run_id = secrets.token_hex(8)  # 16-char hex run ID (matches in-process tracker format)

        # Start the subprocess (non-blocking — returns asyncio.Task).
        # workspace_id is passed so start_subprocess_run can write it into the
        # initial state file atomically — no post-creation overwrite needed
        # (TASK-017 STATE FILE OVERWRITE fix).
        await start_subprocess_run(
            run_request,
            app_workspace,
            run_id,
            project_path=project_path,
            workspace_id=run_request.workspace_id,
        )

        return JSONResponse(
            status_code=202,
            content=RunStartResponse(run_id=run_id, status="started").model_dump(),
        )

    # ── In-process RunTracker branch (existing, unchanged) ─────────────────

    tracker = request.app.state.tracker

    # Build a RunRequest compatible with the existing RunTracker.
    # All new fields are forwarded with their exact values:
    # None means "use config default" — do NOT substitute values here;
    # that logic lives in RunTracker.start_run().
    from orchestrator.dashboard.runner import RunRequest

    run_req = RunRequest(
        feature_request=run_request.feature_request,
        workflow_type=run_request.workflow_type,
        debate=run_request.debate,
        knowledge=run_request.knowledge,
        max_budget_usd=run_request.max_budget_usd,
        dry_run=run_request.dry_run,
        resume_run_id=run_request.resume_run_id,
        # New fields — forwarded as-is
        workspace_dir_override=workspace_dir_override,
        self_orchestrate=run_request.self_orchestrate,
        confirm=run_request.confirm,
        tech_stack_confirmation=run_request.tech_stack_confirmation,
        checklist_verify=run_request.checklist_verify,
        max_concurrent_agents=run_request.max_concurrent_agents if run_request.max_concurrent_agents is not None else 0,
        mode=run_request.mode,
        speed=run_request.speed,
        phase=run_request.phase,
        from_phase=run_request.from_phase,
        log_format=run_request.log_format,
        researchers=run_request.researchers,
        brainstormers=run_request.brainstormers,
        debate_rounds=run_request.debate_rounds,
    )

    try:
        run_id = await tracker.start_run(run_req)
    except ValueError as exc:
        active_run_id = _extract_active_run_id(exc)
        return JSONResponse(
            status_code=409,
            content={
                "error": "Run already active",
                "active_run_id": active_run_id,
            },
        )

    # ── Inject workspace_id into state file for project filtering ─────────────
    if run_request.workspace_id is not None:
        workspace: Path = request.app.state.reader.workspace
        state = _read_per_run_state(workspace, run_id)
        if state:
            state["workspace_id"] = run_request.workspace_id
            state_file = workspace / f"state-{run_id}.json"
            try:
                state_file.write_text(json.dumps(state, indent=2))
            except OSError:
                pass  # Best-effort; filtering will still work if state has workspace_id

    return JSONResponse(
        status_code=202,
        content=RunStartResponse(run_id=run_id, status="started").model_dump(),
    )


# ── POST /api/v1/runs/{run_id}/cancel ─────────────────────────────────────

@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str, request: Request):
    """Cancel an active run by writing the .interrupt sentinel file.

    CRITICAL: Only writes sentinel when the run_id is confirmed active.
    Returns HTTP 400 if run_id format is invalid.
    Returns HTTP 404 if run_id is not currently active (no sentinel written).
    This prevents cross-run sentinel contamination.
    """
    if not _validate_run_id_format(run_id):
        return JSONResponse(
            status_code=400,
            content={"error": "Invalid run_id format"},
        )

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
    Returns HTTP 400 if run_id format is invalid.
    Returns HTTP 409 if another run is already active.

    TASK-017 fix: Reads workspace_id from the original run's state and
    propagates workspace_dir_override to the new run so the resumed run
    executes in the same project workspace as the original.
    """
    if not _validate_run_id_format(run_id):
        return JSONResponse(
            status_code=400,
            content={"error": "Invalid run_id format"},
        )

    tracker = request.app.state.tracker

    # Build a resume RunRequest
    from orchestrator.dashboard.runner import RunRequest

    # Read the original run's details to pass the same feature request
    workspace: Path = request.app.state.reader.workspace
    state = _read_per_run_state(workspace, run_id)

    # If not found in app workspace, search project workspaces
    if state is None:
        projects_root = getattr(request.app.state, "projects_root", None)
        if projects_root is not None:
            for project_dir in Path(projects_root).iterdir():
                if project_dir.is_dir():
                    project_workspace = project_dir / "workspace"
                    state = _read_per_run_state(project_workspace, run_id)
                    if state is not None:
                        break

    feature_request = ""
    workflow_type = "feature_development"
    original_workspace_id: str | None = None
    workspace_dir_override: str | None = None

    if state:
        feature_request = state.get("feature_request", "")
        workflow_type = state.get("workflow_type", "feature_development")
        # Propagate workspace_id so resumed run executes in the same project workspace
        original_workspace_id = state.get("workspace_id")

    # Resolve workspace_id to a path for the resumed run
    if original_workspace_id is not None:
        resolved = _resolve_workspace_path(original_workspace_id, request)
        if resolved is not None:
            workspace_dir_override = str(resolved)

    run_req = RunRequest(
        feature_request=feature_request or "Resume run",
        workflow_type=workflow_type,
        resume_run_id=run_id,
        workspace_dir_override=workspace_dir_override,
    )

    try:
        new_run_id = await tracker.start_run(run_req)
    except ValueError as exc:
        active_run_id = _extract_active_run_id(exc)
        return JSONResponse(
            status_code=409,
            content={
                "error": "Run already active",
                "active_run_id": active_run_id,
            },
        )

    # Propagate workspace_id to the new run's state file
    if original_workspace_id is not None:
        new_state = _read_per_run_state(workspace, new_run_id)
        if new_state:
            new_state["workspace_id"] = original_workspace_id
            new_state_file = workspace / f"state-{new_run_id}.json"
            try:
                new_state_file.write_text(json.dumps(new_state, indent=2))
            except OSError:
                pass

    return JSONResponse(
        status_code=202,
        content=RunStartResponse(run_id=new_run_id, status="started").model_dump(),
    )


# ── Prompt/Response Helpers ───────────────────────────────────────────────

# Regex to strip Unicode bidirectional override characters (visual spoofing)
_BIDI_RE = re.compile(r"[\u202a-\u202e\u2066-\u2069]")


def _validate_run_id_format(run_id: str) -> bool:
    """Validate run_id format using the shared regex.

    Returns True if valid, False if it contains path-traversal or
    other unsafe characters.
    """
    return bool(_RUN_ID_RE.match(run_id))


def _sanitize_prompt(raw: dict) -> dict:
    """Sanitize prompt content to prevent visual spoofing attacks.

    - Truncates question to 500 chars
    - Truncates each option string to 100 chars
    - Strips Unicode bidi override characters (U+202A–U+202E, U+2066–U+2069)
    """
    sanitized = dict(raw)

    question = str(sanitized.get("question", ""))
    question = _BIDI_RE.sub("", question)[:500]
    sanitized["question"] = question

    options = sanitized.get("options")
    if options is not None and isinstance(options, list):
        sanitized["options"] = [
            _BIDI_RE.sub("", str(opt))[:100] for opt in options
        ]

    return sanitized


def _find_prompt_file(
    run_id: str,
    app_workspace: Path,
    request: Request,
) -> tuple[Path | None, Path]:
    """Find the .prompt-{run_id}.json file for a run.

    Checks app_workspace first, then the project workspace associated with
    the run's workspace_id (for system_orchestrate runs where the orchestrate
    binary writes prompt files to the project directory via --workspace).

    Returns:
        (prompt_file, search_workspace) where prompt_file is None if not found
        and search_workspace is the directory to write response files to.
    """
    # Primary: app workspace
    prompt_file = app_workspace / PROMPT_FILE_PATTERN.format(run_id=run_id)
    if prompt_file.exists():
        return prompt_file, app_workspace

    # Secondary: project workspace (for system_orchestrate runs)
    # Read workspace_id from the run's state file to locate the project directory.
    state = _read_per_run_state(app_workspace, run_id)
    workspace_id = state.get("workspace_id") if state else None
    if workspace_id is not None:
        project_path = _resolve_workspace_path(workspace_id, request)
        if project_path is not None:
            # Prompt files written by the orchestrate binary live directly in
            # the project directory (not a sub-directory called workspace/).
            project_prompt = project_path / PROMPT_FILE_PATTERN.format(run_id=run_id)
            if project_prompt.exists():
                return project_prompt, project_path

    return None, app_workspace


# ── GET /api/v1/runs/{run_id}/pending-prompt ──────────────────────────────

@router.get("/runs/{run_id}/pending-prompt")
def get_pending_prompt(run_id: str, request: Request):
    """Get the pending prompt for a run, if any.

    Returns HTTP 200 with prompt data if a prompt is pending.
    Returns HTTP 204 if no prompt is pending.
    Returns HTTP 400 if run_id format is invalid.
    Returns HTTP 404 if the run does not exist.

    TASK-017 fix: searches both app_workspace and project workspace so that
    prompts written by system_orchestrate (to the project directory via
    --workspace) are visible to the API.
    """
    if not _validate_run_id_format(run_id):
        return JSONResponse(
            status_code=400,
            content={"error": "Invalid run_id format"},
        )

    workspace: Path = request.app.state.reader.workspace
    state = _read_per_run_state(workspace, run_id)

    if state is None:
        return JSONResponse(
            status_code=404,
            content={"error": "Run not found"},
        )

    prompt_file, _ = _find_prompt_file(run_id, workspace, request)
    if prompt_file is None:
        return Response(status_code=204)

    try:
        raw = json.loads(prompt_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return Response(status_code=204)

    sanitized = _sanitize_prompt(raw)
    response = PendingPromptResponse(
        prompt_id=sanitized.get("prompt_id", ""),
        question=sanitized.get("question", ""),
        type=sanitized.get("type", "free_text"),
        options=sanitized.get("options"),
        created_at=sanitized.get("created_at", ""),
    )
    return JSONResponse(status_code=200, content=response.model_dump())


# ── POST /api/v1/runs/{run_id}/respond ────────────────────────────────────

@router.post("/runs/{run_id}/respond")
def respond_to_prompt(run_id: str, body: RespondRequest, request: Request):
    """Submit a response to a pending prompt.

    Returns HTTP 200 on success (or idempotent re-submit with same prompt_id).
    Returns HTTP 400 if run_id format is invalid.
    Returns HTTP 404 if no prompt file exists.
    Returns HTTP 409 if prompt_id does not match the current prompt.
    Returns HTTP 410 if the run is no longer active.

    TASK-017 fix: searches both app_workspace and project workspace for the
    prompt file and writes the response file to the same directory where the
    prompt file was found.
    """
    if not _validate_run_id_format(run_id):
        return JSONResponse(
            status_code=400,
            content={"error": "Invalid run_id format"},
        )

    workspace: Path = request.app.state.reader.workspace
    tracker = request.app.state.tracker

    # Check if run is active (HTTP 410 if not)
    if not tracker.is_active(run_id):
        return JSONResponse(
            status_code=410,
            content={"error": "Run is no longer active"},
        )

    # Find prompt file (checks both app_workspace and project workspace)
    prompt_file, prompt_workspace = _find_prompt_file(run_id, workspace, request)
    if prompt_file is None:
        return JSONResponse(
            status_code=404,
            content={"error": "No pending prompt for this run"},
        )

    try:
        prompt_data = json.loads(prompt_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return JSONResponse(
            status_code=404,
            content={"error": "No pending prompt for this run"},
        )

    # Verify prompt_id matches
    stored_prompt_id = prompt_data.get("prompt_id", "")
    if body.prompt_id != stored_prompt_id:
        return JSONResponse(
            status_code=409,
            content={"error": "Prompt ID mismatch \u2014 response rejected"},
        )

    # Write response file to the SAME directory where the prompt file lives.
    # This ensures the orchestrate binary (which polls for the response in the
    # same directory it wrote the prompt) can find it.
    response_file = prompt_workspace / RESPONSE_FILE_PATTERN.format(run_id=run_id)

    # Check for idempotent re-submit
    if response_file.exists():
        try:
            existing = json.loads(response_file.read_text(encoding="utf-8"))
            if existing.get("prompt_id") == body.prompt_id:
                return JSONResponse(
                    status_code=200,
                    content=RespondResponse(
                        status="already_submitted",
                        prompt_id=body.prompt_id,
                    ).model_dump(),
                )
        except (json.JSONDecodeError, OSError):
            pass

    # Write response file atomically via O_CREAT|O_EXCL
    from datetime import datetime, timezone

    response_payload = json.dumps({
        "prompt_id": body.prompt_id,
        "response": body.response,
        "submitted_at": datetime.now(timezone.utc).isoformat(),
    }, ensure_ascii=False).encode("utf-8")

    try:
        fd = os.open(
            str(response_file),
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o644,
        )
        try:
            os.write(fd, response_payload)
        finally:
            os.close(fd)
    except FileExistsError:
        # Race condition: another request wrote the file first
        # Return idempotent success
        pass

    return JSONResponse(
        status_code=200,
        content=RespondResponse(
            status="submitted",
            prompt_id=body.prompt_id,
        ).model_dump(),
    )
