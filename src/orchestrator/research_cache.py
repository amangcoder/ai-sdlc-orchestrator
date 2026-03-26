"""Research cache MCP server integration — two-tier persistent research cache for SDLC agents.

Mirrors the test_runner.py MCP integration pattern exactly.
Provides helpers for initializing, using, and cleaning up the research-cache MCP server.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from orchestrator.models import Finding, ResearchCache, ResearchCacheContext, ResearchEntry

logger = logging.getLogger(__name__)

MCP_SERVER_KEY = "research-cache"

# Well-known locations to search for the research-cache server (relative to project/orchestrator root)
_SEARCH_PATHS = [
    "../sdlc-mcp-servers/research-cache",
    "../../sdlc-mcp-servers/research-cache",
]

# Orchestrator package root (e.g. ~/Projects/Orchestrator)
_ORCHESTRATOR_ROOT = Path(__file__).resolve().parents[2]


def _find_research_server(server_path: str) -> Path | None:
    """Locate the research-cache MCP server dist/index.js.

    Searches configured server_path first, then well-known sibling locations.
    Returns Path to dist/index.js if found, else None.
    """
    if server_path:
        p = Path(server_path).expanduser().resolve()
        # Accept either a path to the directory or directly to dist/index.js
        if p.is_file() and p.name == "index.js":
            return p
        if (p / "dist" / "index.js").exists():
            return p / "dist" / "index.js"
        logger.warning(f"Configured research_cache server_path not found: {server_path}")

    # Auto-detect from well-known sibling locations
    search_roots = [_ORCHESTRATOR_ROOT]
    for root in search_roots:
        for rel in _SEARCH_PATHS:
            candidate = (root / rel).resolve()
            js_path = candidate / "dist" / "index.js"
            if js_path.exists():
                return js_path

    return None


def get_research_mcp_config(
    server_path: str,
    project_root: Path,
) -> dict[str, Any] | None:
    """Return the research-cache MCP server config dict for direct SDK injection.

    Returns None if the server is not found.

    The returned dict has the shape:
        {mcpServers: {"research-cache": {command, args, env}}}
    """
    js_path = _find_research_server(server_path)
    if js_path is None:
        return None

    global_dir = str(Path("~/.orchestrator/research").expanduser())
    local_dir = str(project_root / ".knowledge" / "research")

    return {
        "mcpServers": {
            MCP_SERVER_KEY: {
                "type": "stdio",
                "command": "node",
                "args": [str(js_path)],
                "env": {
                    "GLOBAL_RESEARCH_DIR": global_dir,
                    "PROJECT_RESEARCH_DIR": local_dir,
                    "PROJECT_ROOT": str(project_root),
                },
            }
        }
    }


def ensure_research_mcp_config(
    server_path: str,
    target_project: Path,
) -> bool:
    """Write research-cache entry to .mcp.json in the target project.

    Returns True if MCP was successfully configured.
    """
    js_path = _find_research_server(server_path)
    if js_path is None:
        logger.info("Research cache MCP server not found — agents will perform fresh research each run")
        return False

    global_dir = str(Path("~/.orchestrator/research").expanduser())
    local_dir = str(target_project / ".knowledge" / "research")

    server_entry = {
        "type": "stdio",
        "command": "node",
        "args": [str(js_path)],
        "env": {
            "GLOBAL_RESEARCH_DIR": global_dir,
            "PROJECT_RESEARCH_DIR": local_dir,
            "PROJECT_ROOT": str(target_project),
        },
    }

    # Read existing .mcp.json or create new
    mcp_path = target_project / ".mcp.json"
    mcp_data: dict[str, Any] = {}
    if mcp_path.exists():
        try:
            mcp_data = json.loads(mcp_path.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    servers = mcp_data.setdefault("mcpServers", {})
    servers[MCP_SERVER_KEY] = server_entry

    mcp_path.write_text(json.dumps(mcp_data, indent=2) + "\n")
    logger.info(f"Research cache MCP config written to {mcp_path}")
    return True


def cleanup_research_mcp_config(target_project: Path) -> None:
    """Remove the research-cache MCP server entry from .mcp.json."""
    mcp_path = target_project / ".mcp.json"
    if not mcp_path.exists():
        return

    try:
        mcp_data = json.loads(mcp_path.read_text())
    except (json.JSONDecodeError, OSError):
        return

    servers = mcp_data.get("mcpServers", {})
    if MCP_SERVER_KEY in servers:
        del servers[MCP_SERVER_KEY]
        if servers:
            mcp_path.write_text(json.dumps(mcp_data, indent=2) + "\n")
        else:
            # Remove the file entirely if we were the only entry
            mcp_path.unlink(missing_ok=True)
        logger.info(f"Cleaned up research-cache MCP config from {mcp_path}")


# ---------------------------------------------------------------------------
# Cache loading helpers
# ---------------------------------------------------------------------------

def _load_entries_from_dir(cache_dir: Path) -> list[ResearchEntry]:
    """Load all ResearchEntry objects from a tier directory.

    Reads index.json for the list of entries, then loads each entry JSON file.
    Returns an empty list if the directory or index doesn't exist.
    """
    index_path = cache_dir / "index.json"
    if not index_path.exists():
        return []

    try:
        index = json.loads(index_path.read_text())
    except (json.JSONDecodeError, OSError):
        logger.warning(f"Could not read research cache index: {index_path}")
        return []

    entries: list[ResearchEntry] = []
    entries_dir = cache_dir / "entries"
    for entry_id in index.get("entries", []):
        entry_path = entries_dir / f"{entry_id}.json"
        if not entry_path.exists():
            continue
        try:
            data = json.loads(entry_path.read_text())
            entries.append(ResearchEntry(**data))
        except Exception as e:
            logger.debug(f"Skipping malformed research cache entry {entry_id}: {e}")

    return entries


def load_cache(global_dir: Path, local_dir: Path) -> ResearchCache:
    """Load research cache from both tiers.

    Handles missing directories gracefully — returns empty lists for missing tiers.

    Args:
        global_dir: Path to the global (cross-project) cache directory.
        local_dir: Path to the local (per-project) cache directory.

    Returns:
        ResearchCache with global_entries and local_entries populated.
    """
    global_entries = _load_entries_from_dir(global_dir) if global_dir.exists() else []
    local_entries = _load_entries_from_dir(local_dir) if local_dir.exists() else []

    logger.debug(
        f"Loaded research cache: {len(global_entries)} global entries, "
        f"{len(local_entries)} local entries"
    )
    return ResearchCache(global_entries=global_entries, local_entries=local_entries)


# ---------------------------------------------------------------------------
# Cache lookup
# ---------------------------------------------------------------------------

def _tag_overlap(entry_tags: list[str], query_tags: list[str]) -> int:
    """Count the number of tags shared between an entry and a query."""
    return len(set(entry_tags) & set(query_tags))


def _is_expired(entry: ResearchEntry) -> bool:
    """Return True if the entry has expired based on its TTL."""
    try:
        created = datetime.fromisoformat(entry.created_at.replace("Z", "+00:00"))
        now = datetime.now(tz=timezone.utc)
        age_days = (now - created).days
        return age_days > entry.ttl_days
    except (ValueError, TypeError):
        logger.debug(f"Could not parse created_at for entry '{entry.topic}' — treating as expired")
        return True


def lookup(cache: ResearchCache, query: str, tags: list[str]) -> list[ResearchEntry]:
    """Look up research cache entries matching the query and tags.

    Filters:
    - Expired entries (age > ttl_days) are excluded.
    - Entries that have NO tag overlap AND whose topic does not contain the query string
      as a substring are excluded.

    Sorting:
    - Primary: tag overlap count (descending)
    - Secondary: created_at (descending — more recent first)

    Args:
        cache: Loaded ResearchCache to search.
        query: Natural-language query string (substring matched against topic).
        tags: Tags to match against entry tags.

    Returns:
        List of matching ResearchEntry objects, sorted by relevance.
    """
    all_entries = cache.global_entries + cache.local_entries
    results: list[tuple[int, str, ResearchEntry]] = []

    query_lower = query.lower()
    for entry in all_entries:
        if _is_expired(entry):
            continue
        overlap = _tag_overlap(entry.tags, tags)
        topic_match = query_lower in entry.topic.lower()
        if overlap == 0 and not topic_match:
            continue
        results.append((overlap, entry.created_at, entry))

    # Sort by tag overlap DESC, then created_at DESC
    results.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [r[2] for r in results]


# ---------------------------------------------------------------------------
# Cache save / eviction
# ---------------------------------------------------------------------------

def _entry_hash(topic: str, tags: list[str]) -> str:
    """Compute SHA-256 hash of (topic + sorted tags) for entry filename."""
    key = topic + "".join(sorted(tags))
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _atomic_write(path: Path, data: Any) -> None:
    """Write JSON data to path atomically via write-to-temp-then-rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def save_entry(
    cache: ResearchCache,
    entry: ResearchEntry,
    *,
    global_dir: Path | None = None,
    local_dir: Path | None = None,
    max_entries: int = 500,
) -> None:
    """Persist a ResearchEntry to the appropriate tier directory.

    Performs atomic index.json update (write-to-temp-then-rename).
    Uses fcntl.flock advisory locking around the read-modify-write cycle.
    Evicts oldest entries (by created_at) when max_entries is exceeded.

    The in-memory cache (ResearchCache) is also updated.

    Args:
        cache: In-memory ResearchCache to update.
        entry: The ResearchEntry to save.
        global_dir: Path to the global tier directory (required for global entries).
        local_dir: Path to the local tier directory (required for project entries).
        max_entries: Maximum number of entries per tier before eviction.
    """
    if entry.tier == "global":
        if global_dir is None:
            logger.warning("global_dir not provided — skipping global entry save")
            return
        tier_dir = global_dir
        in_memory_list = cache.global_entries
    else:
        if local_dir is None:
            logger.warning("local_dir not provided — skipping local entry save")
            return
        tier_dir = local_dir
        in_memory_list = cache.local_entries

    entries_dir = tier_dir / "entries"
    entries_dir.mkdir(parents=True, exist_ok=True)
    index_path = tier_dir / "index.json"

    entry_id = _entry_hash(entry.topic, entry.tags)
    entry_path = entries_dir / f"{entry_id}.json"

    # Advisory lock on index file for concurrent-safe read-modify-write
    lock_path = tier_dir / ".index.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)

            # Load current index
            if index_path.exists():
                try:
                    index = json.loads(index_path.read_text())
                except (json.JSONDecodeError, OSError):
                    index = {"entries": []}
            else:
                index = {"entries": []}

            entry_ids: list[str] = index.get("entries", [])

            # Write entry file
            _atomic_write(entry_path, entry.model_dump())

            # Add entry to index if not already present
            if entry_id not in entry_ids:
                entry_ids.append(entry_id)

            # Evict oldest entries if over limit
            if len(entry_ids) > max_entries:
                # Load all entries to find oldest
                loaded: list[tuple[str, str]] = []  # (entry_id, created_at)
                for eid in entry_ids:
                    ep = entries_dir / f"{eid}.json"
                    try:
                        data = json.loads(ep.read_text())
                        loaded.append((eid, data.get("created_at", "")))
                    except (json.JSONDecodeError, OSError):
                        loaded.append((eid, ""))
                # Sort by created_at ascending (oldest first)
                loaded.sort(key=lambda x: x[1])
                # Evict oldest entries
                num_to_evict = len(entry_ids) - max_entries
                for eid, _ in loaded[:num_to_evict]:
                    evict_path = entries_dir / f"{eid}.json"
                    evict_path.unlink(missing_ok=True)
                    entry_ids.remove(eid)
                    # Remove from in-memory list
                    in_memory_list[:] = [e for e in in_memory_list if _entry_hash(e.topic, e.tags) != eid]

            index["entries"] = entry_ids
            _atomic_write(index_path, index)

        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    # Update in-memory cache
    existing_ids = {_entry_hash(e.topic, e.tags) for e in in_memory_list}
    if entry_id not in existing_ids:
        in_memory_list.append(entry)
    else:
        # Replace existing in-memory entry
        for i, e in enumerate(in_memory_list):
            if _entry_hash(e.topic, e.tags) == entry_id:
                in_memory_list[i] = entry
                break

    logger.debug(f"Saved research entry '{entry.topic}' to {entry.tier} tier ({entry_id[:8]}...)")


# ---------------------------------------------------------------------------
# Artifact extraction
# ---------------------------------------------------------------------------

# Mapping: artifact stem -> list of (json_path, tier)
# json_path: dot-separated path to a list field in the artifact JSON
_ARTIFACT_EXTRACTION_MAP: dict[str, list[tuple[list[str], str]]] = {
    "architecture": [
        (["tech_decisions"], "global"),
    ],
    "engineering_plan": [
        (["implementation_order"], "global"),
        (["risk_areas"], "global"),
    ],
    "market_research": [
        (["trends"], "project"),
        (["recommendations"], "project"),
    ],
    "competitor_research": [
        (["competitors"], "project"),
        (["feature_matrix"], "project"),
    ],
    "threat_model": [
        (["recommendations"], "global"),
    ],
    "benchmark_report": [
        (["bottlenecks"], "project"),
        (["recommendations"], "project"),
    ],
}


def _artifact_stem_from_phase(phase: str) -> str | None:
    """Map phase name to expected artifact stem."""
    _PHASE_TO_ARTIFACT: dict[str, str] = {
        "architect": "architecture",
        "architecture": "architecture",
        "principal_engineer": "engineering_plan",
        "engineering_plan": "engineering_plan",
        "market_researcher": "market_research",
        "market_research": "market_research",
        "competitor_researcher": "competitor_research",
        "competitor_research": "competitor_research",
        "security_engineer": "threat_model",
        "threat_model": "threat_model",
        "caching_engineer": "benchmark_report",
        "benchmark_report": "benchmark_report",
    }
    return _PHASE_TO_ARTIFACT.get(phase)


def extract_research_from_artifact(
    artifact_path: Path,
    phase: str,
    run_id: str,
) -> list[ResearchEntry]:
    """Extract research entries from a completed phase artifact.

    Maps known artifact fields to ResearchEntry objects, applying the correct
    tier (global for stable knowledge, project for volatile knowledge).

    Returns an empty list if the artifact doesn't exist, the phase is not
    recognized, or the artifact has no extractable fields.

    Args:
        artifact_path: Filesystem path to the artifact JSON file.
        phase: Phase name (e.g. "architect", "market_researcher").
        run_id: Run ID for the source annotation.

    Returns:
        List of extracted ResearchEntry objects (not yet persisted).
    """
    if not artifact_path.exists():
        return []

    artifact_stem = artifact_path.stem
    extraction_config = _ARTIFACT_EXTRACTION_MAP.get(artifact_stem)

    if not extraction_config:
        # Try mapping phase name to artifact stem
        mapped_stem = _artifact_stem_from_phase(phase)
        if mapped_stem:
            extraction_config = _ARTIFACT_EXTRACTION_MAP.get(mapped_stem)
            if extraction_config:
                artifact_stem = mapped_stem

    if not extraction_config:
        return []

    try:
        artifact_data = json.loads(artifact_path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        logger.debug(f"Could not read artifact {artifact_path}: {e}")
        return []

    entries: list[ResearchEntry] = []
    now_iso = datetime.now(tz=timezone.utc).isoformat()

    for field_path, tier in extraction_config:
        # Navigate the field path
        value = artifact_data
        for key in field_path:
            if isinstance(value, dict):
                value = value.get(key)
            else:
                value = None
                break

        if value is None:
            continue

        # Convert to list of items if not already
        items: list[Any] = value if isinstance(value, list) else [value]

        for item in items:
            if not item:
                continue

            # Build a topic string from the item
            if isinstance(item, dict):
                topic = item.get("decision") or item.get("name") or item.get("trend") or str(item)[:120]
                content = json.dumps(item)
                tags = [artifact_stem, phase, tier]
                if "decision" in item:
                    tags.append("tech_decision")
                if "trend" in item:
                    tags.append("trend")
                if "recommendation" in item or "recommendations" in item:
                    tags.append("recommendation")
            elif isinstance(item, str):
                topic = item[:120]
                content = item
                tags = [artifact_stem, phase, tier]
            else:
                continue

            ttl_days = 7 if tier == "project" else 90

            entry = ResearchEntry(
                topic=topic,
                content=content,
                tags=tags,
                tier=tier,  # type: ignore[arg-type]
                created_at=now_iso,
                ttl_days=ttl_days,
                source_phase=phase,
                run_id=run_id,
                usage_count=0,
            )
            entries.append(entry)

    logger.debug(f"Extracted {len(entries)} research entries from {artifact_path.name} (phase={phase})")
    return entries


# ---------------------------------------------------------------------------
# Finding helpers
# ---------------------------------------------------------------------------

def flag_finding(findings: list[dict[str, Any]], finding: Finding) -> None:
    """Append a Finding to the findings accumulator list.

    Args:
        findings: The list to append to (typically config.research_cache_context.findings).
        finding: The Finding model instance to record.
    """
    findings.append(finding.model_dump())


def format_recommendations(findings: list[dict[str, Any]]) -> str:
    """Format a list of findings as a human-readable recommendations section.

    Args:
        findings: List of Finding dicts (from config.research_cache_context.findings).

    Returns:
        Formatted string with a header and one line per finding.
    """
    if not findings:
        return ""

    lines = ["\n🔍 Recommendations:"]
    for f in findings:
        severity = f.get("severity", "?")
        finding_text = f.get("finding", "")
        recommendation = f.get("recommendation", "")
        lines.append(f"  [{severity}] {finding_text} → {recommendation}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Path validation
# ---------------------------------------------------------------------------

def validate_cache_paths(global_dir: Path, local_dir: Path, project_root: Path) -> None:
    """Validate that cache directories are within allowed roots.

    Args:
        global_dir: Resolved global cache directory path.
        local_dir: Resolved local cache directory path.
        project_root: The project root directory.

    Raises:
        ValueError: If global_dir is not under Path.home() or local_dir is not
                    under project_root.
    """
    home = Path.home().resolve()
    resolved_global = global_dir.expanduser().resolve()
    resolved_local = local_dir.resolve()
    resolved_project = project_root.resolve()

    try:
        resolved_global.relative_to(home)
    except ValueError:
        raise ValueError(
            f"global_dir must be under the user home directory ({home}); "
            f"got {resolved_global}"
        )

    try:
        resolved_local.relative_to(resolved_project)
    except ValueError:
        raise ValueError(
            f"local_dir must be under project_root ({resolved_project}); "
            f"got {resolved_local}"
        )
