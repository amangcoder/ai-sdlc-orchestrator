"""Directory list service for the Mobile API.

SECURITY-CRITICAL: This service guards against path disclosure, directory
traversal, and TOCTOU symlink attacks.

Public API:
  build_directory_entries(config, workspace_dir, salt)
      → tuple[list[dict], dict[str, Path]]
      Builds the opaque-ID→path frozen map once at startup.

  resolve_workspace_id(workspace_id, frozen_map)
      → Path | None
      Validates a client-supplied workspace_id against the pre-built map.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orchestrator.models import OrchestratorConfig

logger = logging.getLogger(__name__)


def _make_opaque_id(abs_path: str, salt: uuid.UUID) -> str:
    """Generate a stable opaque ID from an absolute path and a per-installation salt.

    Uses UUID v5 so the same path + salt always yields the same ID (stable
    across server restarts), while the caller-supplied salt prevents an
    attacker from pre-computing IDs by guessing filesystem paths.

    Args:
        abs_path: Absolute filesystem path string (after Path.resolve()).
        salt: Per-installation random UUID (NOT uuid.NAMESPACE_DNS).

    Returns:
        32-character hex string (UUID v5 digest).
    """
    return uuid.uuid5(salt, abs_path).hex


def _detect_tech_stack(path: Path) -> str | None:
    """Detect the primary technology stack for a directory.

    Checks for indicator files in order of priority:
      pyproject.toml → 'Python'
      pubspec.yaml   → 'Flutter'
      package.json   → 'Node'

    Returns:
        Tech stack string, or None if no indicator found.
    """
    try:
        if (path / "pyproject.toml").exists():
            return "Python"
        if (path / "pubspec.yaml").exists():
            return "Flutter"
        if (path / "package.json").exists():
            return "Node"
    except OSError:
        pass
    return None


def build_directory_entries(
    config: "OrchestratorConfig",
    workspace_dir: Path,
    salt: uuid.UUID,
) -> tuple[list[dict], dict[str, Path]]:
    """Build directory entry dicts and the frozen opaque-ID→path map.

    Called ONCE at server startup. The frozen_map is stored on app.state and
    used by resolve_workspace_id() on every POST /api/v1/runs request.

    Security properties:
    - Symlinked directories are skipped (logged as warning).
    - Paths are resolved via Path.resolve() so relative paths and redundant
      separators are normalised.
    - The frozen map is immutable after startup — path traversal attempts
      via workspace_id manipulation cannot escape the allow-list.
    - No raw filesystem paths appear in the returned entry dicts.

    Args:
        config: OrchestratorConfig instance (reads allowed_directories).
        workspace_dir: Resolved workspace Path (used as fallback when
            allowed_directories is empty).
        salt: Per-installation UUID used to generate opaque IDs.

    Returns:
        A 2-tuple of:
          - list of DirectoryEntry-compatible dicts (id, name, tech_stack,
            last_used) — NO 'path' key.
          - dict[opaque_id, resolved_path] frozen map for O(1) lookups.
    """
    frozen_map: dict[str, Path] = {}
    entries: list[dict] = []

    dirs_to_process = config.allowed_directories if config.allowed_directories else []

    if not dirs_to_process:
        # Fall back to workspace_dir as the single allowed directory.
        try:
            if workspace_dir.is_symlink():
                logger.warning(
                    "workspace_dir is a symlink — skipping: %s", workspace_dir
                )
            else:
                resolved = workspace_dir.resolve()
                opaque_id = _make_opaque_id(str(resolved), salt)
                frozen_map[opaque_id] = resolved
                entries.append(
                    {
                        "id": opaque_id,
                        "name": resolved.name,
                        "tech_stack": _detect_tech_stack(resolved),
                        "last_used": None,
                    }
                )
        except OSError as exc:
            logger.warning("Failed to process workspace_dir %s: %s", workspace_dir, exc)
    else:
        for entry in dirs_to_process:
            try:
                p = Path(entry.path)
                if p.is_symlink():
                    logger.warning(
                        "Skipping symlink in allowed_directories: %s", entry.path
                    )
                    continue
                resolved = p.resolve()
                if not resolved.is_dir():
                    logger.warning(
                        "allowed_directories entry is not a directory — skipping: %s",
                        entry.path,
                    )
                    continue
                opaque_id = _make_opaque_id(str(resolved), salt)
                display_name = entry.name if entry.name else resolved.name
                frozen_map[opaque_id] = resolved
                entries.append(
                    {
                        "id": opaque_id,
                        "name": display_name,
                        "tech_stack": _detect_tech_stack(resolved),
                        "last_used": None,
                    }
                )
            except OSError as exc:
                logger.warning(
                    "Failed to process allowed_directories entry %s: %s",
                    entry.path,
                    exc,
                )

    return entries, frozen_map


def resolve_workspace_id(
    workspace_id: str,
    frozen_map: dict[str, Path],
) -> Path | None:
    """Resolve an opaque workspace_id to a validated filesystem Path.

    Performs a TOCTOU (time-of-check/time-of-use) safety check: after
    looking up the cached resolved path, re-resolves it and confirms the
    result matches the cached value. If a symlink was created between
    startup and this call, the re-resolved path will differ and the lookup
    returns None.

    Args:
        workspace_id: Opaque ID string supplied by the mobile client.
        frozen_map: Pre-built dict[opaque_id, resolved_path] from startup.

    Returns:
        The validated Path, or None if:
          - workspace_id is not in frozen_map (unknown ID),
          - the re-resolved path differs from the cached value (TOCTOU),
          - the path is no longer a directory.
    """
    cached_path = frozen_map.get(workspace_id)
    if cached_path is None:
        return None

    # TOCTOU check: re-resolve and confirm path hasn't changed
    try:
        current = cached_path.resolve()
        if current != cached_path:
            logger.warning(
                "TOCTOU detected for workspace_id %s: cached=%s, current=%s",
                workspace_id,
                cached_path,
                current,
            )
            return None
        if not current.is_dir():
            logger.warning(
                "Path is no longer a directory for workspace_id %s: %s",
                workspace_id,
                current,
            )
            return None
        return cached_path
    except OSError as exc:
        logger.warning(
            "OSError resolving workspace_id %s: %s", workspace_id, exc
        )
        return None
