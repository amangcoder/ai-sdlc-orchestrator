"""Knowledge-base MCP server integration — hybrid semantic + keyword search over project docs.

Discovers the knowledge-base-mcp server from the local installation and provides
helpers for prompt injection so planning agents can search prior documentation.

Tools exposed:
- search_knowledge: hybrid semantic + keyword search over indexed markdown
- list_sources: list configured knowledge sources with indexing status
- get_document_section: retrieve a specific section from a markdown document by heading
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MCP_SERVER_KEY = "knowledge-base"

# Well-known locations to search for the knowledge-base-mcp installation
_SEARCH_PATHS: list[Path] = [
    Path.home() / "Projects" / "knowledge-base-mcp",
    Path.home() / ".local" / "share" / "knowledge-base-mcp",
]


def find_knowledge_base_root() -> Path | None:
    """Find the knowledge-base-mcp installation directory."""
    for path in _SEARCH_PATHS:
        if (path / "dist" / "index.js").exists():
            return path
    return None


def get_knowledge_base_mcp_config(
    server_path: str = "",
) -> dict[str, Any] | None:
    """Return the knowledge-base MCP server config dict for direct SDK injection.

    Returns None if the server binary is not found or node is unavailable.
    """
    if server_path:
        p = Path(server_path).expanduser().resolve()
        js_path = p if p.name == "index.js" else p / "dist" / "index.js"
    else:
        root = find_knowledge_base_root()
        if root is None:
            logger.debug("knowledge-base-mcp installation not found in search paths")
            return None
        js_path = root / "dist" / "index.js"

    if not js_path.exists():
        logger.debug("knowledge-base-mcp dist/index.js not found: %s", js_path)
        return None

    if shutil.which("node") is None:
        logger.warning("node not found — cannot start knowledge-base MCP server")
        return None

    return {
        MCP_SERVER_KEY: {
            "type": "stdio",
            "command": "node",
            "args": [str(js_path), "serve"],
        }
    }


def build_knowledge_base_prompt_section(source_name: str = "") -> str:
    """Build a prompt section instructing the agent about available knowledge-base tools.

    Args:
        source_name: Optional source name to scope searches (e.g. "orchestrator-old").
                     If empty, agents will search across all configured sources.
    """
    source_hint = f" (source: `{source_name}`)" if source_name else " (all sources)"
    source_example = f', source: "{source_name}"' if source_name else ""

    return (
        "## Knowledge Base Search Tools (knowledge-base-mcp)\n\n"
        "You have access to a **semantic + keyword search index** over project documentation"
        f"{source_hint}. Search this BEFORE drafting requirements, architecture, or spawning\n"
        "research agents — prior decisions may already be documented.\n\n"
        "| Tool | Purpose | Example |\n"
        "|------|---------|--------|\n"
        f'| `mcp__knowledge-base__search_knowledge` | Search docs by query | `{{query: "auth approach"{source_example}, max_results: 5}}` |\n'
        "| `mcp__knowledge-base__list_sources` | List indexed documentation sources | `{}` |\n"
        f'| `mcp__knowledge-base__get_document_section` | Read a specific section of a doc | `{{filepath: "docs/arch.md", heading: "API Design"{source_example}}}` |\n\n'
        "**Workflow:** Call `list_sources` once at start, then `search_knowledge` before every\n"
        "major decision. On a hit, use the content to inform your output or avoid duplication."
    )
