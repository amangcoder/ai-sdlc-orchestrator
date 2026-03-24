"""System-installed orchestrate CLI invocation module.

Provides subprocess-based orchestrate invocation for the Mobile API.
Used when use_system_orchestrate=True in the run start request — this
invokes the globally-installed `orchestrate` binary instead of running
the orchestrator in-process.

This is an ADDITIVE code path — the in-process RunTracker flow is
completely unchanged.

IMPORTANT:
  - Per-run state files are written to workspace_dir/state-{run_id}.json
    (same format as in-process runs) so routes/runs.py and WebSocket
    streaming continue to work without modification.
  - JSONL logs go to workspace/logs/run-{run_id}.jsonl.
  - The subprocess runner does NOT use RunTracker — it manages its own
    per-run state lifecycle.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def locate_binary() -> str | None:
    """Find the system-installed orchestrate binary on PATH.

    Returns:
        Absolute path string to the binary, or None if not found.
    """
    return shutil.which("orchestrate")


def build_cli_args(
    request: object,
    binary_path: str,
    workspace_path: Path,
    run_id: str,
    config_path: Path | None = None,
) -> list[str]:
    """Build the argv list for the orchestrate CLI subprocess.

    Maps RunStartRequest fields to CLI flags. None/falsy optional fields
    are omitted so the binary uses its own defaults.

    Args:
        request: RunStartRequest instance (duck-typed for testability).
        binary_path: Absolute path to the orchestrate binary.
        workspace_path: Absolute path to the workspace directory.
        run_id: The run ID (used for --run-id flag if supported).

    Returns:
        Complete argv list starting with binary_path.
    """
    args: list[str] = [binary_path]

    # Required: feature request text
    feature_request = getattr(request, "feature_request", "")
    if feature_request:
        args += ["--feature-request", feature_request]

    # Optional: workflow type
    workflow_type = getattr(request, "workflow_type", None)
    if workflow_type:
        val = workflow_type.value if hasattr(workflow_type, "value") else str(workflow_type)
        args += ["--workflow", val]

    # Optional: phase to run
    phase = getattr(request, "phase", None)
    if phase is not None:
        val = phase.value if hasattr(phase, "value") else str(phase)
        args += ["--phase", val]

    # Optional: from-phase (resume from)
    from_phase = getattr(request, "from_phase", None)
    if from_phase is not None:
        val = from_phase.value if hasattr(from_phase, "value") else str(from_phase)
        args += ["--from-phase", val]

    # Optional: speed mode
    mode = getattr(request, "mode", None)
    if mode is not None:
        val = mode.value if hasattr(mode, "value") else str(mode)
        args += ["--mode", val]

    # Note: max_budget_usd is not passed as a CLI arg — the config file
    # already contains this value and the binary reads it from there.

    # Optional: speed mode
    speed = getattr(request, "speed", None)
    if speed is not None:
        val = speed.value if hasattr(speed, "value") else str(speed)
        args += ["--speed", val]

    # Optional: debate flag
    debate = getattr(request, "debate", False)
    if debate:
        args.append("--debate")

    # Optional: debate sub-settings
    researchers = getattr(request, "researchers", None)
    if researchers is not None:
        args += ["--researchers", str(researchers)]

    brainstormers = getattr(request, "brainstormers", None)
    if brainstormers is not None:
        args += ["--brainstormers", str(brainstormers)]

    debate_rounds = getattr(request, "debate_rounds", None)
    if debate_rounds is not None:
        args += ["--debate-rounds", str(debate_rounds)]

    # Optional: knowledge flag
    knowledge = getattr(request, "knowledge", None)
    if knowledge is True:
        args.append("--knowledge")
    elif knowledge is False:
        args.append("--no-knowledge")

    # Optional: dry-run flag
    dry_run = getattr(request, "dry_run", False)
    if dry_run:
        args.append("--dry-run")

    # Optional: self-orchestrate flag
    self_orchestrate = getattr(request, "self_orchestrate", None)
    if self_orchestrate is True:
        args.append("--self-orchestrate")

    # Never pass --confirm for subprocess runs — it calls input() per agent
    # which blocks forever with no TTY attached.

    # Always disable interactive prompts in subprocess runs — there's no TTY.
    # tech_stack_confirmation calls input() which blocks forever in a subprocess.
    args.append("--no-confirm-tech-stack")

    # Optional: checklist verify (negated flag)
    checklist_verify = getattr(request, "checklist_verify", None)
    if checklist_verify is False:
        args.append("--no-checklist-verify")

    # Optional: max concurrent agents
    max_concurrent_agents = getattr(request, "max_concurrent_agents", None)
    if isinstance(max_concurrent_agents, (int, float)) and max_concurrent_agents > 0:
        args += ["--max-concurrent-agents", str(max_concurrent_agents)]

    # Always use JSON log format for subprocess runs so the monitor can
    # parse structured events and update the state file with phase progress.
    # This overrides any user-supplied log_format.
    args += ["--log-format", "json"]

    # Optional: resume run ID
    resume_run_id = getattr(request, "resume_run_id", None)
    if resume_run_id is not None:
        args += ["--resume-run", str(resume_run_id)]

    # Optional: config file path
    if config_path is not None:
        args += ["--config", str(config_path.resolve())]

    # Append run-id so the binary writes to the right state file
    args += ["--run-id", run_id]

    return args

async def start_subprocess_run(
    request: object,
    workspace: Path,
    run_id: str,
    project_path: Path | None = None,
    workspace_id: str | None = None,
    config_path: Path | None = None,
) -> asyncio.Task:
    """Launch the orchestrate binary as an asyncio subprocess.

    Creates the initial per-run state file, launches the binary, streams
    stdout/stderr lines to the JSONL log, and writes the final status on
    completion.

    IMPORTANT: Per-run state files follow workspace/state-{run_id}.json
    naming (same as in-process runs) so the existing routes/runs.py GET
    handler and WebSocket streaming work without modification.

    Args:
        request: RunStartRequest instance.
        workspace: Absolute path to the APP workspace directory (where state
            files and JSONL logs are written). This is reader.workspace.
        run_id: Unique run identifier (hex string).
        project_path: Optional absolute path to the PROJECT directory to pass
            as --workspace to the CLI binary. Defaults to workspace if None.
            Use this when the user selects a project directory for the run
            while keeping run tracking in the app workspace.
        workspace_id: Optional opaque workspace/project ID (from the Flutter
            client's directory selection). Stored in the state file so that
            list_runs and get_pending_prompt can filter by project. When
            supplied, it is written atomically as part of the initial state —
            no post-creation overwrite is needed (and the caller must NOT
            overwrite the state file after this call returns).

    Returns:
        asyncio.Task for the subprocess monitoring coroutine.

    Raises:
        FileNotFoundError: If the orchestrate binary is not found on PATH.
        ValueError: If the binary cannot be located.
    """
    binary_path = locate_binary()
    if binary_path is None:
        raise FileNotFoundError(
            "orchestrate binary not found on PATH. "
            "Install the orchestrator package globally to use system orchestrate."
        )

    # CLI --workspace arg points to the project directory (or app workspace if not set)
    cli_workspace = project_path if project_path is not None else workspace

    # TOCTOU mitigation: re-verify cli_workspace is still a non-symlink directory.
    # The workspace_id was already validated by _resolve_workspace_path in runs.py,
    # but we re-check here in case the path was swapped between validation and launch.
    if project_path is not None:
        try:
            cli_workspace_resolved = cli_workspace.resolve()
            if cli_workspace.is_symlink() or not cli_workspace_resolved.is_dir():
                logger.warning(
                    "cli_workspace %s failed post-resolution safety check "
                    "(symlink or not a directory) — using app workspace",
                    cli_workspace,
                )
                cli_workspace = workspace
        except OSError as exc:
            logger.warning(
                "cli_workspace %s resolution failed (%s) — using app workspace",
                cli_workspace,
                exc,
            )
            cli_workspace = workspace

    args = build_cli_args(request, binary_path, cli_workspace, run_id, config_path=config_path)

    # Ensure the logs directory exists
    logs_dir = workspace / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    # Write initial per-run state file — matches the format used by in-process runs.
    # workspace_id is included here (single atomic write) so no caller needs to
    # overwrite this file to inject workspace_id after the fact.
    state_file = workspace / f"state-{run_id}.json"
    initial_state: dict = {
        "run_id": run_id,
        "status": "running",
        "feature_request": getattr(request, "feature_request", ""),
        "workflow_type": getattr(request, "workflow_type", "feature_development"),
        "start_time": datetime.now(timezone.utc).isoformat(),
        "end_time": None,
        "total_cost_usd": 0.0,
        "steps_completed": 0,
        "steps_total": 0,
        "current_step": "Starting",
        "phases": {},
        "workflow_tasks": [],
    }
    # Include workspace_id when provided — enables list_runs filtering by project
    if workspace_id is not None:
        initial_state["workspace_id"] = workspace_id

    state_file.write_text(json.dumps(initial_state, ensure_ascii=False), encoding="utf-8")

    # JSONL log file path
    jsonl_path = logs_dir / f"run-{run_id}.jsonl"

    # Write initial run_start event to the JSONL log
    _append_jsonl_event(jsonl_path, {
        "event": "run_start",
        "run_id": run_id,
        "workflow_type": getattr(request, "workflow_type", "feature_development"),
        "feature_request": getattr(request, "feature_request", ""),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "via": "system_orchestrate",
    })

    # Launch orchestrate as a session-independent process that writes directly
    # to the JSONL file — no stdout pipe.
    #
    # WHY: A pipe has a fixed OS buffer (64 KB on Linux, 64 KB on macOS).
    # When the orchestrate binary is blocked inside a long MCP tool call it
    # produces no output, but when the call returns it may burst many KB at
    # once.  If the asyncio reader task is even briefly behind, the pipe
    # buffer fills and the subprocess **blocks on write**, stalling the entire
    # run.  Redirecting stdout to a file removes the buffer ceiling: the
    # subprocess writes at full disk speed regardless of whether the API
    # server is reading.
    #
    # start_new_session=True puts the process in its own POSIX session so it
    # keeps running even if the API server dies or is restarted.  The monitor
    # task then polls the JSONL file rather than the pipe.
    sub_env = os.environ.copy()
    sub_env["PYTHONUNBUFFERED"] = "1"

    # Open the JSONL file in append mode and hand the fd to the subprocess.
    # We close our copy immediately after launch so only the child holds it.
    jsonl_fd = os.open(
        str(jsonl_path),
        os.O_WRONLY | os.O_APPEND | os.O_CREAT,
        0o644,
    )
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=jsonl_fd,
            stderr=jsonl_fd,   # merge stderr into the same file
            cwd=str(cli_workspace),
            env=sub_env,
            start_new_session=True,
        )
    finally:
        os.close(jsonl_fd)  # parent no longer needs this fd

    # Store the PID so external tooling (and startup reconciliation) can
    # check liveness with os.kill(pid, 0).
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
        state["pid"] = proc.pid
        state_file.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    except (OSError, json.JSONDecodeError):
        pass

    # Create and return the monitoring task
    task = asyncio.create_task(
        _monitor_subprocess_file(proc, run_id, state_file, jsonl_path),
        name=f"system_orchestrate_{run_id}",
    )
    return task


async def _monitor_subprocess_file(
    proc: asyncio.subprocess.Process,
    run_id: str,
    state_file: Path,
    jsonl_path: Path,
    poll_interval: float = 2.0,
) -> None:
    """Monitor a session-independent subprocess by polling its JSONL output file.

    The subprocess writes directly to *jsonl_path* (no pipe).  This coroutine:
    - Polls the file every *poll_interval* seconds for new JSON event lines
    - Calls _update_state_from_event for each new event so the REST API
      reflects live progress
    - On process exit, writes the final status to the state file and appends
      a run_complete / run_failed event

    Args:
        proc: The running subprocess (launched with start_new_session=True).
        run_id: The run identifier for log attribution.
        state_file: Path to workspace/state-{run_id}.json.
        jsonl_path: Path to workspace/logs/run-{run_id}.jsonl.
        poll_interval: Seconds between file polls (default 2s).
    """
    # The run_start event written before launch is line 0.  Start processing
    # from line 1 so we don't re-process it for state updates.
    lines_processed = 1

    while True:
        # Read any new lines written by the subprocess since last poll.
        lines_processed = _poll_jsonl_state(
            jsonl_path, state_file, run_id, lines_processed
        )

        # Break as soon as the process has exited (returncode is set by asyncio
        # when the child is reaped — no blocking wait needed here).
        if proc.returncode is not None:
            break

        await asyncio.sleep(poll_interval)

    # One final poll to catch lines flushed just before exit.
    _poll_jsonl_state(jsonl_path, state_file, run_id, lines_processed)

    return_code = await proc.wait()
    final_status = "completed" if return_code == 0 else "failed"

    # Write final status to the per-run state file.
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
        state["status"] = final_status
        state["end_time"] = datetime.now(timezone.utc).isoformat()
        if return_code != 0:
            state["error"] = f"orchestrate exited with code {return_code}"
        state_file.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(
            "Failed to update state file for run %s on completion: %s", run_id, exc
        )

    # Append a completion sentinel so the WebSocket streaming loop knows to close.
    completion_event: dict = {
        "event": "run_complete" if return_code == 0 else "run_failed",
        "run_id": run_id,
        "status": final_status,
        "exit_code": return_code,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if return_code != 0:
        completion_event["error"] = f"orchestrate subprocess exited with code {return_code}"

    _append_jsonl_event(jsonl_path, completion_event)

    if return_code != 0:
        logger.warning(
            "System orchestrate run %s failed with exit code %d", run_id, return_code
        )
    else:
        logger.info("System orchestrate run %s completed successfully", run_id)


def _poll_jsonl_state(
    jsonl_path: Path,
    state_file: Path,
    run_id: str,
    lines_processed: int,
) -> int:
    """Read new lines from *jsonl_path* starting at *lines_processed* and update state.

    Returns the updated lines_processed count.
    """
    try:
        raw = jsonl_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return lines_processed

    all_lines = raw.splitlines()
    new_lines = all_lines[lines_processed:]
    for line in new_lines:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
            if isinstance(event, dict):
                _update_state_from_event(state_file, event, run_id)
        except (json.JSONDecodeError, ValueError):
            pass

    return lines_processed + len(new_lines)


def _update_state_from_event(state_file: Path, event: dict, run_id: str) -> None:  # noqa: ARG001
    """Update the per-run state file with progress extracted from structured events.

    Called for each JSON event emitted by the subprocess. Only events that
    carry phase/step/cost information trigger a state file update — plain
    log lines are ignored to avoid unnecessary I/O.
    """
    event_type = event.get("event", "")

    # Only process events that carry actionable state changes
    if event_type not in (
        "run_start", "run_complete", "task_invoke", "task_result",
        "agent_result", "auto_prd", "budget_warning",
    ):
        return

    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return

    if event_type == "task_invoke":
        step = event.get("step") or event.get("phase", "")
        if step:
            state["current_step"] = step
            # Mark this phase as running
            phases = state.setdefault("phases", {})
            phase_key = step.lower().replace(" ", "_")
            if phase_key not in phases:
                phases[phase_key] = {"status": "running"}
            elif phases[phase_key].get("status") != "completed":
                phases[phase_key]["status"] = "running"

    elif event_type == "task_result":
        success = event.get("success", False)
        step = event.get("step") or event.get("phase", "")
        cost = event.get("cost_usd", 0.0)
        if step:
            phases = state.setdefault("phases", {})
            phase_key = step.lower().replace(" ", "_")
            if success:
                phases[phase_key] = {
                    "status": "completed",
                    "cost_usd": cost,
                }
                # Count completed steps
                completed = sum(
                    1 for p in phases.values()
                    if p.get("status") == "completed"
                )
                state["steps_completed"] = completed
                state["steps_total"] = max(state.get("steps_total", 0), len(phases))
            else:
                phases[phase_key] = {
                    "status": "failed",
                    "error": event.get("error", ""),
                    "cost_usd": cost,
                }

    elif event_type == "agent_result":
        cost = event.get("cost_usd", 0.0)
        state["total_cost_usd"] = state.get("total_cost_usd", 0.0) + cost

    elif event_type == "run_complete":
        state["status"] = "completed"
        state["total_cost_usd"] = event.get("total_cost_usd", state.get("total_cost_usd", 0.0))
        # Merge phase data from the binary's final event
        if "phases" in event:
            for key, phase_data in event["phases"].items():
                phases = state.setdefault("phases", {})
                if isinstance(phase_data, dict):
                    phases[key] = phase_data

    try:
        state_file.write_text(
            json.dumps(state, ensure_ascii=False), encoding="utf-8"
        )
    except OSError:
        pass


def _append_jsonl_event(jsonl_path: Path, event: dict) -> None:
    """Append a single JSON event to the JSONL log file.

    Each event is written as a separate JSON object followed by a newline,
    matching the format expected by the WebSocket streaming endpoint and
    the run-detail screen.

    Args:
        jsonl_path: Path to the JSONL log file.
        event: Dict to serialize as a JSON line.
    """
    try:
        with jsonl_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.warning("Failed to append event to %s: %s", jsonl_path, exc)
