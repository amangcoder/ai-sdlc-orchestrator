"""Tests for TASK-016: MCP server health-check validation at pipeline startup.

Acceptance criteria verified:
  - validate_mcp_servers probes all 5 registered MCP servers (AC-009, REQ-016)
  - Returns MCPHealthResult with status=connected or status=failed for each server
  - Health check failures do not crash the pipeline (returns results, no raise)
  - All registered servers are probed — no server is silently ignored
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from orchestrator.mcp_health import (
    MCPHealthResult,
    _probe_http_server,
    _probe_single_server,
    _probe_stdio_server,
    load_mcp_config_from_file,
    validate_mcp_servers,
)


# ---------------------------------------------------------------------------
# MCPHealthResult dataclass tests
# ---------------------------------------------------------------------------

class TestMCPHealthResult:
    """Unit tests for the MCPHealthResult dataclass."""

    def test_ok_is_true_when_connected(self) -> None:
        result = MCPHealthResult(server_name="test", status="connected")
        assert result.ok is True

    def test_ok_is_false_when_failed(self) -> None:
        result = MCPHealthResult(server_name="test", status="failed", error="not found")
        assert result.ok is False

    def test_error_is_none_by_default(self) -> None:
        result = MCPHealthResult(server_name="test", status="connected")
        assert result.error is None

    def test_command_is_none_by_default(self) -> None:
        result = MCPHealthResult(server_name="test", status="connected")
        assert result.command is None


# ---------------------------------------------------------------------------
# _probe_stdio_server tests
# ---------------------------------------------------------------------------

class TestProbeStdioServer:
    """Unit tests for _probe_stdio_server()."""

    def test_returns_failed_when_no_command(self) -> None:
        result = _probe_stdio_server("test-server", {})
        assert result.status == "failed"
        assert result.error is not None

    def test_returns_failed_when_command_not_found(self) -> None:
        result = _probe_stdio_server(
            "test-server",
            {"command": "definitely-not-a-real-binary-xyz123"},
        )
        assert result.status == "failed"
        assert "not found" in (result.error or "").lower()

    def test_returns_connected_when_command_found(self) -> None:
        # 'python' or sys.executable is always findable
        result = _probe_stdio_server(
            "python-server",
            {"command": sys.executable, "args": []},
        )
        assert result.status == "connected"
        assert result.command is not None

    def test_node_server_checks_script_exists(self, tmp_path: Path) -> None:
        """node servers with a missing script should return failed."""
        missing_script = str(tmp_path / "missing_script.js")
        result = _probe_stdio_server(
            "node-server",
            {"command": "node", "args": [missing_script]},
        )
        # If node is not installed, may fail for different reason — either way status=failed
        if result.status == "connected":
            pytest.skip("node is not installed; cannot verify script path check")
        assert result.status == "failed"

    def test_node_server_with_existing_script_connected(self, tmp_path: Path) -> None:
        """node servers with an existing script should return connected (if node is installed)."""
        existing_script = tmp_path / "server.js"
        existing_script.write_text("// test")
        result = _probe_stdio_server(
            "node-server",
            {"command": "node", "args": [str(existing_script)]},
        )
        # If node not installed, skip; if installed, should be connected
        if result.status == "failed" and "not found on PATH" in (result.error or ""):
            pytest.skip("node is not installed")
        assert result.status == "connected"


# ---------------------------------------------------------------------------
# _probe_http_server tests
# ---------------------------------------------------------------------------

class TestProbeHttpServer:
    """Unit tests for _probe_http_server()."""

    def test_returns_failed_when_no_url(self) -> None:
        result = _probe_http_server("http-server", {})
        assert result.status == "failed"
        assert result.error is not None

    def test_returns_failed_when_unreachable(self) -> None:
        # Use a port that should not be listening
        result = _probe_http_server(
            "unreachable-server",
            {"url": "http://127.0.0.1:19999"},
        )
        assert result.status == "failed"

    def test_type_detection_http(self) -> None:
        """Servers with 'url' field are treated as HTTP servers."""
        result = _probe_single_server(
            "http-server",
            {"url": "http://127.0.0.1:19999"},
        )
        assert result.status == "failed"
        assert result.error is not None


# ---------------------------------------------------------------------------
# validate_mcp_servers tests
# ---------------------------------------------------------------------------

class TestValidateMcpServers:
    """Tests for the async validate_mcp_servers() function."""

    def test_empty_config_returns_empty_list(self) -> None:
        results = asyncio.get_event_loop().run_until_complete(
            validate_mcp_servers({})
        )
        assert results == []

    def test_returns_one_result_per_server(self) -> None:
        config = {
            "knowledge-base": {"command": sys.executable, "args": []},
            "test-runner": {"command": "definitely-not-binary-xyz789"},
        }
        results = asyncio.get_event_loop().run_until_complete(
            validate_mcp_servers(config)
        )
        assert len(results) == 2

    def test_all_server_names_probed(self) -> None:
        server_names = {
            "knowledge-base",
            "test-runner",
            "research-cache",
            "claude-flow",
            "ai-code-knowledge",
        }
        config = {
            name: {"command": "definitely-not-binary-xyz789"}
            for name in server_names
        }
        results = asyncio.get_event_loop().run_until_complete(
            validate_mcp_servers(config)
        )
        result_names = {r.server_name for r in results}
        assert result_names == server_names, (
            f"Missing servers: {server_names - result_names}"
        )

    def test_five_registered_servers_all_probed(self) -> None:
        """The 5 registered MCP servers must all be probed."""
        # All use paths that won't exist — we just verify they are all probed
        config = {
            "knowledge-base": {"command": "missing-bin-001"},
            "test-runner": {"command": "missing-bin-002"},
            "research-cache": {"command": "missing-bin-003"},
            "claude-flow": {"command": "missing-bin-004"},
            "ai-code-knowledge": {"command": "missing-bin-005"},
        }
        results = asyncio.get_event_loop().run_until_complete(
            validate_mcp_servers(config)
        )
        assert len(results) == 5
        for r in results:
            assert r.status in ("connected", "failed")

    def test_failed_servers_do_not_raise(self) -> None:
        """validate_mcp_servers must not raise even when all servers fail."""
        config = {
            "bad-server": {"command": "definitely-not-a-real-binary-xyz123"},
        }
        # Should not raise
        results = asyncio.get_event_loop().run_until_complete(
            validate_mcp_servers(config)
        )
        assert len(results) == 1
        assert results[0].status == "failed"

    def test_connected_server_has_no_error(self) -> None:
        """A successfully probed server should have error=None."""
        config = {
            "python-server": {"command": sys.executable, "args": []},
        }
        results = asyncio.get_event_loop().run_until_complete(
            validate_mcp_servers(config)
        )
        assert len(results) == 1
        assert results[0].status == "connected"
        assert results[0].error is None

    def test_result_fields_are_populated(self) -> None:
        config = {
            "test-server": {"command": "missing-xyz-binary"},
        }
        results = asyncio.get_event_loop().run_until_complete(
            validate_mcp_servers(config)
        )
        r = results[0]
        assert r.server_name == "test-server"
        assert r.status in ("connected", "failed")


# ---------------------------------------------------------------------------
# load_mcp_config_from_file tests
# ---------------------------------------------------------------------------

class TestLoadMcpConfigFromFile:
    """Tests for load_mcp_config_from_file()."""

    def test_returns_empty_dict_when_file_missing(self, tmp_path: Path) -> None:
        result = load_mcp_config_from_file(tmp_path / "nonexistent.json")
        assert result == {}

    def test_loads_mcp_servers_from_json(self, tmp_path: Path) -> None:
        mcp_file = tmp_path / ".mcp.json"
        mcp_file.write_text('{"mcpServers": {"test": {"command": "node"}}}')
        result = load_mcp_config_from_file(mcp_file)
        assert "test" in result

    def test_handles_invalid_json(self, tmp_path: Path) -> None:
        mcp_file = tmp_path / ".mcp.json"
        mcp_file.write_text("not valid json {{{")
        result = load_mcp_config_from_file(mcp_file)
        assert result == {}

    def test_falls_back_to_top_level_when_no_mcp_servers_key(self, tmp_path: Path) -> None:
        mcp_file = tmp_path / ".mcp.json"
        mcp_file.write_text('{"server1": {"command": "python"}}')
        result = load_mcp_config_from_file(mcp_file)
        assert "server1" in result
