"""Crash detection, heartbeat, signal handling, and recovery for the orchestrator.

This module provides:
- CrashRecoveryManager: activated at run start, maintains heartbeat, catches SIGTERM/atexit
- detect_crash(): determines if a loaded RunState represents a crashed run
- recover_from_crash(): resets crashed tasks, cleans orphaned worktrees, prepares for resume
"""

from __future__ import annotations

import asyncio
import atexit
import logging
import os
import signal
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from orchestrator.models import (
    CrashEvent,
    PhaseStatus,
    RunStatus,
    TaskStatus,
    RunState,
)

logger = logging.getLogger(__name__)

_HEARTBEAT_INTERVAL_SECONDS = 10
_HEARTBEAT_STALE_THRESHOLD_SECONDS = 30  # 3x heartbeat interval


class CrashRecoveryManager:
    """Manages crash detection, heartbeat, signal handlers, and state flushing.

    Activated at run start via activate(). Maintains a periodic heartbeat that
    updates last_heartbeat_at in state.json so that a future resume can distinguish
    "actively running" from "crashed mid-run."

    On SIGTERM or interpreter exit, performs an emergency flush: marks in-progress
    tasks as CRASHED, syncs cost accumulators, and writes state.json.
    """

    def __init__(
        self,
        state: RunState,
        workspace: Path,
        run_logger: object | None = None,
    ) -> None:
        self.state = state
        self.workspace = workspace
        self.run_logger = run_logger  # RunLogger instance (has .cumulative_cost_usd)
        self._heartbeat_task: asyncio.Task | None = None
        self._original_sigterm = signal.SIG_DFL
        self._atexit_registered = False
        self._activated = False

    def activate(self) -> None:
        """Set PID, install signal handlers, register atexit. Call once at run start."""
        self.state.pid = os.getpid()
        self.state.last_heartbeat_at = datetime.now(timezone.utc)
        self._install_signal_handlers()
        self._install_atexit()
        self._activated = True
        logger.info(f"Crash recovery activated (PID {self.state.pid})")

    async def start_heartbeat(self) -> None:
        """Start the async heartbeat loop. Must be called from a running event loop."""
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def stop_heartbeat(self) -> None:
        """Stop heartbeat and perform final state flush."""
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None
        self._flush_state()

    def deactivate(self) -> None:
        """Clear crash-recovery fields on clean completion."""
        self.state.pid = None
        self.state.last_heartbeat_at = None
        self.state.active_worktrees.clear()
        self._activated = False

    # --- Heartbeat ---

    async def _heartbeat_loop(self) -> None:
        """Periodically update last_heartbeat_at and flush state to disk."""
        while True:
            await asyncio.sleep(_HEARTBEAT_INTERVAL_SECONDS)
            self.state.last_heartbeat_at = datetime.now(timezone.utc)
            self._flush_state()

    # --- Signal Handlers ---

    def _install_signal_handlers(self) -> None:
        """Install SIGTERM handler. SIGINT is handled by InterruptManager."""
        self._original_sigterm = signal.getsignal(signal.SIGTERM)
        try:
            loop = asyncio.get_running_loop()
            loop.add_signal_handler(signal.SIGTERM, self._handle_sigterm)
        except RuntimeError:
            signal.signal(signal.SIGTERM, lambda *_: self._handle_sigterm())

    def _handle_sigterm(self) -> None:
        """On SIGTERM: emergency flush, then re-raise for default termination."""
        logger.warning("SIGTERM received — performing emergency state flush")
        self._emergency_flush("SIGTERM")
        # Restore default handler and re-raise
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        os.kill(os.getpid(), signal.SIGTERM)

    def _install_atexit(self) -> None:
        """Register atexit handler as last-resort state flush."""
        if not self._atexit_registered:
            atexit.register(self._atexit_flush)
            self._atexit_registered = True

    def _atexit_flush(self) -> None:
        """Last-resort flush on interpreter exit (uncaught exceptions, sys.exit)."""
        if self._activated and self.state.status == RunStatus.RUNNING:
            logger.warning("Process exiting with run still RUNNING — emergency flush")
            self._emergency_flush("atexit")

    # --- Emergency Flush ---

    def _emergency_flush(self, trigger: str) -> None:
        """Mark in-progress work as CRASHED, sync cost, save state.

        Called from signal handlers and atexit — must be safe to call in
        restricted contexts (no async, no locks that might deadlock).
        """
        # Sync cost from RunLogger if available
        if self.run_logger and hasattr(self.run_logger, "cumulative_cost_usd"):
            self.state.total_cost_usd = max(
                self.state.total_cost_usd,
                self.run_logger.cumulative_cost_usd,
            )

        # Mark in-progress tasks as CRASHED
        crashed_tasks = []
        for task in self.state.workflow_tasks:
            if task.status == TaskStatus.IN_PROGRESS:
                task.status = TaskStatus.CRASHED
                task.error = f"Process terminated ({trigger})"
                crashed_tasks.append(task.task_id)

        # Mark in-progress phases as FAILED (legacy mode)
        for phase in self.state.phases.values():
            if phase.status == PhaseStatus.RUNNING:
                phase.status = PhaseStatus.FAILED
                phase.error = f"Process terminated ({trigger})"

        self.state.status = RunStatus.CRASHED
        self.state.last_heartbeat_at = datetime.now(timezone.utc)

        if crashed_tasks:
            logger.warning(f"Marked {len(crashed_tasks)} tasks as CRASHED: {crashed_tasks}")

        # Log the crash event
        if self.run_logger and hasattr(self.run_logger, "log_event"):
            try:
                self.run_logger.log_event("crash", {
                    "trigger": trigger,
                    "step": self.state.current_step,
                    "crashed_tasks": crashed_tasks,
                    "cost_usd": self.state.total_cost_usd,
                })
            except Exception:
                pass  # Best effort in crash path

        self._flush_state()

    def _flush_state(self) -> None:
        """Best-effort state flush — called from signal handlers and heartbeat."""
        try:
            from orchestrator.persistence import save_run_state
            save_run_state(self.state, self.workspace)
        except Exception as exc:
            logger.error(f"Failed to flush state on crash: {exc}")


# --- Crash Detection (called on resume) ---


def detect_crash(state: RunState) -> CrashEvent | None:
    """Detect if a loaded RunState represents a crashed run.

    A run is considered crashed if:
    1. status is RUNNING or CRASHED, AND
    2. The owning PID is dead, OR last_heartbeat_at is stale (>30s old)

    Returns a CrashEvent if crash detected, None otherwise.
    """
    if state.status not in (RunStatus.RUNNING, RunStatus.CRASHED):
        return None

    # Check if the owning process is still alive
    pid_alive = False
    if state.pid:
        try:
            os.kill(state.pid, 0)
            pid_alive = True
        except (OSError, ProcessLookupError):
            pass

    # Check heartbeat staleness
    heartbeat_stale = True  # Default: no heartbeat = old-format state, assume stale
    if state.last_heartbeat_at:
        # Handle both timezone-aware and naive datetimes
        now = datetime.now(timezone.utc)
        heartbeat = state.last_heartbeat_at
        if heartbeat.tzinfo is None:
            heartbeat = heartbeat.replace(tzinfo=timezone.utc)
        age = (now - heartbeat).total_seconds()
        heartbeat_stale = age > _HEARTBEAT_STALE_THRESHOLD_SECONDS

    # If PID is alive and heartbeat is fresh, the run is genuinely active
    if pid_alive and not heartbeat_stale:
        return None

    # Crash detected
    tasks_in_progress = [
        t.task_id for t in state.workflow_tasks
        if t.status in (TaskStatus.IN_PROGRESS, TaskStatus.CRASHED)
    ]

    return CrashEvent(
        detected_at=datetime.now(timezone.utc),
        crashed_pid=state.pid,
        last_heartbeat=state.last_heartbeat_at,
        step_at_crash=state.current_step,
        tasks_in_progress=tasks_in_progress,
        cost_at_crash=state.total_cost_usd,
    )


# --- Crash Recovery (called on resume after detection) ---


def recover_from_crash(state: RunState, project_root: Path) -> None:
    """Apply recovery actions to a crashed RunState.

    1. Record the crash event in history
    2. Reset CRASHED/IN_PROGRESS tasks to PENDING
    3. Clean up orphaned worktrees
    4. Reset status to RUNNING for re-execution
    5. Increment crash_count
    """
    crash = detect_crash(state)
    if not crash:
        return

    # Record crash in history
    state.crash_history.append(crash)
    state.crash_count += 1
    logger.info(
        f"Recovering from crash #{state.crash_count}: "
        f"step='{crash.step_at_crash}', "
        f"tasks_in_progress={crash.tasks_in_progress}, "
        f"cost_at_crash=${crash.cost_at_crash:.4f}"
    )

    # Reset crashed/in-progress tasks to PENDING for retry
    reset_count = 0
    for task in state.workflow_tasks:
        if task.status in (TaskStatus.IN_PROGRESS, TaskStatus.CRASHED):
            task.status = TaskStatus.PENDING
            task.error = None
            reset_count += 1
    if reset_count:
        logger.info(f"Reset {reset_count} crashed/in-progress tasks to PENDING")

    # Reset crashed/in-progress phases to PENDING (legacy mode)
    for phase_name, phase in state.phases.items():
        if phase.status in (PhaseStatus.RUNNING, PhaseStatus.FAILED):
            if phase.error and "Process terminated" in phase.error:
                phase.status = PhaseStatus.PENDING
                phase.error = None
                logger.info(f"Reset crashed phase '{phase_name}' to PENDING")

    # Clean up orphaned worktrees
    cleanup_orphaned_worktrees(state, project_root)

    # Reset run status for re-execution
    state.status = RunStatus.RUNNING
    state.pid = None
    state.last_heartbeat_at = None
    state.interrupted = False


def cleanup_orphaned_worktrees(state: RunState, project_root: Path) -> None:
    """Remove worktrees registered in state that were orphaned by a crash."""
    if not state.active_worktrees:
        return

    cleaned = []
    for wt in state.active_worktrees:
        wt_path = Path(wt.worktree_dir)
        try:
            if wt_path.exists():
                subprocess.run(
                    ["git", "worktree", "remove", "--force", str(wt_path)],
                    cwd=project_root,
                    capture_output=True,
                    timeout=10,
                )
            # Delete the branch even if worktree dir was already gone
            subprocess.run(
                ["git", "branch", "-D", wt.branch_name],
                cwd=project_root,
                capture_output=True,
                timeout=10,
            )
            cleaned.append(wt.task_id)
        except Exception as exc:
            logger.warning(f"Failed to clean worktree for task {wt.task_id}: {exc}")

    # Prune any worktrees git knows about but whose directories are gone
    try:
        subprocess.run(
            ["git", "worktree", "prune"],
            cwd=project_root,
            capture_output=True,
            timeout=10,
        )
    except Exception:
        pass

    state.active_worktrees.clear()
    if cleaned:
        logger.info(f"Cleaned up {len(cleaned)} orphaned worktrees: {cleaned}")
