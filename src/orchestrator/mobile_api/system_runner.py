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

    # Required: workspace path
    args += ["--workspace", str(workspace_path.resolve())]

    # Required: feature request text
    feature_request = getattr(request, "feature_request", "")
    if feature_request:
        args += ["--feature-request", feature_request]

    # Optional: workflow type
    workflow_type = getattr(request, "workflow_type", None)
    if workflow_type:
        args += ["--workflow", str(workflow_type)]

    # Optional: phase to run
    phase = getattr(request, "phase", None)
    if phase is not None:
        args += ["--phase", str(phase)]

    # Optional: from-phase (resume from)
    from_phase = getattr(request, "from_phase", None)
    if from_phase is not None:
        args += ["--from-phase", str(from_phase)]

    # Optional: speed mode
    mode = getattr(request, "mode", None)
    if mode is not None:
        args += ["--mode", str(mode)]

    # Optional: max budget
    max_budget_usd = getattr(request, "max_budget_usd", None)
    if max_budget_usd is not None:
        args += ["--max-budget", str(max_budget_usd)]

    # Optional: speed mode
    speed = getattr(request, "speed", None)
    if speed is not None:
        args += ["--speed", str(speed)]

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

    # Optional: confirm flag
    confirm = getattr(request, "confirm", None)
    if confirm is True:
        args.append("--confirm")

    # Optional: tech stack confirmation (negated flag)
    tech_stack_confirmation = getattr(request, "tech_stack_confirmation", None)
    if tech_stack_confirmation is False:
        args.append("--no-confirm-tech-stack")

    # Optional: checklist verify (negated flag)
    checklist_verify = getattr(request, "checklist_verify", None)
    if checklist_verify is False:
        args.append("--no-checklist-verify")

    # Optional: max concurrent agents
    max_concurrent_agents = getattr(request, "max_concurrent_agents", None)
    if max_concurrent_agents is not None and max_concurrent_agents > 0:
        args += ["--max-concurrent-agents", str(max_concurrent_agents)]

    # Optional: log format
    log_format = getattr(request, "log_format", None)
    if log_format is not None:
        args += ["--log-format", str(log_format)]

    # Optional: resume run ID
    resume_run_id = getattr(request, "resume_run_id", None)
    if resume_run_id is not None:
        args += ["--resume-run", str(resume_run_id)]

    # Append run-id so the binary writes to the right state file
    args += ["--run-id", run_id]

    return args


async def start_subprocess_run(
    request: object,
    workspace: Path,
    run_id: str,
    project_path: Path | None = None,
    workspace_id: str | None = None,
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

    args = build_cli_args(request, binary_path, cli_workspace, run_id)

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

    # Launch the subprocess — use cli_workspace as cwd so both --workspace flag
    # and process working directory agree (TASK-017 fix: was str(workspace)).
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,  # merge stderr into stdout
        cwd=str(cli_workspace),
    )

    # Create and return the monitoring task
    task = asyncio.create_task(
        _monitor_subprocess(proc, run_id, state_file, jsonl_path),
        name=f"system_orchestrate_{run_id}",
    )
    return task


async def _monitor_subprocess(
    proc: asyncio.subprocess.Process,
    run_id: str,
    state_file: Path,
    jsonl_path: Path,
) -> None:
    """Monitor subprocess output and update state/log files on completion.

    Reads stdout/stderr line by line and appends each line as a log event
    to the JSONL file. Updates the per-run state file on process exit.

    Args:
        proc: The running subprocess.
        run_id: The run identifier for log attribution.
        state_file: Path to workspace/state-{run_id}.json.
        jsonl_path: Path to workspace/logs/run-{run_id}.jsonl.
    """
    # Stream stdout/stderr lines to the JSONL log
    if proc.stdout:
        try:
            async for line_bytes in proc.stdout:
                line = line_bytes.decode("utf-8", errors="replace").rstrip()
                if not line:
                    continue

                # Try to parse as JSON (orchestrate outputs JSON events)
                try:
                    event = json.loads(line)
                    if isinstance(event, dict):
                        # Append the event as-is (it's already a structured event)
                        _append_jsonl_event(jsonl_path, event)
                    else:
                        _append_jsonl_event(jsonl_path, {
                            "event": "log",
                            "run_id": run_id,
                            "message": line,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        })
                except (json.JSONDecodeError, ValueError):
                    # Plain text output — wrap as a log event
                    _append_jsonl_event(jsonl_path, {
                        "event": "log",
                        "run_id": run_id,
                        "message": line,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })
        except Exception as exc:
            logger.warning("Error reading subprocess output for run %s: %s", run_id, exc)

    # Wait for process to finish
    return_code = await proc.wait()
    final_status = "completed" if return_code == 0 else "failed"

    # Write final status to the per-run state file
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

    # Append completion event to JSONL log
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
