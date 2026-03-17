"""Atomic file writes and centralized state persistence."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path

from orchestrator.models import RunState

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


def save_run_state(state: RunState, workspace: Path) -> None:
    """Centralized state save — writes both state.json and state-<run_id>.json atomically.

    Thread-safe: uses a module-level lock so concurrent saves from parallel
    agents don't corrupt the file.
    """
    data = json.dumps(state.model_dump(), indent=2, default=str)
    with _state_write_lock:
        atomic_write(workspace / "state.json", data)
        atomic_write(workspace / f"state-{state.run_id}.json", data)
