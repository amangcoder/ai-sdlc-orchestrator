"""Bridge to Ruflo/claude-flow MCP tools for cross-agent memory, sessions, and tasks.

Discovers the claude-flow MCP server from the local AITools installation and
configures only the tools that provide real value to the orchestrator pipeline:
- memory/store, memory/search, memory/list (cross-agent semantic memory)
- session/save, session/restore (pipeline session persistence)
- tasks/create, tasks/list, tasks/status, tasks/dependencies (cross-agent task coordination)

Swarm, agent, federation, and SONA tools are excluded — they duplicate
orchestrator-native functionality or are stubs.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Paths where claude-flow may be installed (ordered by preference)
_SEARCH_PATHS: list[Path] = [
    Path.home() / "Projects" / "AITools" / "ruflo",
    Path.home() / "Projects" / "ruflo",
    Path.home() / ".local" / "share" / "claude-flow",
]

# Only expose tools that have real, working backends
_ALLOWED_TOOLS: list[str] = [
    "memory/store",
    "memory/search",
    "memory/list",
    "session/save",
    "session/restore",
    "session/list",
    "tasks/create",
    "tasks/list",
    "tasks/status",
    "tasks/cancel",
    "tasks/assign",
    "tasks/update",
    "tasks/dependencies",
    "tasks/results",
]

# Role-based tool filtering: which roles get which tool categories
ROLE_TOOL_FILTER: dict[str, list[str]] = {
    # Planning agents: can store/search memory and read task dependencies
    "product_manager": ["memory/store", "memory/search", "memory/list"],
    "software_architect": ["memory/store", "memory/search", "memory/list", "tasks/dependencies"],
    "principal_engineer": ["memory/store", "memory/search", "memory/list", "tasks/create", "tasks/dependencies"],
    # Engineers: can read/write memory, create tasks, check dependencies
    "backend_engineer": ["memory/store", "memory/search", "tasks/create", "tasks/status", "tasks/dependencies", "tasks/update"],
    "frontend_engineer": ["memory/store", "memory/search", "tasks/create", "tasks/status", "tasks/dependencies", "tasks/update"],
    "flutter_engineer": ["memory/store", "memory/search", "tasks/create", "tasks/status", "tasks/dependencies", "tasks/update"],
    "database_engineer": ["memory/store", "memory/search", "tasks/create", "tasks/status", "tasks/dependencies"],
    # QA: can search memory and read tasks
    "qa_planner": ["memory/search", "memory/list", "tasks/list", "tasks/status"],
    "qa_executor": ["memory/search", "tasks/list", "tasks/status", "tasks/results"],
    # Reviewers: read-only memory + task results
    "backend_code_reviewer": ["memory/search", "memory/list", "tasks/list", "tasks/results"],
    "frontend_code_reviewer": ["memory/search", "memory/list", "tasks/list", "tasks/results"],
    "security_engineer": ["memory/search", "memory/list", "tasks/list"],
    # Session tools: only exposed to the orchestrator engine, not individual agents
}

# Default tools for roles not explicitly listed
_DEFAULT_TOOLS: list[str] = ["memory/search", "memory/list"]


def find_claude_flow_root() -> Path | None:
    """Find the claude-flow/ruflo installation directory."""
    for path in _SEARCH_PATHS:
        server_entry = path / "v3" / "mcp" / "server-entry.ts"
        if server_entry.exists():
            return path
    return None


def _check_tsx_available() -> bool:
    """Check if tsx (TypeScript executor) is available."""
    return shutil.which("npx") is not None


def get_claude_flow_mcp_config(
    ruflo_path: str | None = None,
) -> dict[str, Any] | None:
    """Build MCP server config for claude-flow tools.

    Returns a dict suitable for merging into the orchestrator's MCP server config,
    or None if claude-flow is not found or tsx is not available.
    """
    root = Path(ruflo_path) if ruflo_path else find_claude_flow_root()
    if root is None:
        logger.debug("Claude-flow installation not found in search paths")
        return None

    server_entry = root / "v3" / "mcp" / "server-entry.ts"
    if not server_entry.exists():
        logger.debug(f"Claude-flow server entry not found: {server_entry}")
        return None

    if not _check_tsx_available():
        logger.warning("npx not found — cannot start claude-flow MCP server")
        return None

    # Build the tool filter list for the --tools flag
    tools_csv = ",".join(_ALLOWED_TOOLS)

    return {
        "claude-flow": {
            "command": "npx",
            "args": [
                "tsx",
                str(server_entry),
                "--tools", tools_csv,
            ],
        },
    }


def get_tools_for_role(agent_role: str) -> list[str]:
    """Return the list of claude-flow tools allowed for a given agent role."""
    return ROLE_TOOL_FILTER.get(agent_role, _DEFAULT_TOOLS)


def build_claude_flow_prompt_section(agent_role: str) -> str:
    """Build a prompt section instructing the agent about available claude-flow tools.

    Only includes instructions for tools the agent's role is allowed to use.
    """
    tools = get_tools_for_role(agent_role)
    if not tools:
        return ""

    has_memory = any(t.startswith("memory/") for t in tools)
    has_tasks = any(t.startswith("tasks/") for t in tools)

    sections: list[str] = []
    sections.append("## Cross-Agent Coordination Tools (claude-flow)")

    if has_memory:
        sections.append(
            "You have access to a **shared memory system** across all agents in this pipeline.\n"
            "Use these tools to share discoveries, decisions, and context with other agents:\n"
        )
        if "memory/store" in tools:
            sections.append(
                "- **mcp__claude-flow__memory_store**: Store a memory entry for other agents to find.\n"
                "  Use this to record architectural decisions, discovered patterns, codebase findings,\n"
                "  and any insight that would help downstream agents. Include descriptive tags.\n"
                '  Example: `{content: "Auth uses JWT with 15min expiry", type: "semantic", '
                'category: "architecture", tags: ["auth", "jwt"], importance: 0.9}`\n'
            )
        if "memory/search" in tools:
            sections.append(
                "- **mcp__claude-flow__memory_search**: Search for memories stored by other agents.\n"
                "  Before starting your analysis, search for relevant context from prior phases.\n"
                '  Example: `{query: "authentication approach", searchType: "hybrid"}`\n'
            )
        if "memory/list" in tools:
            sections.append(
                "- **mcp__claude-flow__memory_list**: List all stored memories with filtering.\n"
            )

    if has_tasks:
        sections.append(
            "You have access to a **shared task coordination system**:\n"
        )
        if "tasks/create" in tools:
            sections.append(
                "- **mcp__claude-flow__tasks_create**: Create a task visible to other agents.\n"
                "  Use this to flag dependencies or coordinate with parallel engineers.\n"
            )
        if "tasks/dependencies" in tools:
            sections.append(
                "- **mcp__claude-flow__tasks_dependencies**: Check task dependency chains.\n"
                "  Check this before starting work to see if other agents have flagged blockers.\n"
            )
        if "tasks/status" in tools:
            sections.append(
                "- **mcp__claude-flow__tasks_status**: Check the status of a specific task.\n"
            )
        if "tasks/results" in tools:
            sections.append(
                "- **mcp__claude-flow__tasks_results**: Get the results/output of completed tasks.\n"
            )

    return "\n".join(sections)
