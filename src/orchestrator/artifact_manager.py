"""Versioned artifact storage with backward-compatible write path.

ArtifactManager provides:
- Atomic writes to artifacts/{name}.json (current version)
- Versioned copies at artifacts/.versions/{name}/v{N}.json
- A searchable .index.json manifest for fast listing / metadata
- fcntl.flock concurrency protection on .index.json
- Structural diff between artifact versions from different runs
- Retention policy enforcement (age + run count + keep_failed)

Backward compatible:
  ArtifactCache.load_artifact() still reads artifacts/{name}.json unchanged.

Security:
  - Artifact names are validated against [a-zA-Z0-9][a-zA-Z0-9_-]*
  - All resolved paths are verified to reside within artifacts_dir
  - Version IDs must be positive integers
"""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import logging
import re
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Generator

from pydantic import BaseModel

from orchestrator.models import ArtifactsConfig
from orchestrator.persistence import atomic_write

if TYPE_CHECKING:
    from orchestrator.db.repositories.artifacts import ArtifactRepository

log = logging.getLogger(__name__)

# fcntl is Unix-only — guard import for cross-platform compatibility.
# Follows the project's HAS_*/try-except ImportError pattern (see monitoring/).
try:
    import fcntl as _fcntl
    HAS_FCNTL = True
except ImportError:  # pragma: no cover — Windows path
    import threading as _threading
    _lock_fallback = _threading.Lock()
    HAS_FCNTL = False
    log.warning(
        "fcntl unavailable on this platform — using threading.Lock for "
        ".index.json protection (process-local only)"
    )

# Artifact name validation: starts with alphanumeric, followed by alphanumeric/hyphens/underscores
_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


class ArtifactMetadata(BaseModel):
    """Metadata for an artifact tracked in .index.json."""

    name: str
    current_version: int
    run_id: str
    agent: str | None = None
    schema_name: str | None = None
    created_at: str
    updated_at: str
    size_bytes: int = 0
    valid: bool = True  # True when the artifact passed schema validation


class ArtifactVersion(BaseModel):
    """A single versioned entry in the artifact history."""

    version: int
    run_id: str
    agent: str | None = None
    created_at: str
    size_bytes: int = 0
    run_status: str = "unknown"
    checksum: str = ""  # SHA-256 hex digest of the serialised artifact payload


class ArtifactDiff(BaseModel):
    """Structural diff between two artifact versions (top-level key comparison)."""

    name: str
    run_a: str
    run_b: str
    version_a: int
    version_b: int
    added: list[str] = []      # keys present in run_b version but not run_a
    removed: list[str] = []    # keys present in run_a version but not run_b
    changed: list[str] = []    # keys present in both but with different values


class RetentionResult(BaseModel):
    """Summary of what the retention policy did (or would do in dry_run mode)."""

    deleted_versions: int = 0
    deleted_paths: list[str] = []
    retained_versions: int = 0
    dry_run: bool = False


# ---------------------------------------------------------------------------
# ArtifactManager
# ---------------------------------------------------------------------------


class ArtifactManager:
    """Versioned artifact storage manager.

    Manages a single artifacts directory:
      artifacts/{name}.json              — current (latest) version
      artifacts/.versions/{name}/v{N}.json — numbered versioned copies
      artifacts/.index.json             — manifest with run/agent/version metadata

    Thread-safety:
      Uses fcntl.flock(LOCK_EX) around all .index.json reads + writes.
      Current-version files are written via atomic_write (temp + rename).

    Path traversal defence:
      All file paths are constructed via _safe_path() which calls Path.resolve()
      and asserts the result is relative to artifacts_dir.

    Backward compatibility:
      ArtifactCache reads artifacts/{name}.json — this class writes the same file.
    """

    def __init__(
        self,
        artifacts_dir: Path,
        config: ArtifactsConfig | None = None,
        db_repo: "ArtifactRepository | None" = None,
    ) -> None:
        self.artifacts_dir: Path = artifacts_dir.resolve()
        self.config: ArtifactsConfig = config or ArtifactsConfig()
        self._index_path: Path = self.artifacts_dir / ".index.json"
        # When set, all save/load/search operations also hit the DB.
        self._db_repo: ArtifactRepository | None = db_repo

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _validate_name(self, name: str) -> None:
        """Raise ValueError if *name* contains invalid characters or path separators."""
        if not _NAME_RE.match(name):
            raise ValueError(
                f"Invalid artifact name {name!r}: must match ^[a-zA-Z0-9][a-zA-Z0-9_-]*$"
            )

    def _safe_path(self, *parts: str) -> Path:
        """Build a path from *parts* relative to artifacts_dir, rejecting traversal.

        Resolves the candidate path and verifies it is relative_to(artifacts_dir).
        Raises ValueError if the resolved path escapes artifacts_dir.
        """
        candidate = Path(self.artifacts_dir, *parts).resolve()
        try:
            candidate.relative_to(self.artifacts_dir)
        except ValueError:
            raise ValueError(
                f"Path traversal detected: {'/'.join(str(p) for p in parts)!r} "
                f"resolves outside artifacts_dir"
            )
        return candidate

    @contextmanager
    def _locked_index(self) -> Generator[dict[str, Any], None, None]:
        """Acquire an exclusive lock on a stable lock file, yield the parsed index dict.

        Design rationale
        ----------------
        We lock a *separate* stable ``.index.lock`` file rather than the index
        file itself.  ``atomic_write`` replaces ``.index.json`` via
        ``os.replace``, which changes the underlying inode.  If we locked the
        index file directly, threads that opened the old inode would hold a lock
        on a stale inode — allowing two threads to simultaneously believe they
        hold the exclusive lock on different inodes.

        The ``.index.lock`` file is opened in append mode and never atomically
        replaced, so its inode is stable for the lifetime of the process.

        After acquiring the lock we re-read ``.index.json`` from the filesystem
        path to pick up changes written by the previous lock-holder.  On exit we
        write the mutated index back via ``atomic_write`` before releasing the lock.
        """
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        lock_path = self.artifacts_dir / ".index.lock"

        # Open (or create) the lock file in append mode — inode stays stable.
        lock_fd = open(lock_path, "a", encoding="utf-8")
        try:
            # Acquire exclusive lock — platform-aware (fcntl on Unix, threading.Lock on Windows).
            if HAS_FCNTL:
                _fcntl.flock(lock_fd.fileno(), _fcntl.LOCK_EX)
            else:  # pragma: no cover — Windows fallback
                _lock_fallback.acquire()

            # Re-read the index AFTER acquiring the lock so we always see the
            # latest version written by the previous lock-holder.
            if self._index_path.exists():
                try:
                    index = json.loads(self._index_path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    log.warning(
                        "Corrupt .index.json at %s — resetting to empty", self._index_path
                    )
                    index = {"artifacts": {}}
            else:
                index = {"artifacts": {}}

            yield index

            # Write back atomically while still holding the lock.
            atomic_write(self._index_path, json.dumps(index, indent=2, default=str))
        finally:
            if HAS_FCNTL:
                try:
                    _fcntl.flock(lock_fd.fileno(), _fcntl.LOCK_UN)
                except OSError:
                    pass
            else:  # pragma: no cover — Windows fallback
                _lock_fallback.release()
            lock_fd.close()

    def _read_index(self) -> dict[str, Any]:
        """Read .index.json without locking (best-effort, read-only callers)."""
        if not self._index_path.exists():
            return {"artifacts": {}}
        try:
            return json.loads(self._index_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {"artifacts": {}}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def save_artifact(
        self,
        run_id: str,
        name: str,
        data: dict[str, Any],
        agent: str | None = None,
        schema_name: str | None = None,
    ) -> ArtifactMetadata:
        """Save an artifact, writing current + versioned copy + index entry.

        Writes:
          1. artifacts/{name}.json          — current version (atomic overwrite)
          2. artifacts/.versions/{name}/v{N}.json — versioned copy (if enabled)
          3. artifacts/.index.json          — updated manifest

        Args:
            run_id: Identifier of the run that produced this artifact.
            name:   Artifact name (alphanumeric + hyphens + underscores).
            data:   JSON-serialisable dict to persist.
            agent:  Optional agent role that produced the artifact.
            schema_name: Optional schema name for type-based search.

        Returns:
            ArtifactMetadata for the saved artifact.

        Raises:
            ValueError: on invalid name, path traversal, or non-serialisable data.
        """
        self._validate_name(name)

        # Validate paths upfront (before any I/O)
        current_path = self._safe_path(f"{name}.json")

        payload = json.dumps(data, indent=2, default=str)
        payload_bytes = payload.encode("utf-8")
        size_bytes = len(payload_bytes)
        checksum = hashlib.sha256(payload_bytes).hexdigest()
        now = datetime.now(timezone.utc).isoformat()

        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

        # Determine the version number atomically inside the index lock so
        # concurrent threads cannot race to claim the same version number.
        version_num = 1
        if self.config.index_enabled:
            with self._locked_index() as index:
                artifacts = index.setdefault("artifacts", {})
                existing_entry = artifacts.get(name, {})

                created_at = existing_entry.get("created_at", now)
                versions_meta: dict[str, Any] = existing_entry.get("versions", {})

                # Next version = current_version + 1 (from index, NOT from dir scan)
                version_num = existing_entry.get("current_version", 0) + 1

                # 1. Write current version while holding the lock so readers see
                #    an atomic transition to the new content.
                atomic_write(current_path, payload)

                # 2. Write versioned copy
                if self.config.versioning_enabled:
                    versions_dir = self._safe_path(".versions", name)
                    versions_dir.mkdir(parents=True, exist_ok=True)
                    versioned_path = self._safe_path(
                        ".versions", name, f"v{version_num}.json"
                    )
                    atomic_write(versioned_path, payload)

                # 3. Record in index (still inside the lock)
                versions_meta[str(version_num)] = {
                    "run_id": run_id,
                    "agent": agent,
                    "created_at": now,
                    "size_bytes": size_bytes,
                    "run_status": "running",
                    "checksum": checksum,
                }

                artifacts[name] = {
                    "name": name,
                    "current_version": version_num,
                    "run_id": run_id,
                    "agent": agent,
                    "schema_name": schema_name,
                    "created_at": created_at,
                    "updated_at": now,
                    "size_bytes": size_bytes,
                    "versions": versions_meta,
                }
        else:
            # Index disabled — write files without index tracking.
            # Version number is derived from the .versions/ directory (less
            # accurate under high concurrency, but acceptable when index is off).
            atomic_write(current_path, payload)

            if self.config.versioning_enabled:
                versions_dir = self._safe_path(".versions", name)
                versions_dir.mkdir(parents=True, exist_ok=True)
                existing_nums = sorted(
                    int(p.stem[1:])
                    for p in versions_dir.glob("v*.json")
                    if p.stem[1:].isdigit()
                )
                version_num = (existing_nums[-1] + 1) if existing_nums else 1
                versioned_path = self._safe_path(
                    ".versions", name, f"v{version_num}.json"
                )
                atomic_write(versioned_path, payload)

        metadata = ArtifactMetadata(
            name=name,
            current_version=version_num,
            run_id=run_id,
            agent=agent,
            schema_name=schema_name,
            created_at=now,
            updated_at=now,
            size_bytes=size_bytes,
        )

        # DB dual-write (best-effort — never block the pipeline on DB errors)
        if self._db_repo is not None:
            try:
                loop = asyncio.get_event_loop()
                coro = self._db_repo.save(
                    run_id=run_id,
                    name=name,
                    data=data,
                    agent=agent,
                    schema_name=schema_name,
                )
                if loop.is_running():
                    asyncio.ensure_future(coro)
                else:
                    loop.run_until_complete(coro)
            except Exception as exc:
                log.warning("DB artifact save failed for %s/%s: %s", run_id, name, exc)

        return metadata

    def load_artifact(
        self,
        run_id: str,
        name: str,
        version: int | None = None,
    ) -> dict[str, Any] | None:
        """Load an artifact, returning the current version or a specific one.

        When a ``db_repo`` is configured, tries the DB first and falls back
        to the filesystem (handles the window where agent has written the file
        but the DB hasn't been updated yet).

        Args:
            run_id:  Run identifier (informational when version=None).
            name:    Artifact name.
            version: Specific version number (positive integer), or None for current.

        Returns:
            Parsed JSON dict, or None if not found in DB or filesystem.

        Raises:
            ValueError: on invalid name, non-integer version, or path traversal.
        """
        self._validate_name(name)

        if version is not None and (not isinstance(version, int) or version < 1):
            raise ValueError(f"version must be a positive integer, got {version!r}")

        # Try DB first
        if self._db_repo is not None:
            try:
                loop = asyncio.get_event_loop()
                coro = self._db_repo.load(run_id=run_id, name=name, version=version)
                result = (
                    asyncio.ensure_future(coro)
                    if loop.is_running()
                    else loop.run_until_complete(coro)
                )
                # When the loop is running, ensure_future returns a Task not a result.
                # We can't await here, so fall through to the filesystem for in-flight reads.
                if not loop.is_running() and result is not None:
                    return result
            except Exception as exc:
                log.warning("DB artifact load failed for %s/%s: %s", run_id, name, exc)

        # Filesystem fallback (always used during agent execution window)
        if version is not None:
            path = self._safe_path(".versions", name, f"v{version}.json")
        else:
            path = self._safe_path(f"{name}.json")

        if not path.exists():
            return None

        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Failed to load artifact %r (version=%s): %s", name, version, exc)
            return None

    def list_artifacts(self, run_id: str) -> list[ArtifactMetadata]:
        """Return metadata list for all artifacts produced by *run_id*.

        Reads .index.json and returns entries whose latest version was saved
        by the given run_id.  Returns an empty list if the index does not exist.
        """
        index = self._read_index()
        results: list[ArtifactMetadata] = []

        for artifact_name, entry in index.get("artifacts", {}).items():
            if run_id and entry.get("run_id", "") != run_id:
                continue
            results.append(
                ArtifactMetadata(
                    name=artifact_name,
                    current_version=entry.get("current_version", 0),
                    run_id=entry.get("run_id", ""),
                    agent=entry.get("agent"),
                    schema_name=entry.get("schema_name"),
                    created_at=entry.get("created_at", ""),
                    updated_at=entry.get("updated_at", ""),
                    size_bytes=entry.get("size_bytes", 0),
                )
            )
        return results

    def get_artifact_history(
        self,
        run_id: str,  # noqa: ARG002 — kept for API symmetry; all history is returned
        name: str,
    ) -> list[ArtifactVersion]:
        """Return the full version history for *name*, ascending by version number.

        Args:
            run_id: Informational; not used to filter — all versions are returned.
            name:   Artifact name.

        Returns:
            List of ArtifactVersion sorted ascending by version number.

        Raises:
            ValueError: on invalid artifact name.
        """
        self._validate_name(name)

        index = self._read_index()
        entry = index.get("artifacts", {}).get(name, {})
        versions_dict: dict[str, Any] = entry.get("versions", {})

        versions: list[ArtifactVersion] = []
        for ver_str, ver_data in versions_dict.items():
            if not ver_str.isdigit():
                continue  # skip non-integer version keys
            versions.append(
                ArtifactVersion(
                    version=int(ver_str),
                    run_id=ver_data.get("run_id", ""),
                    agent=ver_data.get("agent"),
                    created_at=ver_data.get("created_at", ""),
                    size_bytes=ver_data.get("size_bytes", 0),
                    run_status=ver_data.get("run_status", "unknown"),
                    checksum=ver_data.get("checksum", ""),
                )
            )

        # Ascending version order
        versions.sort(key=lambda v: v.version)
        return versions

    def compare_artifacts(
        self,
        run_a: str,
        run_b: str,
        name: str,
    ) -> ArtifactDiff:
        """Return a structural diff of *name* between the versions from *run_a* and *run_b*.

        Looks up the version saved by each run in the index, loads both, and
        computes a top-level key diff.

        Raises:
            ValueError: if no version is found for either run, or the files are missing.
        """
        self._validate_name(name)

        history = self.get_artifact_history(run_a, name)

        def _find_version(r_id: str) -> int | None:
            # Iterate in reverse (descending version) so the first match is the
            # *latest* version saved by this run, not the earliest.  A single run
            # can save the same artifact multiple times (e.g. iterative refinement),
            # and callers expect the most recent snapshot to be compared.
            for v in reversed(history):
                if v.run_id == r_id:
                    return v.version
            return None

        ver_a = _find_version(run_a)
        ver_b = _find_version(run_b)

        if ver_a is None:
            raise ValueError(f"No artifact {name!r} found for run {run_a!r}")
        if ver_b is None:
            raise ValueError(f"No artifact {name!r} found for run {run_b!r}")

        data_a = self.load_artifact(run_a, name, version=ver_a)
        data_b = self.load_artifact(run_b, name, version=ver_b)

        if data_a is None:
            raise ValueError(
                f"Could not load artifact {name!r} version {ver_a} (run_a={run_a!r})"
            )
        if data_b is None:
            raise ValueError(
                f"Could not load artifact {name!r} version {ver_b} (run_b={run_b!r})"
            )

        keys_a = set(data_a.keys())
        keys_b = set(data_b.keys())

        return ArtifactDiff(
            name=name,
            run_a=run_a,
            run_b=run_b,
            version_a=ver_a,
            version_b=ver_b,
            added=sorted(keys_b - keys_a),
            removed=sorted(keys_a - keys_b),
            changed=sorted(k for k in keys_a & keys_b if data_a[k] != data_b[k]),
        )

    def search_artifacts(
        self,
        query: str,
        artifact_type: str | None = None,
        agent: str | None = None,
    ) -> list[ArtifactMetadata]:
        """Search artifacts by type, agent, and/or text content.

        Filters (all applied as AND):
          - artifact_type: matches schema_name (or artifact name if schema_name is unset)
          - agent:         matches the agent that created the latest version
          - query:         case-insensitive substring search in the current JSON content

        Returns:
            List of matching ArtifactMetadata entries.
        """
        index = self._read_index()
        results: list[ArtifactMetadata] = []

        for artifact_name, entry in index.get("artifacts", {}).items():
            # Filter by artifact_type
            if artifact_type is not None:
                entry_type = entry.get("schema_name") or artifact_name
                if entry_type != artifact_type:
                    continue

            # Filter by agent
            if agent is not None and entry.get("agent") != agent:
                continue

            # Text search in current artifact content
            if query:
                artifact_path = self.artifacts_dir / f"{artifact_name}.json"
                if not artifact_path.exists():
                    continue
                try:
                    content = artifact_path.read_text(encoding="utf-8")
                    if query.lower() not in content.lower():
                        continue
                except OSError:
                    continue

            results.append(
                ArtifactMetadata(
                    name=artifact_name,
                    current_version=entry.get("current_version", 0),
                    run_id=entry.get("run_id", ""),
                    agent=entry.get("agent"),
                    schema_name=entry.get("schema_name"),
                    created_at=entry.get("created_at", ""),
                    updated_at=entry.get("updated_at", ""),
                    size_bytes=entry.get("size_bytes", 0),
                )
            )

        return results

    def mark_run_status(self, run_id: str, status: str) -> None:
        """Update the run_status for all versions created by *run_id*.

        Called after a run completes or fails so the retention policy can
        identify failed runs to preserve.

        Args:
            run_id: Run identifier.
            status: "completed", "failed", or any other string.
        """
        # DB update (best-effort)
        if self._db_repo is not None:
            try:
                loop = asyncio.get_event_loop()
                coro = self._db_repo.mark_run_status(run_id, status)
                if loop.is_running():
                    asyncio.ensure_future(coro)
                else:
                    loop.run_until_complete(coro)
            except Exception as exc:
                log.warning("DB mark_run_status failed for %s: %s", run_id, exc)

        if not self.config.index_enabled:
            return

        with self._locked_index() as index:
            for entry in index.get("artifacts", {}).values():
                for ver_data in entry.get("versions", {}).values():
                    if ver_data.get("run_id") == run_id:
                        ver_data["run_status"] = status

    def apply_retention_policy(
        self,
        max_age_days: int,
        max_runs: int,
        keep_failed: bool,
        dry_run: bool = False,
    ) -> RetentionResult:
        """Enforce retention policy on versioned artifact copies.

        Removes old artifact versions based on:
          - max_age_days: remove versions older than N days (0 = no age limit)
          - max_runs:     keep at most N unique run IDs (0 = no count limit)
          - keep_failed:  when True, never remove versions from failed runs

        The current artifacts/{name}.json files are NEVER removed — only versioned
        copies in .versions/ are eligible for deletion.

        Args:
            max_age_days: Cutoff in days; 0 disables the age check.
            max_runs:     Maximum distinct run_ids to retain; 0 disables.
            keep_failed:  Preserve all versions where run_status == "failed".
            dry_run:      If True, compute what would be deleted but do nothing.

        Returns:
            RetentionResult describing deleted and retained version counts.
        """
        index = self._read_index()
        now = datetime.now(timezone.utc)
        cutoff = (now - timedelta(days=max_age_days)) if max_age_days > 0 else None

        # Collect all (run_id, created_at) pairs from ALL artifact versions
        # to determine "old" runs by count
        run_timestamps: dict[str, datetime] = {}
        for entry in index.get("artifacts", {}).values():
            for ver_data in entry.get("versions", {}).values():
                r_id = ver_data.get("run_id", "")
                if not r_id:
                    continue
                ts_str = ver_data.get("created_at", "")
                try:
                    ts = datetime.fromisoformat(ts_str)
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                except (ValueError, TypeError):
                    ts = datetime(1970, 1, 1, tzinfo=timezone.utc)
                # Keep the LATEST timestamp for each run (most recent activity)
                if r_id not in run_timestamps or ts > run_timestamps[r_id]:
                    run_timestamps[r_id] = ts

        # Determine which run_ids to prune by count (keep the newest max_runs)
        runs_to_prune_by_count: set[str] = set()
        if max_runs > 0 and len(run_timestamps) > max_runs:
            sorted_runs = sorted(
                run_timestamps.items(), key=lambda x: x[1], reverse=True
            )
            runs_to_prune_by_count = {r for r, _ in sorted_runs[max_runs:]}

        deleted_paths: list[str] = []
        retained_versions = 0

        # Walk through each artifact's version history and decide what to delete
        versions_dir_base = self.artifacts_dir / ".versions"

        for artifact_name, entry in index.get("artifacts", {}).items():
            versions_dict: dict[str, Any] = entry.get("versions", {})
            vers_to_delete: list[str] = []

            for ver_str, ver_data in versions_dict.items():
                if not ver_str.isdigit():
                    continue

                r_id = ver_data.get("run_id", "")
                run_status = ver_data.get("run_status", "unknown")
                ts_str = ver_data.get("created_at", "")

                # Never delete failed-run versions when keep_failed is set
                if keep_failed and run_status == "failed":
                    retained_versions += 1
                    continue

                # Check age
                should_delete_age = False
                if cutoff is not None:
                    try:
                        ts = datetime.fromisoformat(ts_str)
                        if ts.tzinfo is None:
                            ts = ts.replace(tzinfo=timezone.utc)
                        if ts < cutoff:
                            should_delete_age = True
                    except (ValueError, TypeError):
                        pass

                # Check run count
                should_delete_count = r_id in runs_to_prune_by_count

                if should_delete_age or should_delete_count:
                    vers_to_delete.append(ver_str)
                else:
                    retained_versions += 1

            # Delete (or record) the pruned versions
            for ver_str in vers_to_delete:
                ver_path = versions_dir_base / artifact_name / f"v{ver_str}.json"
                deleted_paths.append(str(ver_path))
                if not dry_run:
                    try:
                        ver_path.unlink(missing_ok=True)
                    except OSError as exc:
                        log.warning("Failed to delete %s: %s", ver_path, exc)
                    # Remove from index
                    versions_dict.pop(ver_str, None)

        # Persist updated index (only when not dry_run)
        if not dry_run and deleted_paths:
            with self._locked_index() as idx:
                # Rebuild in-memory changes from what we computed above
                for artifact_name, entry in list(idx.get("artifacts", {}).items()):
                    vd = entry.get("versions", {})
                    # Remove deleted version keys
                    for ver_str in list(vd.keys()):
                        ver_path = versions_dir_base / artifact_name / f"v{ver_str}.json"
                        if not ver_path.exists() and str(ver_path) in deleted_paths:
                            vd.pop(ver_str, None)

        return RetentionResult(
            deleted_versions=len(deleted_paths),
            deleted_paths=deleted_paths,
            retained_versions=retained_versions,
            dry_run=dry_run,
        )
