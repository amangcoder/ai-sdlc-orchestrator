"""Watchdog wrapper — auto-resumes the orchestrator after abnormal termination.

Usage:
    orchestrate-watchdog "Build a todo app"              # wraps orchestrate
    orchestrate-watchdog --max-restarts 5 "Build a todo app"

The watchdog spawns `orchestrate` as a subprocess and monitors its exit code.
On abnormal exit (non-zero, excluding graceful failures like budget exceeded),
it automatically restarts with `--resume` to continue from the last checkpoint.

Graceful exits (code 0 or 1) are not retried — code 1 means a phase failed
but the process exited normally. Only crashes (signals, OOM, etc.) trigger
auto-resume.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import logging

logger = logging.getLogger(__name__)

# Exponential backoff delays (seconds) between restart attempts
BACKOFF_DELAYS = [2, 8, 32]

# Exit codes that should NOT trigger auto-resume
# 0 = success, 1 = phase failed (normal exit)
_GRACEFUL_EXIT_CODES = {0, 1}

# Negative exit codes on Unix indicate signal kills (e.g., -9 = SIGKILL, -11 = SIGSEGV)
_CRASH_SIGNALS = "SIGKILL, SIGSEGV, SIGABRT, SIGBUS"


def _validate_state_file(workspace_dir: str = "workspace") -> bool:
    """Validate that the state file exists and contains valid JSON.

    Also checks for crash indicators and logs diagnostic info if crash detected.
    Returns True if the state file is valid and resumable, False otherwise.
    """
    from pathlib import Path

    state_path = Path(workspace_dir) / "state.json"
    if not state_path.exists():
        logger.warning(f"State file not found: {state_path}")
        return False

    try:
        data = json.loads(state_path.read_text())
        if not isinstance(data, dict):
            logger.warning(f"State file is not a JSON object: {state_path}")
            return False

        # Check for crash indicators
        try:
            from orchestrator.models import RunState
            from orchestrator.crash_recovery import detect_crash

            state = RunState.model_validate(data)
            crash = detect_crash(state)
            if crash:
                logger.warning(
                    f"Crash detected in saved state: "
                    f"step='{crash.step_at_crash}', "
                    f"tasks_in_progress={len(crash.tasks_in_progress)}, "
                    f"cost_at_crash=${crash.cost_at_crash:.4f}"
                )
                if crash.last_heartbeat:
                    logger.info(f"Last heartbeat: {crash.last_heartbeat.isoformat()}")
        except Exception as exc:
            logger.debug(f"Could not analyze crash state: {exc}")

        return True
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning(f"State file is corrupt or unreadable: {state_path} — {exc}")
        return False


def run_with_watchdog(
    args: list[str],
    max_restarts: int = 3,
    cooldown_seconds: float = 2.0,
) -> int:
    """Run the orchestrator with automatic crash recovery.

    Args:
        args: Arguments to pass to `orchestrate` (e.g., ["Build a todo app", "--workflow", "feature"]).
        max_restarts: Maximum number of auto-resume attempts before giving up.
        cooldown_seconds: Seconds to wait between restarts to avoid tight loops.

    Returns:
        The final exit code from the orchestrator process.
    """
    restarts = 0
    is_resume = False

    while True:
        cmd = [sys.executable, "-m", "orchestrator.main"]

        if is_resume:
            # Validate state file before attempting resume
            if _validate_state_file():
                cmd.append("--resume")
            else:
                logger.error(
                    "Cannot resume — state file is missing or corrupt. "
                    "Starting fresh is not safe; exiting."
                )
                return 1

        cmd.extend(args)

        logger.info(
            f"{'Resuming' if is_resume else 'Starting'} orchestrator "
            f"(attempt {restarts + 1}/{max_restarts + 1}): {' '.join(cmd)}"
        )

        try:
            result = subprocess.run(cmd, env=os.environ)
            exit_code = result.returncode
        except KeyboardInterrupt:
            logger.info("Watchdog received Ctrl+C — exiting without restart")
            return 130  # Standard Ctrl+C exit code

        if exit_code in _GRACEFUL_EXIT_CODES:
            logger.info(f"Orchestrator exited gracefully (code {exit_code})")
            return exit_code

        # Abnormal exit — potential crash
        restarts += 1
        if exit_code < 0:
            signal_num = -exit_code
            logger.warning(
                f"Orchestrator killed by signal {signal_num} — "
                f"crash detected ({_CRASH_SIGNALS})"
            )
        else:
            logger.warning(f"Orchestrator exited with code {exit_code}")

        if restarts > max_restarts:
            logger.error(
                f"Max restarts ({max_restarts}) exceeded — giving up. "
                f"Resume manually with: orchestrate --resume <feature_request>"
            )
            return exit_code

        delay = BACKOFF_DELAYS[min(restarts - 1, len(BACKOFF_DELAYS) - 1)]
        logger.info(
            f"Auto-resuming in {delay}s (exponential backoff)... "
            f"({restarts}/{max_restarts} restarts used)"
        )
        time.sleep(delay)
        is_resume = True


def main() -> None:
    """CLI entry point for orchestrate-watchdog."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="orchestrate-watchdog",
        description="Watchdog wrapper that auto-resumes the orchestrator after crashes",
    )
    parser.add_argument(
        "--max-restarts", type=int, default=3,
        help="Maximum number of auto-resume attempts (default: 3)",
    )
    parser.add_argument(
        "--cooldown", type=float, default=2.0,
        help="Seconds to wait between restarts (default: 2.0)",
    )

    # Everything else is passed through to orchestrate
    parser.add_argument("orchestrate_args", nargs=argparse.REMAINDER,
                        help="Arguments to pass to orchestrate")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="[watchdog] %(message)s",
    )

    # Strip leading '--' if present (argparse REMAINDER quirk)
    orchestrate_args = args.orchestrate_args
    if orchestrate_args and orchestrate_args[0] == "--":
        orchestrate_args = orchestrate_args[1:]

    if not orchestrate_args:
        parser.error("No arguments provided for orchestrate")

    exit_code = run_with_watchdog(
        args=orchestrate_args,
        max_restarts=args.max_restarts,
        cooldown_seconds=args.cooldown,
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
