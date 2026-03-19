"""Projects listing endpoint for the Mobile API.

Endpoint:
  GET /api/v1/projects — list ~/Projects subdirectories as project cards

Returns a JSON array of {id, name, last_modified, run_count} with no raw
filesystem paths in any entry (security: path disclosure prevention).

Auth: handled by the existing AuthMiddleware applied in app.py.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Request

from orchestrator.mobile_api.models import ProjectEntry

logger = logging.getLogger(__name__)

router = APIRouter()


def _count_runs_for_workspace(workspace_root: Path, opaque_id: str) -> int:
    """Count the number of state-*.json runs associated with a workspace.

    Reads all state-*.json files in workspace_root and filters by the
    'workspace_id' field matching the given opaque_id.

    Called lazily via the in-memory index built by _build_run_count_index()
    so subsequent project enumeration calls are O(1).

    Args:
        workspace_root: The orchestrator workspace directory (contains state-*.json files).
        opaque_id: The opaque directory ID to match against.

    Returns:
        Number of runs associated with this workspace/project.
    """
    count = 0
    try:
        for state_file in workspace_root.glob("state-*.json"):
            try:
                data = json.loads(state_file.read_text(encoding="utf-8"))
                if data.get("workspace_id") == opaque_id:
                    count += 1
            except (json.JSONDecodeError, OSError):
                continue
    except OSError as exc:
        logger.debug("OSError scanning workspace for run counts: %s", exc)
    return count


def _build_run_count_index(workspace_root: Path) -> dict[str, int]:
    """Build an in-memory index of workspace_id → run count.

    Scans all state-*.json files once and returns a mapping.
    Used by list_projects() to compute run_count for each project in O(1)
    per project after the single O(n) scan of the workspace.

    Args:
        workspace_root: The orchestrator workspace directory.

    Returns:
        Dict mapping opaque workspace_id → count of associated runs.
    """
    index: dict[str, int] = defaultdict(int)
    try:
        for state_file in workspace_root.glob("state-*.json"):
            try:
                data = json.loads(state_file.read_text(encoding="utf-8"))
                ws_id = data.get("workspace_id")
                if ws_id:
                    index[ws_id] += 1
            except (json.JSONDecodeError, OSError):
                continue
    except OSError as exc:
        logger.debug("OSError building run count index: %s", exc)
    return dict(index)


# ── GET /api/v1/projects ───────────────────────────────────────────────────

@router.get("/projects")
def list_projects(request: Request) -> list[dict]:
    """List ~/Projects subdirectories as project cards.

    Returns a JSON array of {id, name, last_modified (ISO-8601), run_count}.
    No 'path' field is present in any entry (path disclosure prevention).

    Returns an empty array (not 404) when projects_root is not configured
    or has no subdirectories.

    Uses sync def to avoid blocking the event loop — file I/O is acceptable
    in a sync FastAPI handler (runs in a thread pool automatically).
    """
    projects_root: Path | None = getattr(request.app.state, "projects_root", None)
    if projects_root is None:
        logger.debug("projects_root not configured — returning empty project list")
        return []

    from orchestrator.mobile_api.dynamic_directory_service import make_opaque_id

    salt = request.app.state.directory_salt
    workspace_root: Path = request.app.state.reader.workspace

    # Build run count index in a single pass over state files
    run_count_index = _build_run_count_index(workspace_root)

    entries: list[ProjectEntry] = []
    try:
        for entry in projects_root.iterdir():
            try:
                if not entry.is_dir():
                    continue
                if entry.is_symlink():
                    logger.debug("Skipping symlink project entry: %s", entry)
                    continue

                resolved = entry.resolve()

                # Verify the resolved path is still inside projects_root
                try:
                    resolved.relative_to(projects_root.resolve())
                except ValueError:
                    logger.warning(
                        "Skipping project %s — resolved path %s is outside projects_root",
                        entry.name,
                        resolved,
                    )
                    continue

                opaque_id = make_opaque_id(str(resolved), salt)

                # Last-modified: directory mtime as ISO-8601 UTC
                try:
                    mtime = entry.stat().st_mtime
                    last_modified = datetime.fromtimestamp(
                        mtime, tz=timezone.utc
                    ).isoformat()
                except OSError:
                    last_modified = datetime.now(timezone.utc).isoformat()

                run_count = run_count_index.get(opaque_id, 0)

                entries.append(ProjectEntry(
                    id=opaque_id,
                    name=entry.name,
                    last_modified=last_modified,
                    run_count=run_count,
                ))

            except OSError as exc:
                logger.debug("OSError processing project entry %s: %s", entry, exc)
                continue

    except OSError as exc:
        logger.warning("OSError enumerating projects_root %s: %s", projects_root, exc)

    # Sort by last_modified descending (most recently modified first)
    entries.sort(key=lambda p: p.last_modified, reverse=True)

    return [e.model_dump() for e in entries]
