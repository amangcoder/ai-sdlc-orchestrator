"""AICoder knowledge integration — build codebase knowledge and configure MCP tools."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Well-known locations to search for AICoder installation
# Searched relative to project_root first, then relative to the orchestrator package
_AICODER_SEARCH_PATHS = [
    "../AICoder",       # sibling directory
    "../../AICoder",    # one level up
]

# Orchestrator package root (e.g. ~/Projects/Orchestrator)
_ORCHESTRATOR_ROOT = Path(__file__).resolve().parents[2]

MCP_SERVER_KEY = "ai-code-knowledge"

# Maps Orchestrator phase names to AICoder phase names for MCP tool calls
ORCHESTRATOR_TO_AICODER_PHASE: dict[str, str] = {
    "pm": "prd",
    "architect": "architecture",
    "principal_engineer": "engineering_plan",
    "tpm": "task_breakdown",
    "engineer": "implementation",
    "qa": "implementation",
    "reviewer": "implementation",
}


def map_phase_for_mcp(orchestrator_phase: str) -> str:
    """Translate Orchestrator phase name to AICoder phase name for MCP tool calls."""
    return ORCHESTRATOR_TO_AICODER_PHASE.get(orchestrator_phase, orchestrator_phase)


@dataclass
class KnowledgeResult:
    """Result from building codebase knowledge."""
    success: bool
    knowledge_root: Path | None = None
    build_time_ms: float = 0.0
    file_count: int = 0
    error: str | None = None


def _find_aicoder(configured_path: str, project_root: Path) -> Path | None:
    """Locate the AICoder installation directory."""
    if configured_path:
        p = Path(configured_path).resolve()
        if (p / "package.json").exists():
            return p
        logger.warning(f"Configured aicoder_path not found: {configured_path}")

    # Auto-detect from well-known sibling locations
    # Search relative to both the target project AND the orchestrator installation
    search_roots = [project_root]
    if _ORCHESTRATOR_ROOT != project_root:
        search_roots.append(_ORCHESTRATOR_ROOT)

    for root in search_roots:
        for rel in _AICODER_SEARCH_PATHS:
            candidate = (root / rel).resolve()
            if (candidate / "package.json").exists():
                # Verify it's actually AICoder by checking for the build script
                pkg = json.loads((candidate / "package.json").read_text())
                if pkg.get("name") == "ai-code-knowledge" or "build-knowledge" in pkg.get("scripts", {}):
                    return candidate

    # Check if globally available via npx
    if shutil.which("npx"):
        return None  # Will try npx as a fallback

    return None


def ensure_gitignore_entries(project_root: Path, entries: list[str] | None = None) -> None:
    """Ensure .knowledge/ and workspace/ are listed in the project's .gitignore.

    Idempotent — only appends entries that are missing.
    """
    if entries is None:
        entries = [".knowledge/", "workspace/"]

    gitignore_path = project_root / ".gitignore"

    existing_lines: set[str] = set()
    if gitignore_path.exists():
        try:
            existing_lines = {line.rstrip() for line in gitignore_path.read_text().splitlines()}
        except OSError:
            pass

    missing = [e for e in entries if e not in existing_lines and e.rstrip("/") not in existing_lines]
    if not missing:
        return

    # Append missing entries
    block = "\n# Orchestrator runtime artifacts\n" + "\n".join(missing) + "\n"
    try:
        with gitignore_path.open("a") as f:
            # Ensure we start on a new line
            if existing_lines and not gitignore_path.read_text().endswith("\n"):
                f.write("\n")
            f.write(block)
        logger.info(f"Added {missing} to {gitignore_path}")
    except OSError as e:
        logger.warning(f"Could not update .gitignore: {e}")


def _knowledge_is_fresh(project_root: Path, max_age_minutes: int, richness: str = "rich") -> bool:
    """Check if the .knowledge/ directory is recent enough to skip rebuilding.

    Also invalidates the cache if the requested richness level differs from
    what was used in the last build.
    """
    index_path = project_root / ".knowledge" / "index.json"
    if not index_path.exists():
        return False

    try:
        index = json.loads(index_path.read_text())

        # Invalidate if richness level changed
        existing_richness = index.get("richness", "minimal")
        if existing_richness != richness:
            logger.info(f"Knowledge richness changed ({existing_richness} → {richness}), forcing rebuild")
            return False

        last_built = index.get("lastBuilt")
        if not last_built:
            return False
        built_at = datetime.fromisoformat(last_built.replace("Z", "+00:00"))
        age_minutes = (datetime.now(timezone.utc) - built_at).total_seconds() / 60
        return age_minutes < max_age_minutes
    except (json.JSONDecodeError, ValueError, OSError):
        return False


async def build_knowledge(
    project_root: Path,
    aicoder_path: str = "",
    timeout_seconds: int = 60,
    skip_if_fresh_minutes: int = 5,
    richness: str = "rich",
    skip_vectors: bool = False,
    skip_features: bool = False,
) -> KnowledgeResult:
    """Build AICoder knowledge base for the target project.

    Runs AICoder's build-knowledge script against project_root.
    Skips if .knowledge/ is fresh enough and richness level matches.
    """
    knowledge_root = project_root / ".knowledge"

    # Skip if fresh (also checks richness match)
    if _knowledge_is_fresh(project_root, skip_if_fresh_minutes, richness=richness):
        try:
            index = json.loads((knowledge_root / "index.json").read_text())
            file_count = index.get("fileCount", 0)
        except (json.JSONDecodeError, OSError):
            file_count = 0
        logger.info(f"Knowledge base is fresh (< {skip_if_fresh_minutes}m old), skipping rebuild")
        return KnowledgeResult(
            success=True,
            knowledge_root=knowledge_root,
            build_time_ms=0,
            file_count=file_count,
        )

    aicoder_dir = _find_aicoder(aicoder_path, project_root)
    if aicoder_dir is None:
        return KnowledgeResult(
            success=False,
            error="AICoder installation not found. Set knowledge.aicoder_path in config or install AICoder as a sibling directory.",
        )

    # Build the knowledge base
    build_script = aicoder_dir / "scripts" / "build-knowledge.ts"
    if not build_script.exists():
        return KnowledgeResult(
            success=False,
            error=f"AICoder build script not found at {build_script}",
        )

    cmd = [
        "npx", "tsx",
        str(build_script),
        "--root", str(project_root),
        "--richness", richness,
    ]
    if skip_vectors:
        cmd.append("--skip-vectors")
    if skip_features:
        cmd.append("--skip-features")

    start = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(aicoder_dir),
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError:
        proc.kill()
        return KnowledgeResult(
            success=False,
            error=f"Knowledge build timed out after {timeout_seconds}s",
        )
    except FileNotFoundError:
        return KnowledgeResult(
            success=False,
            error="npx/tsx not found. Ensure Node.js is installed.",
        )
    except OSError as e:
        return KnowledgeResult(
            success=False,
            error=f"Failed to run knowledge build: {e}",
        )

    build_time_ms = (time.monotonic() - start) * 1000

    if proc.returncode != 0:
        error_text = stderr.decode()[:500] if stderr else "Unknown error"
        return KnowledgeResult(
            success=False,
            error=f"Knowledge build failed (exit {proc.returncode}): {error_text}",
        )

    # Read the index to get file count
    file_count = 0
    if (knowledge_root / "index.json").exists():
        try:
            index = json.loads((knowledge_root / "index.json").read_text())
            file_count = index.get("fileCount", 0)
        except (json.JSONDecodeError, OSError):
            pass

    logger.info(f"Knowledge base built in {build_time_ms:.0f}ms ({file_count} files)")
    return KnowledgeResult(
        success=True,
        knowledge_root=knowledge_root,
        build_time_ms=build_time_ms,
        file_count=file_count,
    )


def synthesize_brief(
    knowledge_root: Path,
    max_files: int = 30,
    max_symbols: int = 15,
) -> str:
    """Read .knowledge/ artifacts and produce a condensed codebase brief."""
    sections: list[str] = []

    # 1. Read index
    index = _read_json(knowledge_root / "index.json")
    if not index:
        return ""

    modules = index.get("modules", [])
    file_count = index.get("fileCount", 0)
    last_built = index.get("lastBuilt", "unknown")

    sections.append(
        f"**Project:** {file_count} source files across {len(modules)} modules ({', '.join(modules)})\n"
        f"**Knowledge built:** {last_built}"
    )

    # 2. Module dependency graph
    deps = _read_json(knowledge_root / "dependencies.json")
    if deps:
        edges = deps.get("edges", [])
        cycles = deps.get("cycles", [])
        if edges:
            dep_lines = [f"  {e['from']} → {e['to']}" for e in edges[:20]]
            sections.append("### Module Dependencies\n" + "\n".join(dep_lines))
        if cycles:
            sections.append(f"**Circular dependencies detected:** {len(cycles)} cycle(s)")

    # 3. Top symbols (most referenced)
    symbols = _read_json(knowledge_root / "symbols.json")
    if symbols and isinstance(symbols, list):
        # Sort by number of callers (most-called first)
        scored = []
        for s in symbols:
            caller_count = len(s.get("calledBy", []))
            if s.get("isExported", False):
                caller_count += 1  # Boost exported symbols
            scored.append((caller_count, s))
        scored.sort(key=lambda x: x[0], reverse=True)

        top = scored[:max_symbols]
        if top:
            sym_lines = []
            for count, s in top:
                sig = s.get("signature", s.get("name", "?"))
                if len(sig) > 120:
                    sig = sig[:117] + "..."
                file_loc = s.get("file", "?")
                line = s.get("line", "")
                callers = f" (called by {count})" if count > 0 else ""
                sym_lines.append(f"  - `{s.get('name', '?')}` in {file_loc}:{line}{callers}")
            sections.append("### Key Symbols\n" + "\n".join(sym_lines))

    # 4. File summaries (top files)
    summaries = _read_json(knowledge_root / "summaries" / "cache.json")
    if summaries and isinstance(summaries, dict):
        # Sort by number of exports (most important files first)
        file_entries = []
        for file_path, summary in summaries.items():
            export_count = len(summary.get("exports", []))
            purpose = summary.get("purpose", "")
            if purpose:
                file_entries.append((export_count, file_path, purpose))
        file_entries.sort(key=lambda x: x[0], reverse=True)

        top_files = file_entries[:max_files]
        if top_files:
            file_lines = [f"  - **{fp}:** {purpose}" for _, fp, purpose in top_files]
            sections.append("### File Purposes\n" + "\n".join(file_lines))

    # 5. Architecture docs (if exists and non-trivial)
    arch_path = knowledge_root / "architecture.md"
    if arch_path.exists():
        arch_content = arch_path.read_text().strip()
        # Only include if non-trivial (more than a template placeholder)
        if len(arch_content) > 100 and not arch_content.startswith("# TODO"):
            # Truncate to first 500 chars
            if len(arch_content) > 500:
                arch_content = arch_content[:497] + "..."
            sections.append("### Architecture Notes\n" + arch_content)

    # 6. Feature groups (if available)
    features = _read_json(knowledge_root / "features" / "index.json")
    if features and isinstance(features, list):
        feature_lines = []
        for fg in features[:10]:
            name = fg.get("name", "?")
            desc = fg.get("description", "")
            file_count = len(fg.get("files", []))
            line = f"  - **{name}** ({file_count} files)"
            if desc:
                line += f" — {desc}"
            feature_lines.append(line)
        if feature_lines:
            sections.append("### Feature Groups\n" + "\n".join(feature_lines))

    if not sections:
        return ""

    return "\n\n".join(sections)


def ensure_mcp_config(
    aicoder_path: str,
    target_project: Path,
    project_root: Path | None = None,
) -> bool:
    """Write .mcp.json in the target project with AICoder MCP server entry.

    Uses absolute paths so it works from worktrees too.
    Returns True if MCP was successfully configured.
    """
    aicoder_dir = _find_aicoder(aicoder_path, project_root or target_project)
    if aicoder_dir is None:
        logger.warning("Cannot configure MCP: AICoder not found")
        return False

    # Ensure the MCP server is built
    server_js = aicoder_dir / "mcp-server" / "dist" / "server.js"
    if not server_js.exists():
        logger.info("Building AICoder MCP server...")
        try:
            import subprocess
            result = subprocess.run(
                ["npm", "run", "build-mcp"],
                cwd=str(aicoder_dir),
                capture_output=True,
                timeout=30,
            )
            if result.returncode != 0:
                logger.warning(f"Failed to build MCP server: {result.stderr.decode()[:200]}")
                return False
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
            logger.warning(f"Failed to build MCP server: {e}")
            return False

    if not server_js.exists():
        logger.warning("MCP server binary not found after build")
        return False

    knowledge_root = target_project / ".knowledge"

    # Read existing .mcp.json or create new
    mcp_path = target_project / ".mcp.json"
    mcp_data: dict[str, Any] = {}
    if mcp_path.exists():
        try:
            mcp_data = json.loads(mcp_path.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    server_entry = {
        "type": "stdio",
        "command": "node",
        "args": [str(server_js)],
        "env": {
            "KNOWLEDGE_ROOT": str(knowledge_root),
        },
    }

    servers = mcp_data.setdefault("mcpServers", {})
    servers[MCP_SERVER_KEY] = server_entry

    mcp_path.write_text(json.dumps(mcp_data, indent=2) + "\n")
    logger.info(f"MCP config written to {mcp_path}")
    return True


def get_mcp_server_config(
    aicoder_path: str,
    project_root: Path,
) -> dict[str, Any] | None:
    """Return the MCP server config dict for direct SDK injection.

    This allows passing MCP servers directly to ClaudeAgentOptions.mcp_servers
    instead of relying on .mcp.json file discovery (which breaks in worktrees).
    """
    aicoder_dir = _find_aicoder(aicoder_path, project_root)
    if aicoder_dir is None:
        return None

    server_js = aicoder_dir / "mcp-server" / "dist" / "server.js"
    if not server_js.exists():
        return None

    knowledge_root = project_root / ".knowledge"
    if not knowledge_root.exists():
        return None

    return {
        MCP_SERVER_KEY: {
            "type": "stdio",
            "command": "node",
            "args": [str(server_js)],
            "env": {
                "KNOWLEDGE_ROOT": str(knowledge_root),
            },
        }
    }


def cleanup_mcp_config(target_project: Path) -> None:
    """Remove the AICoder MCP server entry from .mcp.json."""
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
        logger.info(f"Cleaned up MCP config from {mcp_path}")


def generate_artifact_digest(workspace: Path, artifact_name: str) -> str:
    """Read an artifact JSON and produce a condensed digest for prompt injection."""
    artifact_path = workspace / "artifacts" / f"{artifact_name}.json"
    if not artifact_path.exists():
        return ""

    try:
        data = json.loads(artifact_path.read_text())
    except (json.JSONDecodeError, OSError):
        return ""

    if artifact_name == "prd":
        return _digest_prd(data)
    elif artifact_name == "architecture":
        return _digest_architecture(data)
    elif artifact_name == "tasks":
        return _digest_tasks(data)
    elif artifact_name == "engineering_plan":
        return _digest_engineering_plan(data)
    else:
        # Generic: summarize top-level keys
        return _digest_generic(artifact_name, data)


def _digest_prd(data: dict) -> str:
    title = data.get("title", "Untitled")
    overview = data.get("overview", "")[:200]
    goals = data.get("goals", [])
    reqs = data.get("requirements", [])
    must = [r for r in reqs if r.get("priority") == "must"]
    criteria = data.get("acceptance_criteria", [])

    lines = [
        f"### PRD Digest: {title}",
        f"**Overview:** {overview}",
        f"**Goals:** {len(goals)} — " + "; ".join(g[:60] for g in goals[:3]),
        f"**Requirements:** {len(reqs)} total ({len(must)} must-have)",
    ]
    if must:
        lines.append("**Key Must-Haves:**")
        for r in must[:5]:
            lines.append(f"  - {r.get('id', '?')}: {r.get('description', '')[:80]}")
    lines.append(f"**Acceptance Criteria:** {len(criteria)} defined")
    return "\n".join(lines)


def _digest_architecture(data: dict) -> str:
    components = data.get("components", [])
    data_flow = data.get("data_flow", "")[:200]
    decisions = data.get("tech_decisions", [])

    lines = [
        "### Architecture Digest",
        f"**Components:** {len(components)}",
    ]
    for c in components[:8]:
        deps = ", ".join(c.get("dependencies", [])) or "none"
        lines.append(f"  - **{c.get('name', '?')}**: {c.get('responsibility', '')[:80]} (deps: {deps})")
    lines.append(f"**Data Flow:** {data_flow}")
    if decisions:
        lines.append(f"**Tech Decisions:** {len(decisions)}")
        for d in decisions[:3]:
            lines.append(f"  - {d.get('decision', '')[:80]}")
    return "\n".join(lines)


def _digest_tasks(data: dict) -> str:
    tasks = data.get("tasks", [])
    by_role: dict[str, int] = {}
    by_complexity: dict[str, int] = {}
    for t in tasks:
        role = t.get("assigned_role", "unknown")
        by_role[role] = by_role.get(role, 0) + 1
        comp = t.get("estimated_complexity", "unknown")
        by_complexity[comp] = by_complexity.get(comp, 0) + 1

    lines = [
        f"### Task Breakdown Digest ({len(tasks)} tasks)",
        f"**By role:** " + ", ".join(f"{r}: {c}" for r, c in sorted(by_role.items())),
        f"**By complexity:** " + ", ".join(f"{k}: {v}" for k, v in sorted(by_complexity.items())),
        "**Tasks:**",
    ]
    for t in tasks[:10]:
        deps = ", ".join(t.get("dependencies", [])) or "none"
        lines.append(f"  - {t.get('task_id', '?')}: {t.get('title', '')[:60]} [{t.get('assigned_role', '?')}] (deps: {deps})")
    if len(tasks) > 10:
        lines.append(f"  ... and {len(tasks) - 10} more tasks")
    return "\n".join(lines)


def _digest_engineering_plan(data: dict) -> str:
    strategy = data.get("strategy", "")[:200]
    order = data.get("implementation_order", [])
    risks = data.get("risk_areas", [])

    lines = [
        "### Engineering Plan Digest",
        f"**Strategy:** {strategy}",
        f"**Implementation Order:** {len(order)} steps",
    ]
    for step in order[:5]:
        if isinstance(step, dict):
            label = f"{step.get('phase', '?')}: {step.get('description', '')}"
            lines.append(f"  - {label[:80]}")
        else:
            lines.append(f"  - {str(step)[:80]}")
    if risks:
        risk_strs = []
        for r in risks[:3]:
            if isinstance(r, dict):
                risk_strs.append(str(r.get("description", r.get("risk", str(r))))[:60])
            else:
                risk_strs.append(str(r)[:60])
        lines.append(f"**Risks:** {', '.join(risk_strs)}")
    return "\n".join(lines)


def _digest_generic(artifact_name: str, data: dict) -> str:
    """Generic digest for unknown artifact types."""
    keys = list(data.keys())[:8]
    lines = [f"### {artifact_name.replace('_', ' ').title()} Digest"]
    for k in keys:
        v = data[k]
        if isinstance(v, str):
            lines.append(f"**{k}:** {v[:100]}")
        elif isinstance(v, list):
            lines.append(f"**{k}:** {len(v)} items")
        elif isinstance(v, dict):
            lines.append(f"**{k}:** {len(v)} entries")
        else:
            lines.append(f"**{k}:** {v}")
    return "\n".join(lines)


def update_cumulative_context(
    workspace: Path,
    phase_name: str,
    artifacts: list[str] | None = None,
) -> None:
    """Append key decisions from a completed phase to workspace/context.md."""
    context_path = workspace / "context.md"

    # Build the section for this phase
    section_lines = [f"\n## {phase_name.replace('_', ' ').title()} Phase\n"]

    artifacts_dir = workspace / "artifacts"
    artifact_names = artifacts or []

    # Auto-detect artifacts if none specified
    if not artifact_names:
        # Check what this phase produced
        phase_artifact_map = {
            # Canonical keys (new contract)
            "prd": ["prd"],
            "architecture": ["architecture", "tasks"],
            "engineering_plan": ["engineering_plan"],
            "task_breakdown": ["tasks"],
            "implementation": [],
            "qa": ["qa_report"],
            "reviewer": ["review"],
            # Legacy keys — backward-compat aliases (REQ-008)
            "pm": ["prd"],
            "architect": ["architecture", "tasks"],
            "principal_engineer": ["engineering_plan"],
            "tpm": ["tasks"],
            "engineer": [],
        }
        artifact_names = phase_artifact_map.get(phase_name, [])

    for name in artifact_names:
        digest = generate_artifact_digest(workspace, name)
        if digest:
            section_lines.append(digest)
            section_lines.append("")

    if len(section_lines) <= 1:
        # Nothing to add
        return

    section = "\n".join(section_lines)

    # Append to context.md
    existing = ""
    if context_path.exists():
        existing = context_path.read_text()

    if not existing:
        existing = "# Pipeline Context\n\nKey decisions and artifacts from each phase.\n"

    combined = existing + section

    # Enforce maximum context size to prevent unbounded growth
    MAX_CONTEXT_SIZE = 8192  # bytes
    if len(combined) > MAX_CONTEXT_SIZE:
        sections = combined.split("\n## ")
        while len("\n## ".join(sections)) > MAX_CONTEXT_SIZE and len(sections) > 1:
            sections.pop(0)
        combined = "\n## ".join(sections)

    context_path.write_text(combined)
    logger.info(f"Updated cumulative context for phase: {phase_name}")


def _read_json(path: Path) -> Any:
    """Read and parse a JSON file, returning None on failure."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
