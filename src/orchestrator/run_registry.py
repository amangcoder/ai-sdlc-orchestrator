"""Global run registry — makes ALL orchestrate runs discoverable.

Registry location: ~/.orchestrator/runs/{run_id}.json

Each entry is a lightweight JSON pointer file containing paths to the
actual state and JSONL files.  The mobile API (and dashboard) scan
this directory to discover runs started from ANY source — CLI terminal,
mobile app subprocess, IDE integration, etc.

The registry is purely additive and never deletes entries.  Stale entries
are harmless: the mobile API validates that the pointed-to files exist
before serving data.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

REGISTRY_DIR = Path.home() / ".orchestrator" / "runs"


def register_run(
    run_id: str,
    project_dir: Path,
    jsonl_path: Path,
    state_path: Path,
    feature_request: str = "",
    workflow_type: str = "feature_development",
    source: str = "cli",
) -> Path | None:
    """Register a run so the mobile API can discover it.

    Returns the registry file path, or None on failure (best-effort).
    """
    try:
        REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
        entry = {
            "run_id": run_id,
            "pid": os.getpid(),
            "project_dir": str(project_dir),
            "jsonl_path": str(jsonl_path),
            "state_path": str(state_path),
            "feature_request": feature_request,
            "workflow_type": workflow_type,
            "start_time": datetime.now(timezone.utc).isoformat(),
            "source": source,
        }
        path = REGISTRY_DIR / f"{run_id}.json"
        path.write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")
        return path
    except OSError as exc:
        logger.debug("Failed to register run %s: %s", run_id, exc)
        return None


def update_run(run_id: str, **updates: Any) -> None:
    """Merge *updates* into an existing registry entry (best-effort)."""
    path = REGISTRY_DIR / f"{run_id}.json"
    try:
        entry = json.loads(path.read_text(encoding="utf-8"))
        entry.update(updates)
        path.write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")
    except (OSError, json.JSONDecodeError):
        pass


def lookup_run(run_id: str) -> dict[str, Any] | None:
    """Return the registry entry for *run_id*, or None."""
    path = REGISTRY_DIR / f"{run_id}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def list_registered_runs(max_results: int = 200) -> list[dict[str, Any]]:
    """Return all registry entries, newest-first by file mtime."""
    if not REGISTRY_DIR.exists():
        return []

    runs: list[dict[str, Any]] = []
    for entry_file in REGISTRY_DIR.glob("*.json"):
        try:
            entry = json.loads(entry_file.read_text(encoding="utf-8"))
            entry["_reg_mtime"] = entry_file.stat().st_mtime
            runs.append(entry)
        except (OSError, json.JSONDecodeError):
            continue

    runs.sort(key=lambda x: x.get("_reg_mtime", 0), reverse=True)
    return runs[:max_results]
