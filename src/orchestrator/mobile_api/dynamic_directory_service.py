"""Dynamic directory resolution service for the Mobile API.

SECURITY-CRITICAL: This module guards against path disclosure, directory
traversal, and TOCTOU symlink attacks for request-time directory resolution.

This is a SIBLING to the startup frozen_map in directory_service.py.
Do NOT modify directory_service.py — this module provides separate
request-time resolution for the dynamic directory tree.

REUSES:
  _detect_tech_stack from directory_service — detects Python/Flutter/Node
  _make_opaque_id equivalent — same UUID v5 logic for stable opaque IDs

Public API:
  make_opaque_id(abs_path, salt) -> str
      Identical logic to _make_opaque_id in directory_service.py.

  resolve_dynamic_id(opaque_id, projects_root, salt) -> Path | None
      Scans projects_root, finds matching opaque ID, TOCTOU-validates.

  get_root_entry(projects_root, salt) -> dict
      Returns a DirectoryEntry-compatible dict for projects_root itself.

  list_children(parent_path, projects_root, salt, current_depth, max_depth)
      -> list[dict]
      Lists immediate subdirectories, skipping symlinks.

  create_child(parent_path, name, projects_root, salt) -> dict
      Creates a new subdirectory with safety checks.

  compute_depth(path, projects_root) -> int
      Counts path segments below projects_root (0 = projects_root itself).
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
import uuid
from collections import OrderedDict
from pathlib import Path

from orchestrator.mobile_api.directory_service import _detect_tech_stack

logger = logging.getLogger(__name__)

# Regex for valid directory names — must match CreateDirectoryRequest.name pattern
_VALID_NAME_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_\-. ]{0,127}$')

# ── LRU Cache for resolve_dynamic_id ──────────────────────────────────────
# Process-local cache mapping opaque_id → resolved Path.
# Uses OrderedDict for O(1) get/put/delete with LRU eviction.

_CACHE_MAX_SIZE = 512
_NEGATIVE_CACHE_MAX_SIZE = 1024
_NEGATIVE_CACHE_TTL_SECONDS = 60.0

# Directories to skip during rglob fallback scan
_SKIP_DIRS = frozenset({'.git', 'node_modules', '__pycache__', '.venv'})

_cache: OrderedDict[str, Path] = OrderedDict()
_negative_cache: dict[str, float] = {}  # opaque_id → expiry timestamp
_cache_lock = threading.Lock()


def _clear_cache() -> None:
    """Clear both positive and negative caches.

    Called once during lifespan() startup to prevent stale entries
    from a previous server process.
    """
    with _cache_lock:
        _cache.clear()
        _negative_cache.clear()


def _invalidate_cache_entry(opaque_id: str) -> None:
    """Remove a specific entry from the cache.

    Called by create_child() and any delete-directory operation so that
    the next resolve_dynamic_id() call for this opaque_id rescans.
    """
    with _cache_lock:
        _cache.pop(opaque_id, None)
        _negative_cache.pop(opaque_id, None)


def make_opaque_id(abs_path: str, salt: uuid.UUID) -> str:
    """Generate a stable opaque ID from an absolute path and a per-installation salt.

    Uses UUID v5 so the same path + salt always yields the same ID (stable
    across server restarts), while the caller-supplied salt prevents an
    attacker from pre-computing IDs by guessing filesystem paths.

    Produces identical output to _make_opaque_id in directory_service.py
    for the same inputs.

    Args:
        abs_path: Absolute filesystem path string (after Path.resolve()).
        salt: Per-installation random UUID (NOT uuid.NAMESPACE_DNS).

    Returns:
        32-character hex string (UUID v5 digest).
    """
    return uuid.uuid5(salt, abs_path).hex


def compute_depth(path: Path, projects_root: Path) -> int:
    """Count path segments below projects_root.

    Args:
        path: The path to compute depth for.
        projects_root: The root of the dynamic directory tree.

    Returns:
        0 if path == projects_root, positive integer for subdirectories.
        Returns 0 if path is not under projects_root (safe fallback).
    """
    try:
        rel = path.relative_to(projects_root)
        return len(rel.parts)
    except ValueError:
        # path is not under projects_root — safe fallback
        return 0


def _should_skip_dir(entry: Path) -> bool:
    """Check if a directory should be skipped during rglob scan."""
    return entry.name in _SKIP_DIRS


def _rglob_scan(
    opaque_id: str,
    projects_root: Path,
    salt: uuid.UUID,
    root_resolved: Path,
) -> Path | None:
    """Fallback rglob scan for cache miss. Returns resolved Path or None."""
    try:
        for entry in projects_root.rglob("*"):
            try:
                if not entry.is_dir():
                    continue
                if entry.is_symlink():
                    continue
                if _should_skip_dir(entry):
                    continue
                candidate_id = make_opaque_id(str(entry.resolve()), salt)
                if candidate_id != opaque_id:
                    continue

                # Match found — TOCTOU re-resolve and verify containment
                resolved = entry.resolve()

                try:
                    resolved.relative_to(root_resolved)
                except ValueError:
                    logger.warning(
                        "TOCTOU: resolved path %s is outside projects_root %s for id %s",
                        resolved,
                        root_resolved,
                        opaque_id,
                    )
                    return None

                if entry.is_symlink():
                    logger.warning(
                        "Path became a symlink during TOCTOU check: %s", entry
                    )
                    return None

                if not resolved.is_dir():
                    logger.warning(
                        "Path is no longer a directory for id %s: %s", opaque_id, resolved
                    )
                    return None

                return resolved

            except OSError as exc:
                logger.debug("OSError scanning %s: %s", entry, exc)
                continue

    except OSError as exc:
        logger.warning(
            "OSError scanning projects_root %s for id %s: %s",
            projects_root,
            opaque_id,
            exc,
        )
    return None


def resolve_dynamic_id(
    opaque_id: str,
    projects_root: Path,
    salt: uuid.UUID,
) -> Path | None:
    """Resolve an opaque directory ID to a validated filesystem Path.

    Uses a process-local LRU cache (max 512 entries) for O(1) resolution
    on repeated lookups. Falls back to rglob scan on cache miss and populates
    the cache on success. A bounded negative-result cache (max 1024 entries,
    60-second TTL) prevents rglob floods from invalid opaque_id requests.

    NOTE: This function performs blocking I/O on cache miss. Async callers
    (route handlers) should wrap calls in asyncio.to_thread() to avoid
    blocking the event loop.

    SECURITY:
    - After finding a candidate, re-resolves via Path.resolve() and verifies
      the result is still inside projects_root (symlink escape prevention).
    - Returns None on any violation: unknown ID, path outside root, symlink,
      or filesystem error.

    Args:
        opaque_id: Opaque ID string supplied by the mobile client.
        projects_root: Absolute path to the projects root directory.
        salt: Per-installation UUID used for ID generation.

    Returns:
        The validated Path, or None if the ID cannot be safely resolved.
    """
    # First, check if the ID matches projects_root itself
    root_resolved = projects_root.resolve()
    root_id = make_opaque_id(str(root_resolved), salt)
    if opaque_id == root_id:
        return root_resolved

    # Check positive cache (LRU)
    with _cache_lock:
        if opaque_id in _cache:
            # Move to end (most recently used) and return
            _cache.move_to_end(opaque_id)
            cached_path = _cache[opaque_id]
            # Quick validation: path still exists and is a directory
            if cached_path.is_dir() and not cached_path.is_symlink():
                return cached_path
            else:
                # Stale entry — remove it
                del _cache[opaque_id]

        # Check negative cache
        neg_expiry = _negative_cache.get(opaque_id)
        if neg_expiry is not None:
            if time.monotonic() < neg_expiry:
                return None
            else:
                # Expired — remove stale entry
                del _negative_cache[opaque_id]

    # Cache miss — fall back to rglob scan
    result = _rglob_scan(opaque_id, projects_root, salt, root_resolved)

    with _cache_lock:
        if result is not None:
            # Populate positive cache
            _cache[opaque_id] = result
            _cache.move_to_end(opaque_id)
            # Evict LRU entry if over capacity
            if len(_cache) > _CACHE_MAX_SIZE:
                _cache.popitem(last=False)
            # Remove from negative cache if present
            _negative_cache.pop(opaque_id, None)
        else:
            # Populate negative cache
            _negative_cache[opaque_id] = time.monotonic() + _NEGATIVE_CACHE_TTL_SECONDS
            # Evict oldest negative entries if over capacity
            if len(_negative_cache) > _NEGATIVE_CACHE_MAX_SIZE:
                # Remove oldest entries (by earliest expiry)
                sorted_keys = sorted(_negative_cache, key=_negative_cache.get)  # type: ignore[arg-type]
                for key in sorted_keys[:len(_negative_cache) - _NEGATIVE_CACHE_MAX_SIZE]:
                    del _negative_cache[key]

    return result


def get_root_entry(projects_root: Path, salt: uuid.UUID) -> dict:
    """Return a DirectoryEntry-compatible dict for the projects_root directory.

    Args:
        projects_root: Absolute path to the projects root.
        salt: Per-installation UUID for opaque ID generation.

    Returns:
        Dict with keys: id, name, tech_stack.
        NO 'path' key (security: no raw path disclosure).
    """
    resolved = projects_root.resolve()
    opaque_id = make_opaque_id(str(resolved), salt)
    return {
        "id": opaque_id,
        "name": resolved.name,
        "tech_stack": _detect_tech_stack(resolved),
    }


def list_children(
    parent_path: Path,
    projects_root: Path,
    salt: uuid.UUID,
    current_depth: int,
    max_depth: int,
) -> list[dict]:
    """List immediate subdirectories of parent_path as DirectoryEntry dicts.

    Returns an empty list (not an error) when current_depth >= max_depth.
    Symlinks are skipped (consistent with frozen_map startup behavior).
    Response dicts never contain a 'path' key.

    Args:
        parent_path: Resolved absolute path to list children of.
        projects_root: Absolute path to the projects root (for ID generation).
        salt: Per-installation UUID for opaque ID generation.
        current_depth: Depth of parent_path relative to projects_root.
        max_depth: Maximum allowed depth from config.max_browse_depth.

    Returns:
        List of DirectoryEntry-compatible dicts (id, name, tech_stack).
        Empty list if at max depth or no subdirectories exist.
    """
    if current_depth >= max_depth:
        return []

    entries: list[dict] = []
    try:
        for entry in sorted(parent_path.iterdir(), key=lambda p: p.name.lower()):
            try:
                if not entry.is_dir():
                    continue
                if entry.is_symlink():
                    logger.debug("Skipping symlink: %s", entry)
                    continue
                resolved = entry.resolve()
                opaque_id = make_opaque_id(str(resolved), salt)
                entries.append({
                    "id": opaque_id,
                    "name": entry.name,
                    "tech_stack": _detect_tech_stack(resolved),
                })
            except OSError as exc:
                logger.debug("OSError listing entry %s: %s", entry, exc)
                continue
    except OSError as exc:
        logger.warning("OSError listing children of %s: %s", parent_path, exc)

    return entries


def create_child(
    parent_path: Path,
    name: str,
    projects_root: Path,
    salt: uuid.UUID,
) -> dict:
    """Create a new subdirectory inside parent_path and return its entry dict.

    SECURITY:
    - Validates name against the allowed regex (no path separators, etc.).
    - Re-resolves parent_path to verify it's still inside projects_root.
    - Uses os.makedirs with exist_ok=False to detect duplicates.

    Args:
        parent_path: Resolved absolute path of the parent directory.
        name: Name for the new directory (validated against regex).
        projects_root: Absolute path to projects root for containment check.
        salt: Per-installation UUID for opaque ID generation.

    Returns:
        DirectoryEntry-compatible dict for the new directory.

    Raises:
        ValueError: If name fails the regex or contains path separators.
        PermissionError: If parent_path resolves outside projects_root.
        FileExistsError: If the directory already exists.
        OSError: On filesystem errors during mkdir.
    """
    # Validate name: no path separators or null bytes
    if '/' in name or '\\' in name or '\x00' in name:
        raise ValueError(
            f"Directory name must not contain path separators or null bytes: {name!r}"
        )
    if not _VALID_NAME_RE.match(name):
        raise ValueError(
            f"Invalid directory name {name!r}. "
            "Must start with an alphanumeric character and contain only "
            "letters, digits, underscores, hyphens, dots, or spaces (max 128 chars)."
        )

    # TOCTOU: re-resolve parent to verify it's still inside projects_root
    root_resolved = projects_root.resolve()
    try:
        parent_resolved = parent_path.resolve()
    except OSError as exc:
        raise PermissionError(
            f"Cannot resolve parent path {parent_path}: {exc}"
        ) from exc

    try:
        parent_resolved.relative_to(root_resolved)
    except ValueError:
        raise PermissionError(
            f"Parent path {parent_path} resolves to {parent_resolved} which is "
            f"outside projects_root {root_resolved}"
        )

    new_dir = parent_resolved / name

    # Create the directory — raises FileExistsError if it already exists
    os.makedirs(new_dir, mode=0o755, exist_ok=False)

    resolved_new = new_dir.resolve()
    opaque_id = make_opaque_id(str(resolved_new), salt)

    # Invalidate parent's cache entry so subsequent resolves rescan
    parent_opaque_id = make_opaque_id(str(parent_resolved), salt)
    _invalidate_cache_entry(parent_opaque_id)

    return {
        "id": opaque_id,
        "name": name,
        "tech_stack": None,  # Newly created directory has no tech stack yet
    }
