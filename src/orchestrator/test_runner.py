"""Test-runner MCP server integration — structured test execution for QA agents."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MCP_SERVER_KEY = "test-runner"

# Well-known locations to search for the test-runner server
_SEARCH_PATHS = [
    "../sdlc-mcp-servers/test-runner",
    "../../sdlc-mcp-servers/test-runner",
]

# Orchestrator package root (e.g. ~/Projects/Orchestrator)
_ORCHESTRATOR_ROOT = Path(__file__).resolve().parents[2]


def _find_test_runner(configured_path: str, project_root: Path) -> Path | None:
    """Locate the test-runner MCP server installation directory."""
    if configured_path:
        p = Path(configured_path).resolve()
        if (p / "dist" / "index.js").exists():
            return p
        logger.warning(f"Configured test_runner server_path not found: {configured_path}")

    # Auto-detect from well-known sibling locations
    search_roots = [project_root]
    if _ORCHESTRATOR_ROOT != project_root:
        search_roots.append(_ORCHESTRATOR_ROOT)

    for root in search_roots:
        for rel in _SEARCH_PATHS:
            candidate = (root / rel).resolve()
            if (candidate / "dist" / "index.js").exists():
                return candidate

    return None


def get_test_runner_mcp_config(
    server_path: str,
    project_root: Path,
) -> dict[str, Any] | None:
    """Return the test-runner MCP server config dict for direct SDK injection.

    Returns None if the server is not found.
    The agent's cwd is already set to project_root by ClaudeAgentOptions,
    so the test-runner will auto-detect the project's test framework.
    """
    server_dir = _find_test_runner(server_path, project_root)
    if server_dir is None:
        return None

    server_js = server_dir / "dist" / "index.js"
    if not server_js.exists():
        return None

    return {
        MCP_SERVER_KEY: {
            "type": "stdio",
            "command": "node",
            "args": [str(server_js)],
        }
    }


def ensure_test_runner_mcp_config(
    server_path: str,
    target_project: Path,
    project_root: Path | None = None,
) -> bool:
    """Write test-runner entry to .mcp.json in the target project.

    Returns True if MCP was successfully configured.
    """
    server_dir = _find_test_runner(server_path, project_root or target_project)
    if server_dir is None:
        logger.info("Test-runner MCP server not found — QA agents will run tests via Bash")
        return False

    server_js = server_dir / "dist" / "index.js"
    if not server_js.exists():
        logger.warning("Test-runner dist/index.js not found — run 'npm run build' in the test-runner directory")
        return False

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
    }

    servers = mcp_data.setdefault("mcpServers", {})
    servers[MCP_SERVER_KEY] = server_entry

    mcp_path.write_text(json.dumps(mcp_data, indent=2) + "\n")
    logger.info(f"Test-runner MCP config written to {mcp_path}")
    return True


def cleanup_test_runner_mcp_config(target_project: Path) -> None:
    """Remove the test-runner MCP server entry from .mcp.json."""
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
        logger.info(f"Cleaned up test-runner MCP config from {mcp_path}")
