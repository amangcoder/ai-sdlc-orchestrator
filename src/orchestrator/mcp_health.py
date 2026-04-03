"""MCP server health-check validation at pipeline startup.

Probes each registered MCP server to verify it is reachable before the
pipeline begins running.  Results are logged as structured entries so the
operator can immediately see which servers are healthy and which are not.

Usage::

    from orchestrator.mcp_health import validate_mcp_servers

    results = await validate_mcp_servers(mcp_config)
    for r in results:
        if r.status == "failed":
            logger.warning("MCP server %s is unavailable: %s", r.server_name, r.error)
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

logger = logging.getLogger(__name__)
_structlog = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class MCPHealthResult:
    """Health-check result for a single MCP server."""

    server_name: str
    status: str  # "connected" | "failed"
    error: str | None = None
    command: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """Return True when the server is reachable."""
        return self.status == "connected"


# ---------------------------------------------------------------------------
# Health-check probe functions
# ---------------------------------------------------------------------------


def _probe_stdio_server(name: str, server_cfg: dict[str, Any]) -> MCPHealthResult:
    """Probe a stdio-based MCP server by verifying the command executable exists.

    Checks:
    1. The ``command`` field specifies an executable that is findable on PATH or
       as an absolute path.
    2. For ``node``-based servers, the ``args[0]`` script path is checked to
       exist on disk.
    """
    command: str = server_cfg.get("command", "")
    args: list[str] = server_cfg.get("args", [])

    if not command:
        return MCPHealthResult(
            server_name=name,
            status="failed",
            error="No 'command' specified in MCP server config",
        )

    # Verify the command binary is findable
    resolved_cmd = shutil.which(command)
    if resolved_cmd is None:
        # Try as an absolute path
        cmd_path = Path(command)
        if not cmd_path.is_file():
            return MCPHealthResult(
                server_name=name,
                status="failed",
                command=command,
                error=f"Command '{command}' not found on PATH and is not an absolute file path",
            )
        resolved_cmd = str(cmd_path)

    # For node/npx-based servers the first argument is typically the script path
    if command in ("node",) and args:
        script_path = Path(args[0])
        if not script_path.exists():
            return MCPHealthResult(
                server_name=name,
                status="failed",
                command=command,
                error=f"Script file does not exist: {args[0]}",
                details={"script": args[0]},
            )

    return MCPHealthResult(
        server_name=name,
        status="connected",
        command=resolved_cmd,
        details={"type": server_cfg.get("type", "stdio"), "args_count": len(args)},
    )


def _probe_http_server(name: str, server_cfg: dict[str, Any]) -> MCPHealthResult:
    """Probe an HTTP-based MCP server with a basic TCP/HTTP reachability check."""
    url: str = server_cfg.get("url", "")
    if not url:
        return MCPHealthResult(
            server_name=name,
            status="failed",
            error="No 'url' specified in HTTP MCP server config",
        )

    # Attempt a fast TCP connection to verify reachability
    try:
        import urllib.parse
        import socket

        parsed = urllib.parse.urlparse(url)
        host = parsed.hostname or "localhost"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)

        sock = socket.create_connection((host, port), timeout=2.0)
        sock.close()
        return MCPHealthResult(
            server_name=name,
            status="connected",
            details={"url": url},
        )
    except (OSError, TimeoutError) as exc:
        return MCPHealthResult(
            server_name=name,
            status="failed",
            error=f"HTTP server unreachable at {url}: {exc}",
        )


def _probe_single_server(name: str, server_cfg: dict[str, Any]) -> MCPHealthResult:
    """Dispatch to the appropriate probe based on the server type/shape."""
    server_type = server_cfg.get("type", "")

    # Detect HTTP-based servers by URL presence or explicit type
    if server_type in ("http", "sse") or "url" in server_cfg:
        return _probe_http_server(name, server_cfg)

    # Default: stdio-based (node, npx, python, etc.)
    return _probe_stdio_server(name, server_cfg)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def validate_mcp_servers(
    mcp_config: dict[str, Any],
) -> list[MCPHealthResult]:
    """Probe all registered MCP servers and return health results.

    This function is designed to be called at pipeline startup.  It probes
    each server concurrently and returns a result list regardless of failures —
    failed servers are logged but do NOT crash the pipeline.

    Args:
        mcp_config: Dict mapping server name → server config dict.
                    Typically loaded from ``.mcp.json`` or the orchestrator config.

    Returns:
        List of :class:`MCPHealthResult` instances — one per server.
    """
    if not mcp_config:
        _structlog.debug("mcp_health.validate_mcp_servers: no servers configured")
        return []

    # Run probes concurrently in the thread-pool (probes may do blocking I/O)
    loop = asyncio.get_event_loop()
    tasks = [
        loop.run_in_executor(None, _probe_single_server, name, cfg)
        for name, cfg in mcp_config.items()
    ]
    results: list[MCPHealthResult] = list(await asyncio.gather(*tasks))

    # Log structured health-check summary
    connected = [r for r in results if r.ok]
    failed = [r for r in results if not r.ok]

    _structlog.info(
        "mcp_health.startup_check",
        total=len(results),
        connected=len(connected),
        failed=len(failed),
        connected_servers=[r.server_name for r in connected],
        failed_servers=[r.server_name for r in failed],
    )

    for result in results:
        if result.ok:
            _structlog.info(
                "mcp_health.server_connected",
                server=result.server_name,
                command=result.command,
                **{k: v for k, v in result.details.items()},
            )
        else:
            _structlog.warning(
                "mcp_health.server_failed",
                server=result.server_name,
                error=result.error,
                command=result.command,
            )

    return results


def load_mcp_config_from_file(mcp_json_path: Path) -> dict[str, Any]:
    """Load MCP server configuration from a ``.mcp.json`` file.

    Returns an empty dict if the file is missing or invalid.
    """
    import json

    if not mcp_json_path.exists():
        return {}
    try:
        data = json.loads(mcp_json_path.read_text(encoding="utf-8"))
        return data.get("mcpServers", data)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not load MCP config from %s: %s", mcp_json_path, exc)
        return {}
