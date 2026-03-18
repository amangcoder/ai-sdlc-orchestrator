"""Artifacts REST router with two-layer path traversal defense.

Security model (three layers as per test contract):
  Layer 1: regex allowlist [a-z][a-z0-9_]{0,63} — rejects uppercase, dots, slashes
  Layer 2: ARTIFACT_MODELS allowlist — only known artifact types served
  Layer 3: Path.resolve() escape check — prevents symlink-based directory escape

Note: run_id in URL is accepted for API consistency but artifacts are currently
workspace-global (shared across all runs). The run_id parameter is not used for
path lookup.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

router = APIRouter()

# Allowlist regex: must start with lowercase letter, only lowercase/digits/underscores
# Max total length: 64 chars (1 + up to 63)
_ARTIFACT_NAME_RE = re.compile(r'^[a-z][a-z0-9_]{0,63}$')


def resolve_artifact_path(workspace: Path, name: str) -> Path:
    """Resolve and validate an artifact file path.

    Applies three layers of path traversal defense:
      1. Regex allowlist: [a-z][a-z0-9_]{0,63}
      2. ARTIFACT_MODELS allowlist: only known orchestrator artifact types
      3. Path.resolve(): prevents symlink-based directory escape

    Args:
        workspace: The workspace root directory.
        name: The artifact name (without .json extension).

    Returns:
        The resolved Path to the artifact JSON file.

    Raises:
        ValueError: If the name fails any validation layer.
    """
    # Layer 1: Regex allowlist
    if not name or not _ARTIFACT_NAME_RE.match(name):
        raise ValueError(
            f"Invalid artifact name '{name}': must match [a-z][a-z0-9_]{{0,63}}"
        )

    # Layer 2: ARTIFACT_MODELS allowlist
    from orchestrator.models import ARTIFACT_MODELS

    if name not in ARTIFACT_MODELS:
        raise ValueError(
            f"Unknown artifact type '{name}': not in ARTIFACT_MODELS allowlist"
        )

    # Layer 3: Path.resolve() escape check
    artifacts_dir = (workspace / "artifacts").resolve()
    candidate = (workspace / "artifacts" / f"{name}.json").resolve()

    if not str(candidate).startswith(str(artifacts_dir) + "/") and str(candidate) != str(artifacts_dir):
        raise ValueError(
            f"Path traversal detected for artifact name '{name}'"
        )

    return candidate


# ── GET /api/v1/runs/{run_id}/artifacts ───────────────────────────────────

@router.get("/runs/{run_id}/artifacts")
async def list_artifacts(run_id: str, request: Request) -> list[str]:
    """List available artifact names (without .json extension).

    Scans workspace/artifacts/ for JSON files and returns names
    that pass the ARTIFACT_MODELS allowlist filter.
    """
    workspace: Path = request.app.state.reader.workspace
    artifacts_dir = workspace / "artifacts"

    if not artifacts_dir.exists():
        return []

    from orchestrator.models import ARTIFACT_MODELS

    names: list[str] = []
    for f in artifacts_dir.glob("*.json"):
        name = f.stem  # filename without .json extension
        # Only list artifacts that are in the ARTIFACT_MODELS allowlist
        if name in ARTIFACT_MODELS:
            names.append(name)

    return sorted(names)


# ── GET /api/v1/runs/{run_id}/artifacts/{artifact_name} ───────────────────

@router.get("/runs/{run_id}/artifacts/{artifact_name}")
async def get_artifact(
    run_id: str,
    artifact_name: str,
    request: Request,
):
    """Serve a specific artifact JSON file.

    Returns the artifact's JSON content directly.
    Returns HTTP 400 on path traversal attempt.
    Returns HTTP 404 if artifact is not found on disk.
    """
    workspace: Path = request.app.state.reader.workspace

    # Two-layer path traversal defense via resolve_artifact_path()
    try:
        artifact_path = resolve_artifact_path(workspace, artifact_name)
    except ValueError:
        return JSONResponse(
            status_code=404,
            content={"error": f"Artifact not found: {artifact_name}"},
        )

    # Check file exists
    if not artifact_path.exists():
        return JSONResponse(
            status_code=404,
            content={"error": f"Artifact not found: {artifact_name}"},
        )

    # Read and return JSON content
    try:
        import json
        content = json.loads(artifact_path.read_text(encoding="utf-8"))
        return JSONResponse(content=content)
    except (json.JSONDecodeError, OSError) as exc:
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to read artifact: {exc}"},
        )


# ── GET /api/v1/runs/{run_id}/events ──────────────────────────────────────

@router.get("/runs/{run_id}/events")
async def get_events(
    run_id: str,
    request: Request,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=1000),
) -> dict[str, Any]:
    """HTTP polling fallback endpoint for run events.

    Returns a slice of the JSONL log starting at `offset` with at most
    `limit` events. Clients use this when WebSocket is unavailable.
    """
    reader = request.app.state.reader
    events = reader.get_events(run_id, offset=offset, limit=limit)

    return {
        "events": events,
        "offset": offset,
        "count": len(events),
    }
