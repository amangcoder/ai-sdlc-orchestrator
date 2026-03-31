"""Global run registry — makes ALL orchestrate runs discoverable.

File-based registry location: ~/.orchestrator/runs/{run_id}.json

Each file entry is a lightweight JSON pointer containing paths to the
actual state and JSONL files.  When a ``RunRepository`` is injected, the
DB is used as the authoritative source; the file-based registry acts as
a fallback for local/legacy runs.

The file registry is purely additive and never deletes entries.  Stale
entries are harmless: the mobile API validates that pointed-to files
exist before serving data.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from orchestrator.db.repositories.runs import RunRepository

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
    run_repo: "RunRepository | None" = None,
) -> Path | None:
    """Register a run so the mobile API and dashboard can discover it.

    When ``run_repo`` is provided the entry is also written to the DB
    (``runs`` table) via an async upsert scheduled on the current event loop.

    Returns the file-based registry path, or None on failure (best-effort).
    """
    import asyncio

    if run_repo is not None:
        # DB registration happens via save_run_state() upsert; nothing extra needed.
        # Just proceed to file registry for the pointer file.
        pass

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


def update_run(
    run_id: str,
    run_repo: "RunRepository | None" = None,
    **updates: Any,
) -> None:
    """Merge *updates* into an existing registry entry (best-effort).

    When ``run_repo`` is provided, also updates the ``runs`` table row.
    """
    import asyncio

    if run_repo is not None and updates:
        # Extract known DB columns from updates
        status = updates.get("status")
        total_cost_usd = updates.get("total_cost_usd")
        db_updates: dict[str, Any] = {}
        if status:
            db_updates["status"] = status
        if total_cost_usd is not None:
            db_updates["total_cost_usd"] = total_cost_usd
        if db_updates:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.ensure_future(run_repo.update_status(run_id, **db_updates))
                else:
                    loop.run_until_complete(run_repo.update_status(run_id, **db_updates))
            except Exception as exc:
                logger.debug("DB update_run failed for %s: %s", run_id, exc)

    # Always update the file registry too (backward-compat / fallback)
    path = REGISTRY_DIR / f"{run_id}.json"
    try:
        entry = json.loads(path.read_text(encoding="utf-8"))
        entry.update(updates)
        path.write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")
    except (OSError, json.JSONDecodeError):
        pass


def lookup_run(
    run_id: str,
    run_repo: "RunRepository | None" = None,
) -> dict[str, Any] | None:
    """Return the registry entry for *run_id*, or None.

    Checks the DB first when ``run_repo`` is provided, then falls back to
    the file registry.
    """
    import asyncio

    if run_repo is not None:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Can't await here; fall through to file fallback
                pass
            else:
                result = loop.run_until_complete(run_repo.get(run_id))
                if result is not None:
                    return result
        except Exception as exc:
            logger.debug("DB lookup_run failed for %s: %s", run_id, exc)

    path = REGISTRY_DIR / f"{run_id}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def list_registered_runs(
    max_results: int = 200,
    run_repo: "RunRepository | None" = None,
) -> list[dict[str, Any]]:
    """Return all registry entries, newest-first.

    When ``run_repo`` is provided, queries the DB for the authoritative list.
    Falls back to file scanning for local/legacy runs not in the DB.
    """
    import asyncio

    if run_repo is not None:
        try:
            loop = asyncio.get_event_loop()
            if not loop.is_running():
                return loop.run_until_complete(run_repo.list(limit=max_results))
        except Exception as exc:
            logger.debug("DB list_registered_runs failed: %s", exc)

    # File-based fallback
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
