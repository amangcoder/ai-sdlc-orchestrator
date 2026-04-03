"""RAG MCP server configuration for the artifact_rag_search tool.

When ``rag.enabled=true`` in the orchestrator config, this module builds
the MCP server configuration entry that makes ``artifact_rag_search`` available
to all pipeline agents.

Usage::

    from orchestrator.rag.mcp_tool import get_rag_mcp_config
    from orchestrator.models import OrchestratorConfig

    config = OrchestratorConfig(rag=RAGConfig(enabled=True))
    mcp_cfg = get_rag_mcp_config(config)
    if mcp_cfg:
        engine._mcp_servers.update(mcp_cfg)
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from orchestrator.models import OrchestratorConfig

# Key used in the mcpServers config dict
MCP_SERVER_KEY = "artifact-rag-search"

# Path to the MCP server entry-point script (sibling of this file)
_SERVER_SCRIPT = Path(__file__).resolve().parent / "mcp_server.py"


def get_rag_mcp_config(
    config: "OrchestratorConfig",
) -> dict[str, Any] | None:
    """Return the MCP server config dict for artifact RAG search.

    Returns ``None`` when ``rag.enabled=False`` or when the server script is
    not found.

    The returned dict has the shape expected by OrchestratorEngine._mcp_servers:
    ``{MCP_SERVER_KEY: {command, args, env}}``

    Args:
        config: Full orchestrator config.

    Returns:
        Dict suitable for merging into engine._mcp_servers, or None.
    """
    if not getattr(config, "rag", None) or not config.rag.enabled:
        return None

    if not _SERVER_SCRIPT.exists():
        logger.debug("RAG MCP server script not found at %s — skipping", _SERVER_SCRIPT)
        return None

    vector_store_path = str(
        Path(config.rag.vector_store_path).expanduser().resolve()
    )

    return {
        MCP_SERVER_KEY: {
            "command": sys.executable,
            "args": [str(_SERVER_SCRIPT)],
            "env": {
                "RAG_VECTOR_STORE_PATH": vector_store_path,
                "RAG_PROVIDER": config.rag.provider,
                "RAG_EMBEDDING_MODEL": config.rag.embedding_model,
                "RAG_TOP_K": str(config.rag.top_k),
            },
        }
    }
