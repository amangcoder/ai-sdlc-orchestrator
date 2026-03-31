"""Atomic file writes and centralized state persistence."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from orchestrator.models import RunState

if TYPE_CHECKING:
    from orchestrator.db.repositories.runs import RunRepository

log = logging.getLogger(__name__)

# Module-level lock for state writes — ensures only one thread writes at a time
_state_write_lock = threading.Lock()


def atomic_write(target: Path, data: str) -> None:
    """Write data to target atomically via temp file + os.replace.

    On POSIX, os.replace is atomic, so readers never see a partially
    written file. If the write fails, the original file is untouched.
    """
    fd, tmp = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
    try:
        os.write(fd, data.encode())
        os.fsync(fd)
        os.close(fd)
        os.replace(tmp, target)  # atomic on POSIX
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def save_run_state(
    state: RunState,
    workspace: Path,
    run_repo: "RunRepository | None" = None,
    source: str = "cli",
    project_name: str = "",
    write_sidecar: bool = True,
) -> None:
    """Centralized state save — writes to DB (when repo provided) and filesystem.

    When ``run_repo`` is provided the state is upserted into the ``runs`` table.
    Filesystem writes are controlled by ``write_sidecar`` (default True for
    backward-compatibility and crash recovery).

    Thread-safe: uses a module-level lock so concurrent saves from parallel
    agents don't corrupt the file.

    Args:
        state: The current ``RunState``.
        workspace: The run's workspace directory (for filesystem sidecar).
        run_repo: Optional ``RunRepository`` for DB upsert.
        source: Run source tag (``cli``, ``web``, ``mobile``).
        project_name: Human-readable project name for the DB row.
        write_sidecar: Whether to also write state.json / state-{id}.json.
    """
    import asyncio

    data = json.dumps(state.model_dump(), indent=2, default=str)

    # 1. DB upsert (best-effort — never block the pipeline on DB errors)
    if run_repo is not None:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Schedule the coroutine as a task without awaiting it —
                # the drain will happen in the current event loop iteration.
                asyncio.ensure_future(
                    run_repo.upsert(state, source=source, project_name=project_name)
                )
            else:
                loop.run_until_complete(
                    run_repo.upsert(state, source=source, project_name=project_name)
                )
        except Exception as exc:
            log.warning("DB state upsert failed for run %s: %s", state.run_id, exc)

    # 2. Filesystem sidecar (belt-and-suspenders for crash recovery)
    if write_sidecar:
        with _state_write_lock:
            atomic_write(workspace / "state.json", data)

            # Backward compatibility: flat-layout runs/ subdirectory check.
            if "runs" not in workspace.parts:
                atomic_write(workspace / f"state-{state.run_id}.json", data)
