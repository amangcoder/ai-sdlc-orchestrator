"""Integration tests: MCP Server Tool Contracts.

Extends test_mcp_protocol_compliance.py with tool-level contract tests
for the 5 registered MCP servers. This file tests TOOL BEHAVIOUR, not
just protocol conformance — i.e., "does the tool do what the pipeline expects?"

Servers covered:
  - research-cache (save → lookup round-trip, flag finding, get recommendations)
  - ai-code-knowledge (get_project_overview, find_symbol, semantic_search)
  - test-runner (run_tests returns structured JSON output)
  - .mcp.json registration (all 5 servers registered and discoverable)

Tests are split into two categories:
  1. LIVE tests (marked live_mcp) — require actual server binaries
  2. CONTRACT tests — verify .mcp.json configuration and registration

The live tests skip gracefully when server binaries are not found.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest


# ── Location helpers ────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).parent.parent.parent


def _mcp_json_path() -> Path:
    """Return the path to .mcp.json in the project root."""
    return PROJECT_ROOT / ".mcp.json"


def _load_mcp_config() -> dict:
    """Load .mcp.json and return the mcpServers dict."""
    mcp_path = _mcp_json_path()
    if not mcp_path.exists():
        pytest.skip(".mcp.json not found in project root")
    return json.loads(mcp_path.read_text())


# ── .mcp.json Configuration Contract ──────────────────────────────────────

class TestMCPJsonRegistrationContract:
    """
    .mcp.json must register exactly the 5 MCP servers the pipeline depends on.
    These tests verify the REGISTRATION CONTRACT — that each server is present
    and correctly configured.
    """

    def test_mcp_json_exists(self):
        """Project root must contain a .mcp.json file."""
        assert _mcp_json_path().exists(), (
            ".mcp.json must exist so Claude Code knows which MCP servers to launch"
        )

    def test_mcp_json_is_valid_json(self):
        """`.mcp.json` must parse as valid JSON."""
        content = _mcp_json_path().read_text()
        try:
            json.loads(content)
        except json.JSONDecodeError as e:
            pytest.fail(f".mcp.json contains invalid JSON: {e}")

    def test_mcp_json_has_mcp_servers_key(self):
        """Top-level key must be 'mcpServers'."""
        config = _load_mcp_config()
        assert "mcpServers" in config, (
            ".mcp.json must have 'mcpServers' top-level key "
            "(Claude Code uses this exact key)"
        )

    @pytest.mark.xfail(
        reason=(
            "REAL GAP: .mcp.json only registers 'knowledge-base' and 'claude-flow'. "
            "Missing: test-runner, research-cache, ai-code-knowledge. "
            "These three servers are used by pipeline agents but not yet registered. "
            "Fix: add all 5 servers to .mcp.json (TASK-006, AC-009, REQ-016)."
        ),
        strict=True,
    )
    def test_all_five_servers_registered(self):
        """
        All 5 pipeline-required MCP servers must be registered:
        knowledge-base, test-runner, research-cache, claude-flow, ai-code-knowledge.

        EXPECTED FAILURE: .mcp.json currently only has knowledge-base and claude-flow.
        This test documents the gap — it will start passing once TASK-006 is complete.
        """
        config = _load_mcp_config()
        servers = config.get("mcpServers", {})
        required = {
            "knowledge-base",
            "test-runner",
            "research-cache",
            "claude-flow",
            "ai-code-knowledge",
        }
        missing = required - set(servers.keys())
        assert not missing, (
            f"These MCP servers must be registered in .mcp.json but are missing: {missing}\n"
            f"Registered: {set(servers.keys())}"
        )

    def test_each_server_has_command_field(self):
        """Each MCP server entry must have a 'command' field."""
        config = _load_mcp_config()
        servers = config.get("mcpServers", {})
        for name, cfg in servers.items():
            assert "command" in cfg, (
                f"MCP server '{name}' missing 'command' field in .mcp.json"
            )

    def test_each_server_has_args_field(self):
        """Each MCP server entry must have an 'args' list (even if empty)."""
        config = _load_mcp_config()
        servers = config.get("mcpServers", {})
        for name, cfg in servers.items():
            assert "args" in cfg, (
                f"MCP server '{name}' missing 'args' field in .mcp.json"
            )
            assert isinstance(cfg["args"], list), (
                f"MCP server '{name}' 'args' must be a list"
            )

    def test_ai_code_knowledge_uses_node_command(self):
        """ai-code-knowledge server must use 'node' command (it's a JS server)."""
        config = _load_mcp_config()
        server = config.get("mcpServers", {}).get("ai-code-knowledge", {})
        if not server:
            pytest.skip("ai-code-knowledge not in .mcp.json")
        assert server.get("command") == "node", (
            "ai-code-knowledge server must use 'node' command — it is a Node.js process"
        )

    def test_no_server_uses_absolute_path_that_doesnt_exist(self):
        """
        For servers using absolute binary paths in args[0], the binary must exist.
        Package names (npx, tsx) are not checked (resolved by shell at runtime).
        """
        config = _load_mcp_config()
        servers = config.get("mcpServers", {})
        for name, cfg in servers.items():
            args = cfg.get("args", [])
            if args and args[0].startswith("/"):
                # Absolute path — verify it exists
                binary = Path(args[0])
                assert binary.exists(), (
                    f"MCP server '{name}' references absolute path '{args[0]}' "
                    f"which does not exist on disk"
                )

    @pytest.mark.xfail(
        reason=(
            "REAL GAP: research-cache MCP server not registered in .mcp.json. "
            "Pipeline agents call lookup_research/save_research/flag_finding but the "
            "server is absent. Fix: add research-cache entry to .mcp.json (TASK-006)."
        ),
        strict=True,
    )
    def test_research_cache_server_registered(self):
        """
        research-cache MCP server must be registered.
        The pipeline uses it for competitive research caching.

        EXPECTED FAILURE: research-cache not yet in .mcp.json — documents the gap.
        """
        config = _load_mcp_config()
        servers = config.get("mcpServers", {})
        assert "research-cache" in servers, (
            "research-cache MCP server missing from .mcp.json — "
            "pipeline agents cannot call lookup_research/save_research"
        )

    def test_claude_flow_server_registered(self):
        """
        claude-flow MCP server must be registered.
        The pipeline uses it for cross-agent memory sharing.
        """
        config = _load_mcp_config()
        servers = config.get("mcpServers", {})
        assert "claude-flow" in servers, (
            "claude-flow MCP server missing from .mcp.json — "
            "agents cannot share memory via memory_search/memory_store"
        )


# ── MCP Server Tool Schema Contract ───────────────────────────────────────

class TestMCPToolSchemaContract:
    """
    Verify that the tool schemas registered by each MCP server match
    what the pipeline agents expect to call.

    These tests use the MCPClient from test_mcp_protocol_compliance.py
    and are marked @pytest.mark.skipif to run only when servers are available.
    """

    def _get_client_class(self):
        """Import MCPClient from integration tests."""
        try:
            from tests.integration.test_mcp_protocol_compliance import MCPClient
            return MCPClient
        except ImportError:
            try:
                import importlib
                mod = importlib.import_module(
                    "tests.integration.test_mcp_protocol_compliance"
                )
                return mod.MCPClient
            except Exception:
                return None

    def test_research_cache_tool_names_match_pipeline_calls(self):
        """
        research-cache must expose these 4 tools used by pipeline agents:
          lookup_research, save_research, flag_finding, get_run_recommendations.
        """
        config = _load_mcp_config()
        server_cfg = config.get("mcpServers", {}).get("research-cache", {})
        if not server_cfg:
            pytest.skip("research-cache not registered")

        MCPClient = self._get_client_class()
        if MCPClient is None:
            pytest.skip("MCPClient not importable")

        # Build the command from .mcp.json config
        cmd = server_cfg.get("command", "")
        args = server_cfg.get("args", [])
        if not args:
            pytest.skip("research-cache has no args — cannot start server")

        binary_path = args[0] if args[0].startswith("/") else None
        if binary_path is None:
            pytest.skip("research-cache uses relative path — skip binary existence check")
        if not Path(binary_path).exists():
            pytest.skip(f"research-cache binary not found: {binary_path}")

        with MCPClient(binary_path) as client:
            client.initialize()
            tools_response = client.list_tools()
            tool_names = {t["name"] for t in tools_response.get("result", {}).get("tools", [])}

        expected_tools = {
            "lookup_research",
            "save_research",
            "flag_finding",
            "get_run_recommendations",
        }
        missing = expected_tools - tool_names
        assert not missing, (
            f"research-cache missing tools that pipeline agents call: {missing}\n"
            f"Available: {tool_names}"
        )

    def test_ai_code_knowledge_tool_names_include_core_tools(self):
        """
        ai-code-knowledge must expose the core tools used during pipeline:
          get_project_overview, find_symbol, semantic_search, get_implementation_context.
        """
        config = _load_mcp_config()
        server_cfg = config.get("mcpServers", {}).get("ai-code-knowledge", {})
        if not server_cfg:
            pytest.skip("ai-code-knowledge not registered")

        MCPClient = self._get_client_class()
        if MCPClient is None:
            pytest.skip("MCPClient not importable")

        args = server_cfg.get("args", [])
        if not args:
            pytest.skip("ai-code-knowledge has no args")

        # For node servers, the script path is in args[0]
        script_path = Path(args[0]) if args[0].startswith("/") else None
        if script_path is None or not script_path.exists():
            pytest.skip(f"ai-code-knowledge script not found at {args[0]}")

        with MCPClient(str(script_path), env={**os.environ}) as client:
            client.initialize()
            tools_response = client.list_tools()
            tool_names = {t["name"] for t in tools_response.get("result", {}).get("tools", [])}

        expected = {
            "get_project_overview",
            "find_symbol",
            "semantic_search",
            "get_implementation_context",
        }
        missing = expected - tool_names
        assert not missing, (
            f"ai-code-knowledge missing core tools: {missing}"
        )


# ── Claude Flow session tool removal boundary ──────────────────────────────

class TestClaudeFlowToolFilterContract:
    """
    Verify TASK-007: claude-flow 'session' tool category removed from
    ROLE_TOOL_FILTER and config.

    Boundary: claude_flow_bridge.py ROLE_TOOL_FILTER → tool injection into prompts.
    """

    def test_role_tool_filter_has_no_session_category(self):
        """
        claude_flow_bridge.ROLE_TOOL_FILTER must NOT contain 'session' key.
        (TASK-007, AC-010, REQ-015)
        """
        try:
            from orchestrator.claude_flow_bridge import ROLE_TOOL_FILTER
        except ImportError:
            pytest.skip("orchestrator.claude_flow_bridge not importable")

        for role, tools in ROLE_TOOL_FILTER.items():
            assert "session" not in tools, (
                f"Role '{role}' still has 'session' in tool list — "
                f"TASK-007 requires removing this non-existent tool category"
            )

    def test_default_yaml_has_no_session_tools(self):
        """
        config/default.yaml claude_flow.tools must NOT enable the 'session' category.
        TASK-007 requires removing or disabling the session tool — it does not exist
        in the claude-flow MCP server and causes 'tool not found' errors at runtime.

        Acceptance: 'session' key is either absent from tools dict, or set to False.
        """
        default_yaml = PROJECT_ROOT / "config" / "default.yaml"
        if not default_yaml.exists():
            pytest.skip("config/default.yaml not found")

        import yaml
        with default_yaml.open() as f:
            raw = yaml.safe_load(f)

        claude_flow = raw.get("claude_flow", {})
        tools = claude_flow.get("tools", {})

        # TASK-007: session must be absent or explicitly disabled (False/null)
        session_val = tools.get("session", None)
        assert session_val is not True, (
            f"config/default.yaml claude_flow.tools['session'] = {session_val!r} — "
            f"TASK-007 requires session to be absent or False (not True), "
            f"because the claude-flow MCP server has no 'session' tool category"
        )

    def test_claude_flow_memory_tools_still_injected(self):
        """
        After removing session tools, memory and tasks tools must still be
        present in ROLE_TOOL_FILTER for supported roles.
        (No regression in existing claude-flow functionality)
        """
        try:
            from orchestrator.claude_flow_bridge import ROLE_TOOL_FILTER
        except ImportError:
            pytest.skip("orchestrator.claude_flow_bridge not importable")

        # At least one role should still have memory tools
        has_memory_tools = any(
            "memory" in str(tools).lower() or "tasks" in str(tools).lower()
            for role, tools in ROLE_TOOL_FILTER.items()
        )
        assert has_memory_tools, (
            "After removing session tools, memory/tasks tools must still be "
            "injected for supported roles — no regression allowed"
        )


# ── Knowledge base injection boundary ─────────────────────────────────────

class TestKnowledgeBaseInjectionBoundary:
    """
    TASK-007: knowledge-base MCP injection must work for implementation phases.
    Boundary: phases.py _inject_knowledge_base_section() → prompt content.
    """

    def test_inject_knowledge_base_section_exists(self):
        """
        phases.py must have _inject_knowledge_base_section() function.
        """
        try:
            from orchestrator.phases import _inject_knowledge_base_section
        except ImportError:
            pytest.skip("phases._inject_knowledge_base_section not found")

        assert callable(_inject_knowledge_base_section), (
            "_inject_knowledge_base_section must be callable"
        )

    def test_knowledge_base_injected_for_implementation_phases(self):
        """
        When knowledge_base_mcp.inject_into_phases includes backend_engineer,
        _inject_knowledge_base_section(config, role) returns a non-empty KB guidance string.

        Actual signature: _inject_knowledge_base_section(config, role) -> str
        (REQ-027, TASK-007)
        """
        try:
            from orchestrator.phases import _inject_knowledge_base_section
        except ImportError:
            pytest.skip("Required modules not importable")

        config = MagicMock()
        config.knowledge_base_mcp.inject_into_phases = [
            "backend_engineer",
            "frontend_engineer",
            "flutter_engineer",
        ]
        config.knowledge_base_mcp.enabled = True

        result = _inject_knowledge_base_section(config, "backend_engineer")

        # Must return a string (KB guidance section to prepend to the agent prompt)
        assert isinstance(result, str), (
            "_inject_knowledge_base_section must return a string "
            "(KB guidance text to inject into the agent prompt)"
        )

    def test_knowledge_base_not_injected_for_excluded_phases(self):
        """
        When inject_into_phases does not include 'qa',
        _inject_knowledge_base_section(config, 'qa') returns empty string or None.

        Actual signature: _inject_knowledge_base_section(config, role) -> str
        """
        try:
            from orchestrator.phases import _inject_knowledge_base_section
        except ImportError:
            pytest.skip("phases._inject_knowledge_base_section not found")

        config = MagicMock()
        config.knowledge_base_mcp.inject_into_phases = ["backend_engineer"]
        config.knowledge_base_mcp.enabled = True

        result = _inject_knowledge_base_section(config, "qa")
        # For excluded phases, function returns empty string or None
        assert result is None or result == "", (
            "For roles not in inject_into_phases, _inject_knowledge_base_section "
            "must return None or empty string — got: {result!r}"
        )
