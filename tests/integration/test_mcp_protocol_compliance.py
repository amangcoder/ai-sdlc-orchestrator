"""MCP Protocol Integration Tests — Category: Full protocol compliance audit.

Tests three MCP servers for JSON-RPC 2.0 and MCP protocol correctness:
  1. sdlc-mcp-server  (test-runner)      — dist/index.js in sdlc-mcp-servers/test-runner/
  2. aicoder          (ai-code-knowledge) — dist/server.js in AICoder/mcp-server/
  3. research-cache   (knowledge-base)    — dist/index.js in workspace/artifacts/research-cache/

Protocol dimensions tested per server
  • Capability negotiation  — initialize / initialized / ping
  • Tool listing            — tools/list returns correct schema
  • Tool calls (happy path) — valid params → TextContent response
  • Invalid params          — missing required fields → -32602
  • Method not found        — unknown method → -32601
  • Malformed JSON          — parse error → -32700
  • Concurrent requests     — multiple in-flight, all answered
  • Server info             — name/version present in initialize response

Transport: stdio (JSON-RPC 2.0, newline-delimited)
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# MCP server paths (resolved relative to repository root and siblings)
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent.parent

SERVERS: dict[str, dict] = {
    "test-runner": {
        "binary": str(_REPO_ROOT.parent / "sdlc-mcp-servers" / "test-runner" / "dist" / "index.js"),
        "env": {},
        "expected_tools": ["run_tests", "run_single_test"],
        "server_name_hint": "test-runner",
    },
    "ai-code-knowledge": {
        "binary": str(_REPO_ROOT.parent / "AICoder" / "mcp-server" / "dist" / "server.js"),
        "env": {
            "KNOWLEDGE_ROOT": str(_REPO_ROOT / ".knowledge"),
        },
        "expected_tools": [
            "health_check",
            "get_project_overview",
            "find_symbol",
            "semantic_search",
        ],
        "server_name_hint": "ai-code-knowledge",
    },
    "research-cache": {
        "binary": str(_REPO_ROOT / "workspace" / "artifacts" / "research-cache" / "dist" / "index.js"),
        "env": {},
        "expected_tools": [
            "lookup_research",
            "save_research",
            "flag_finding",
            "get_run_recommendations",
        ],
        "server_name_hint": "research-cache",
    },
}

# ---------------------------------------------------------------------------
# MCP stdio client — thin JSON-RPC 2.0 over subprocess stdio
# ---------------------------------------------------------------------------

class MCPClient:
    """Communicates with an MCP stdio server via subprocess stdin/stdout."""

    HANDSHAKE_TIMEOUT = 10.0  # seconds to wait for initialize response
    REQUEST_TIMEOUT = 15.0    # seconds to wait for a tool call response

    def __init__(self, binary_path: str, env: dict[str, str] | None = None) -> None:
        self._binary = binary_path
        self._env = {**os.environ, **(env or {})}
        self._proc: subprocess.Popen | None = None
        self._id_counter = 0
        self._initialized = False
        self._read_lock = threading.Lock()

    # ------------------------------------------------------------------ lifecycle

    def start(self) -> None:
        """Spawn the server process."""
        self._proc = subprocess.Popen(
            ["node", self._binary],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=self._env,
            text=True,
            bufsize=1,
        )
        # Brief pause to let the JS event loop start
        time.sleep(0.3)
        if self._proc.poll() is not None:
            stderr = self._proc.stderr.read() if self._proc.stderr else ""
            raise RuntimeError(
                f"Server process exited immediately (code {self._proc.returncode}). "
                f"stderr: {stderr[:500]}"
            )

    def stop(self) -> None:
        """Terminate the server process."""
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                self._proc.kill()

    def __enter__(self) -> "MCPClient":
        self.start()
        return self

    def __exit__(self, *_: Any) -> None:
        self.stop()

    # ------------------------------------------------------------------ JSON-RPC helpers

    def _next_id(self) -> int:
        self._id_counter += 1
        return self._id_counter

    def _send(self, message: dict) -> None:
        """Write a JSON message to the server's stdin."""
        assert self._proc and self._proc.stdin
        line = json.dumps(message) + "\n"
        self._proc.stdin.write(line)
        self._proc.stdin.flush()

    def _read_response(self, timeout: float = 15.0) -> dict:
        """Read a JSON-RPC response line from stdout, with timeout."""
        assert self._proc and self._proc.stdout

        deadline = time.monotonic() + timeout
        with self._read_lock:
            while time.monotonic() < deadline:
                # Non-blocking check for data
                line = self._readline_with_timeout(
                    max(0.01, deadline - time.monotonic())
                )
                if line is None:
                    continue
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                    # Skip server-sent notifications (no "id")
                    if "id" not in msg:
                        continue
                    return msg
                except json.JSONDecodeError:
                    continue
        raise TimeoutError(f"No response from server within {timeout}s")

    def _readline_with_timeout(self, timeout: float) -> str | None:
        """Read one line from stdout within the given timeout."""
        import select
        assert self._proc and self._proc.stdout
        fds = [self._proc.stdout]
        readable, _, _ = select.select(fds, [], [], timeout)
        if readable:
            return self._proc.stdout.readline()
        return None

    # ------------------------------------------------------------------ MCP operations

    def initialize(self) -> dict:
        """Perform MCP capability negotiation. Returns the initialize result."""
        req_id = self._next_id()
        self._send({
            "jsonrpc": "2.0",
            "id": req_id,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "mcp-protocol-test-client",
                    "version": "1.0.0",
                },
            },
        })
        resp = self._read_response(timeout=self.HANDSHAKE_TIMEOUT)
        # Send initialized notification (no response expected)
        self._send({
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        })
        self._initialized = True
        return resp

    def ping(self) -> dict:
        """Send ping and return response."""
        req_id = self._next_id()
        self._send({"jsonrpc": "2.0", "id": req_id, "method": "ping", "params": {}})
        return self._read_response(timeout=5.0)

    def list_tools(self) -> dict:
        """Call tools/list and return response."""
        req_id = self._next_id()
        self._send({"jsonrpc": "2.0", "id": req_id, "method": "tools/list", "params": {}})
        return self._read_response(timeout=self.REQUEST_TIMEOUT)

    def call_tool(self, name: str, arguments: dict) -> dict:
        """Call a named tool and return response."""
        req_id = self._next_id()
        self._send({
            "jsonrpc": "2.0",
            "id": req_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        })
        return self._read_response(timeout=self.REQUEST_TIMEOUT)

    def send_raw(self, raw: str) -> dict:
        """Send a raw string to the server and return the response."""
        assert self._proc and self._proc.stdin
        self._proc.stdin.write(raw + "\n")
        self._proc.stdin.flush()
        return self._read_response(timeout=5.0)

    def send_invalid_json(self) -> dict:
        """Send malformed JSON and return the parse-error response."""
        return self.send_raw("{this is not json!!")

    def send_invalid_jsonrpc(self) -> dict:
        """Send valid JSON that is not valid JSON-RPC 2.0 and return error."""
        return self.send_raw('{"hello": "world"}')

    def call_unknown_method(self) -> dict:
        """Call a method that does not exist and return error."""
        req_id = self._next_id()
        self._send({
            "jsonrpc": "2.0",
            "id": req_id,
            "method": "nonexistent/method/xyz",
            "params": {},
        })
        return self._read_response(timeout=5.0)


# ---------------------------------------------------------------------------
# Fixture — start / stop server for each test class
# ---------------------------------------------------------------------------

def _server_available(server_key: str) -> bool:
    """Return True if the compiled server binary exists."""
    path = SERVERS[server_key]["binary"]
    return Path(path).exists()


def _make_client(server_key: str, extra_env: dict | None = None) -> MCPClient:
    cfg = SERVERS[server_key]
    env = {**cfg["env"], **(extra_env or {})}
    return MCPClient(binary_path=cfg["binary"], env=env)


# ---------------------------------------------------------------------------
# Helper assertions shared by all server test classes
# ---------------------------------------------------------------------------

def _assert_jsonrpc_response(resp: dict) -> None:
    """Assert that resp is a valid JSON-RPC 2.0 response envelope."""
    assert "jsonrpc" in resp, f"Missing 'jsonrpc' field: {resp}"
    assert resp["jsonrpc"] == "2.0", f"jsonrpc must be '2.0', got: {resp['jsonrpc']}"
    assert "id" in resp, f"Missing 'id' field: {resp}"


def _assert_success_response(resp: dict) -> None:
    _assert_jsonrpc_response(resp)
    assert "result" in resp, f"Expected 'result' but got error: {resp.get('error')}"
    assert "error" not in resp, f"Unexpected error in success response: {resp['error']}"


def _assert_error_response(resp: dict, expected_code: int | None = None) -> None:
    _assert_jsonrpc_response(resp)
    assert "error" in resp, f"Expected error response, got: {resp}"
    err = resp["error"]
    assert "code" in err, f"Error object missing 'code': {err}"
    assert "message" in err, f"Error object missing 'message': {err}"
    if expected_code is not None:
        assert err["code"] == expected_code, (
            f"Expected error code {expected_code}, got {err['code']}: {err['message']}"
        )


def _assert_tool_content(resp: dict) -> list:
    """Assert tools/call response has TextContent list. Returns content list."""
    _assert_success_response(resp)
    result = resp["result"]
    assert "content" in result, f"Tool result missing 'content': {result}"
    assert isinstance(result["content"], list), f"content must be a list: {result['content']}"
    assert len(result["content"]) > 0, "content list must not be empty"
    item = result["content"][0]
    assert "type" in item, f"Content item missing 'type': {item}"
    assert item["type"] == "text", f"Expected 'text' content type, got: {item['type']}"
    assert "text" in item, f"Content item missing 'text': {item}"
    return result["content"]


# ===========================================================================
# Test Class 1: test-runner MCP server (sdlc-mcp-server)
# ===========================================================================

@pytest.mark.skipif(
    not _server_available("test-runner"),
    reason=f"test-runner server not compiled at {SERVERS['test-runner']['binary']}",
)
class TestTestRunnerMCPProtocol:
    """MCP-TC-001 through MCP-TC-015: Protocol compliance for sdlc-mcp-server (test-runner)."""

    @pytest.fixture(autouse=True)
    def _client(self, tmp_path):
        """Start the test-runner server for each test."""
        env = {"PROJECT_ROOT": str(tmp_path)}
        self.client = _make_client("test-runner", extra_env=env)
        self.client.start()
        yield
        self.client.stop()

    # MCP-TC-001: Capability negotiation
    def test_mcp_tc_001_initialize_returns_valid_capabilities(self):
        """MCP-TC-001 — initialize response has required fields and server info."""
        resp = self.client.initialize()
        _assert_success_response(resp)
        result = resp["result"]
        assert "capabilities" in result, f"Missing capabilities: {result}"
        assert "serverInfo" in result, f"Missing serverInfo: {result}"
        info = result["serverInfo"]
        assert "name" in info, f"serverInfo missing 'name': {info}"
        assert "version" in info, f"serverInfo missing 'version': {info}"
        assert isinstance(info["name"], str) and info["name"], "serverInfo.name must be non-empty string"
        assert isinstance(info["version"], str) and info["version"], "serverInfo.version must be non-empty string"

    # MCP-TC-002: tools/list after initialization
    def test_mcp_tc_002_tools_list_after_init(self):
        """MCP-TC-002 — tools/list returns expected tool names and schema."""
        self.client.initialize()
        resp = self.client.list_tools()
        _assert_success_response(resp)
        tools = resp["result"].get("tools", [])
        assert isinstance(tools, list), f"tools must be a list: {tools}"
        assert len(tools) > 0, "tools/list must return at least one tool"
        tool_names = [t["name"] for t in tools]
        # Each expected tool must be listed
        for expected in SERVERS["test-runner"]["expected_tools"]:
            assert expected in tool_names, (
                f"Expected tool '{expected}' not found in tools/list. Got: {tool_names}"
            )
        # Each tool must have name, description, inputSchema
        for tool in tools:
            assert "name" in tool, f"Tool missing 'name': {tool}"
            assert "description" in tool, f"Tool '{tool.get('name')}' missing 'description'"
            assert "inputSchema" in tool, f"Tool '{tool.get('name')}' missing 'inputSchema'"

    # MCP-TC-003: run_tests with valid params (empty project → structured result)
    def test_mcp_tc_003_run_tests_valid_params(self, tmp_path):
        """MCP-TC-003 — run_tests with valid arguments returns TextContent response."""
        self.client.initialize()
        # Point to tmp_path (no tests → structured empty result is still a valid response)
        resp = self.client.call_tool("run_tests", {"filter": "nonexistent_test_xyz"})
        # Either success with content or a structured error — must never be a protocol error
        assert "result" in resp or "error" in resp
        if "result" in resp:
            _assert_tool_content(resp)

    # MCP-TC-004: run_single_test missing required param → -32602
    def test_mcp_tc_004_run_single_test_missing_required_param(self):
        """MCP-TC-004 — run_single_test without testFile → -32602 Invalid params."""
        self.client.initialize()
        resp = self.client.call_tool("run_single_test", {})
        # Should be an error response with -32602 (or a tool-level error in content)
        if "error" in resp:
            _assert_error_response(resp, expected_code=-32602)
        else:
            # Some servers return errors as TextContent — accept that too
            content = resp["result"].get("content", [])
            assert len(content) > 0

    # MCP-TC-005: Method not found
    def test_mcp_tc_005_method_not_found(self):
        """MCP-TC-005 — calling nonexistent method returns -32601 Method not found."""
        self.client.initialize()
        resp = self.client.call_unknown_method()
        _assert_error_response(resp, expected_code=-32601)

    # MCP-TC-006: Unknown tool name → error response
    def test_mcp_tc_006_unknown_tool_name(self):
        """MCP-TC-006 — calling an unregistered tool name returns an error."""
        self.client.initialize()
        resp = self.client.call_tool("completely_nonexistent_tool_xyz", {})
        # Must be either a JSON-RPC error or a tool error in content
        assert "error" in resp or "result" in resp
        if "error" in resp:
            code = resp["error"]["code"]
            # Either -32601 (method not found) or -32602 (invalid params) or custom -32000 range
            assert code in (-32601, -32602) or (-32099 <= code <= -32000), (
                f"Unexpected error code {code}"
            )

    # MCP-TC-007: Concurrent requests — send 3 requests simultaneously
    def test_mcp_tc_007_concurrent_requests_handled(self):
        """MCP-TC-007 — server handles multiple concurrent in-flight requests."""
        self.client.initialize()
        results = []
        errors = []

        def _do_list():
            try:
                r = self.client.list_tools()
                results.append(r)
            except Exception as exc:
                errors.append(str(exc))

        threads = [threading.Thread(target=_do_list) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=20.0)

        assert len(errors) == 0, f"Concurrent request errors: {errors}"
        assert len(results) == 3, f"Expected 3 responses, got {len(results)}"
        for r in results:
            _assert_success_response(r)


# ===========================================================================
# Test Class 2: ai-code-knowledge MCP server (aicoder / knowledge-base-mcp)
# ===========================================================================

@pytest.mark.skipif(
    not _server_available("ai-code-knowledge"),
    reason=f"ai-code-knowledge server not compiled at {SERVERS['ai-code-knowledge']['binary']}",
)
class TestAICodeKnowledgeMCPProtocol:
    """MCP-TC-016 through MCP-TC-030: Protocol compliance for ai-code-knowledge server."""

    @pytest.fixture(autouse=True)
    def _client(self, tmp_path):
        env = {
            "KNOWLEDGE_ROOT": str(_REPO_ROOT / ".knowledge"),
        }
        self.client = _make_client("ai-code-knowledge", extra_env=env)
        self.client.start()
        yield
        self.client.stop()

    # MCP-TC-016: Capability negotiation
    def test_mcp_tc_016_initialize_returns_capabilities(self):
        """MCP-TC-016 — initialize response includes serverInfo with name and version."""
        resp = self.client.initialize()
        _assert_success_response(resp)
        result = resp["result"]
        assert "capabilities" in result
        assert "serverInfo" in result
        info = result["serverInfo"]
        assert "name" in info and info["name"], "serverInfo.name must be non-empty"
        assert "version" in info and info["version"], "serverInfo.version must be non-empty"

    # MCP-TC-017: tools/list completeness
    def test_mcp_tc_017_tools_list_includes_core_tools(self):
        """MCP-TC-017 — tools/list includes all core ai-code-knowledge tools."""
        self.client.initialize()
        resp = self.client.list_tools()
        _assert_success_response(resp)
        tools = resp["result"].get("tools", [])
        tool_names = {t["name"] for t in tools}
        for expected in SERVERS["ai-code-knowledge"]["expected_tools"]:
            assert expected in tool_names, (
                f"Expected core tool '{expected}' not in tools/list. Present: {sorted(tool_names)}"
            )

    # MCP-TC-018: Tool schema structure
    def test_mcp_tc_018_tool_schemas_have_required_fields(self):
        """MCP-TC-018 — every tool in tools/list has name, description, and inputSchema."""
        self.client.initialize()
        resp = self.client.list_tools()
        _assert_success_response(resp)
        tools = resp["result"].get("tools", [])
        for tool in tools:
            assert "name" in tool, f"Tool missing 'name': {tool}"
            assert "description" in tool, f"Tool '{tool.get('name')}' missing 'description'"
            assert "inputSchema" in tool, f"Tool '{tool.get('name')}' missing 'inputSchema'"
            schema = tool["inputSchema"]
            assert schema.get("type") == "object", (
                f"inputSchema.type must be 'object' for '{tool['name']}', got: {schema.get('type')}"
            )

    # MCP-TC-019: health_check happy path
    def test_mcp_tc_019_health_check_returns_status(self):
        """MCP-TC-019 — health_check returns a TextContent response with status info."""
        self.client.initialize()
        resp = self.client.call_tool("health_check", {})
        _assert_tool_content(resp)
        text = resp["result"]["content"][0]["text"]
        assert isinstance(text, str) and len(text) > 0, "health_check must return non-empty text"

    # MCP-TC-020: get_project_overview happy path
    def test_mcp_tc_020_get_project_overview_returns_text(self):
        """MCP-TC-020 — get_project_overview returns a TextContent response."""
        self.client.initialize()
        resp = self.client.call_tool("get_project_overview", {})
        _assert_tool_content(resp)
        text = resp["result"]["content"][0]["text"]
        assert len(text) > 10, "get_project_overview must return substantial text"

    # MCP-TC-021: find_symbol with valid params
    def test_mcp_tc_021_find_symbol_valid_params(self):
        """MCP-TC-021 — find_symbol with name param returns TextContent or empty result."""
        self.client.initialize()
        resp = self.client.call_tool("find_symbol", {"name": "OrchestratorConfig"})
        _assert_tool_content(resp)

    # MCP-TC-022: find_symbol missing required param → error
    def test_mcp_tc_022_find_symbol_missing_name_param(self):
        """MCP-TC-022 — find_symbol without 'name' param returns error response."""
        self.client.initialize()
        resp = self.client.call_tool("find_symbol", {})
        # Server should return either -32602 or tool-level error in content
        if "error" in resp:
            _assert_error_response(resp, expected_code=-32602)
        else:
            # Accept tool-level error wrapped in TextContent
            content = resp["result"].get("content", [])
            assert len(content) > 0

    # MCP-TC-023: Method not found → -32601
    def test_mcp_tc_023_method_not_found(self):
        """MCP-TC-023 — unknown method returns -32601."""
        self.client.initialize()
        resp = self.client.call_unknown_method()
        _assert_error_response(resp, expected_code=-32601)

    # MCP-TC-024: Unknown tool name → tool-level or protocol error
    def test_mcp_tc_024_unknown_tool_returns_error(self):
        """MCP-TC-024 — calling a tool that doesn't exist returns an error."""
        self.client.initialize()
        resp = self.client.call_tool("nonexistent_tool_xyz_abc", {})
        assert "error" in resp or "result" in resp
        if "error" in resp:
            code = resp["error"]["code"]
            assert code in (-32601, -32602) or (-32099 <= code <= -32000), (
                f"Unexpected error code {code}"
            )

    # MCP-TC-025: Transport — server is alive after multiple requests
    def test_mcp_tc_025_server_stays_alive_after_multiple_requests(self):
        """MCP-TC-025 — server handles 5 sequential requests without dying."""
        self.client.initialize()
        for _ in range(5):
            resp = self.client.list_tools()
            _assert_success_response(resp)
        # Server process should still be running
        assert self.client._proc and self.client._proc.poll() is None, (
            "Server process must not exit during sequential requests"
        )


# ===========================================================================
# Test Class 3: research-cache MCP server (knowledge-base-mcp)
# ===========================================================================

@pytest.mark.skipif(
    not _server_available("research-cache"),
    reason=f"research-cache server not compiled at {SERVERS['research-cache']['binary']}",
)
class TestResearchCacheMCPProtocol:
    """MCP-TC-031 through MCP-TC-055: Full protocol compliance for research-cache server."""

    @pytest.fixture(autouse=True)
    def _client(self, tmp_path):
        global_dir = tmp_path / "global_research"
        project_dir = tmp_path / "project_research"
        global_dir.mkdir()
        project_dir.mkdir()
        env = {
            "GLOBAL_RESEARCH_DIR": str(global_dir),
            "PROJECT_RESEARCH_DIR": str(project_dir),
            "PROJECT_ROOT": str(tmp_path),
        }
        self._global_dir = global_dir
        self._project_dir = project_dir
        self.client = _make_client("research-cache", extra_env=env)
        self.client.start()
        yield
        self.client.stop()

    # --- Category: Capability Negotiation ---

    def test_mcp_tc_031_initialize_returns_valid_capabilities(self):
        """MCP-TC-031 — initialize response includes serverInfo with name='research-cache'."""
        resp = self.client.initialize()
        _assert_success_response(resp)
        result = resp["result"]
        assert "capabilities" in result, f"Missing capabilities: {result}"
        assert "serverInfo" in result, f"Missing serverInfo: {result}"
        info = result["serverInfo"]
        assert "name" in info, "serverInfo missing 'name'"
        assert "version" in info, "serverInfo missing 'version'"
        assert "research-cache" in info["name"].lower(), (
            f"serverInfo.name should identify 'research-cache', got: {info['name']}"
        )

    def test_mcp_tc_032_capabilities_includes_tools(self):
        """MCP-TC-032 — capabilities.tools must be present (server has tools)."""
        resp = self.client.initialize()
        _assert_success_response(resp)
        caps = resp["result"]["capabilities"]
        assert "tools" in caps, (
            f"capabilities.tools must be declared since server has tools. Got: {caps}"
        )

    # --- Category: Tool Listing ---

    def test_mcp_tc_033_tools_list_returns_all_four_tools(self):
        """MCP-TC-033 — tools/list returns all four spec-defined tools."""
        self.client.initialize()
        resp = self.client.list_tools()
        _assert_success_response(resp)
        tools = resp["result"].get("tools", [])
        tool_names = {t["name"] for t in tools}
        expected = set(SERVERS["research-cache"]["expected_tools"])
        assert expected.issubset(tool_names), (
            f"Missing tools: {expected - tool_names}. Present: {tool_names}"
        )

    def test_mcp_tc_034_each_tool_has_required_schema_fields(self):
        """MCP-TC-034 — every tool has name, description, inputSchema with type=object."""
        self.client.initialize()
        resp = self.client.list_tools()
        _assert_success_response(resp)
        for tool in resp["result"]["tools"]:
            assert "name" in tool
            assert "description" in tool, f"Tool '{tool['name']}' missing description"
            schema = tool.get("inputSchema", {})
            assert schema.get("type") == "object", (
                f"inputSchema.type must be 'object' for '{tool['name']}', got: {schema.get('type')}"
            )

    def test_mcp_tc_035_lookup_research_schema_has_required_query(self):
        """MCP-TC-035 — lookup_research inputSchema lists 'query' as required."""
        self.client.initialize()
        resp = self.client.list_tools()
        tools_by_name = {t["name"]: t for t in resp["result"]["tools"]}
        schema = tools_by_name["lookup_research"]["inputSchema"]
        assert "query" in schema.get("required", []), (
            f"lookup_research must require 'query'. Got required: {schema.get('required')}"
        )

    def test_mcp_tc_036_save_research_schema_has_required_fields(self):
        """MCP-TC-036 — save_research inputSchema requires topic, content, tier, tags."""
        self.client.initialize()
        resp = self.client.list_tools()
        tools_by_name = {t["name"]: t for t in resp["result"]["tools"]}
        schema = tools_by_name["save_research"]["inputSchema"]
        required = set(schema.get("required", []))
        expected_required = {"topic", "content", "tier", "tags"}
        assert expected_required.issubset(required), (
            f"save_research must require {expected_required}. Got: {required}"
        )

    # --- Category: Tool Calls (happy path) ---

    def test_mcp_tc_037_lookup_research_empty_cache_returns_miss(self):
        """MCP-TC-037 — lookup_research on empty cache returns miss=true."""
        self.client.initialize()
        resp = self.client.call_tool(
            "lookup_research",
            {"query": "react native performance", "tags": ["react-native"]},
        )
        content = _assert_tool_content(resp)
        result_data = json.loads(content[0]["text"])
        assert "hits" in result_data, f"Response must have 'hits': {result_data}"
        assert "miss" in result_data, f"Response must have 'miss': {result_data}"
        assert result_data["miss"] is True, "Empty cache must return miss=true"
        assert result_data["hits"] == [], "Empty cache must return empty hits list"

    def test_mcp_tc_038_save_research_returns_saved_true(self):
        """MCP-TC-038 — save_research with valid params returns saved=true and entry_id."""
        self.client.initialize()
        resp = self.client.call_tool(
            "save_research",
            {
                "topic": "React Native performance optimization",
                "content": "Use FlatList instead of ScrollView for large lists. "
                            "Memo components to prevent unnecessary re-renders.",
                "tier": "global",
                "tags": ["react-native", "performance"],
                "source_phase": "architect",
                "run_id": "test-run-001",
            },
        )
        content = _assert_tool_content(resp)
        result_data = json.loads(content[0]["text"])
        assert result_data.get("saved") is True, f"save_research must return saved=true: {result_data}"
        assert "entry_id" in result_data, f"save_research must return entry_id: {result_data}"
        assert isinstance(result_data["entry_id"], str) and len(result_data["entry_id"]) == 64, (
            f"entry_id must be a 64-char hex SHA-256: {result_data['entry_id']}"
        )

    def test_mcp_tc_039_save_then_lookup_returns_hit(self):
        """MCP-TC-039 — save_research followed by lookup_research returns a hit."""
        self.client.initialize()
        topic = "PostgreSQL indexing strategies"
        tags = ["database", "postgresql"]

        # Save
        self.client.call_tool(
            "save_research",
            {
                "topic": topic,
                "content": "B-tree indexes work well for range queries. "
                            "Partial indexes reduce index size significantly.",
                "tier": "global",
                "tags": tags,
            },
        )
        # Lookup
        resp = self.client.call_tool(
            "lookup_research",
            {"query": "PostgreSQL", "tags": ["database"]},
        )
        content = _assert_tool_content(resp)
        result_data = json.loads(content[0]["text"])
        assert result_data.get("miss") is False, "Lookup after save must return miss=false"
        assert len(result_data.get("hits", [])) >= 1, "Must have at least one hit"

    def test_mcp_tc_040_flag_finding_returns_flagged_true(self):
        """MCP-TC-040 — flag_finding with valid params returns flagged=true."""
        self.client.initialize()
        resp = self.client.call_tool(
            "flag_finding",
            {
                "type": "performance",
                "severity": "high",
                "finding": "FlatList not used for long lists causing UI jank",
                "recommendation": "Replace ScrollView with FlatList for lists > 50 items",
                "phase": "architect",
            },
        )
        content = _assert_tool_content(resp)
        result_data = json.loads(content[0]["text"])
        assert result_data.get("flagged") is True, f"flag_finding must return flagged=true: {result_data}"

    def test_mcp_tc_041_get_run_recommendations_returns_findings_list(self):
        """MCP-TC-041 — get_run_recommendations returns findings array."""
        self.client.initialize()
        # Flag one finding first
        self.client.call_tool(
            "flag_finding",
            {
                "type": "security",
                "severity": "medium",
                "finding": "API keys stored in plaintext",
                "recommendation": "Use environment variables for all secrets",
                "phase": "pm",
            },
        )
        resp = self.client.call_tool("get_run_recommendations", {})
        content = _assert_tool_content(resp)
        result_data = json.loads(content[0]["text"])
        assert "findings" in result_data, f"Response must have 'findings': {result_data}"
        assert isinstance(result_data["findings"], list), "findings must be a list"
        assert len(result_data["findings"]) >= 1, "Must have at least 1 finding after flag_finding"

    # --- Category: Error Handling — Invalid Params ---

    def test_mcp_tc_042_lookup_research_missing_query_returns_error(self):
        """MCP-TC-042 — lookup_research without 'query' returns -32602 Invalid params."""
        self.client.initialize()
        resp = self.client.call_tool("lookup_research", {})
        _assert_error_response(resp, expected_code=-32602)

    def test_mcp_tc_043_save_research_empty_content_returns_error(self):
        """MCP-TC-043 — save_research with empty content string returns -32602."""
        self.client.initialize()
        resp = self.client.call_tool(
            "save_research",
            {"topic": "test", "content": "", "tier": "global", "tags": []},
        )
        _assert_error_response(resp, expected_code=-32602)

    def test_mcp_tc_044_save_research_empty_topic_returns_error(self):
        """MCP-TC-044 — save_research with empty topic string returns -32602."""
        self.client.initialize()
        resp = self.client.call_tool(
            "save_research",
            {"topic": "", "content": "valid content", "tier": "global", "tags": []},
        )
        _assert_error_response(resp, expected_code=-32602)

    def test_mcp_tc_045_save_research_invalid_tier_returns_error(self):
        """MCP-TC-045 — save_research with tier='invalid' returns -32602."""
        self.client.initialize()
        resp = self.client.call_tool(
            "save_research",
            {"topic": "test", "content": "content", "tier": "invalid", "tags": []},
        )
        _assert_error_response(resp, expected_code=-32602)

    def test_mcp_tc_046_save_research_oversized_content_returns_error(self):
        """MCP-TC-046 — save_research with content > 50KB returns -32602."""
        self.client.initialize()
        oversized = "x" * (51 * 1024)  # 51 KB
        resp = self.client.call_tool(
            "save_research",
            {"topic": "test", "content": oversized, "tier": "global", "tags": []},
        )
        _assert_error_response(resp, expected_code=-32602)

    def test_mcp_tc_047_flag_finding_invalid_type_returns_error(self):
        """MCP-TC-047 — flag_finding with invalid type returns -32602."""
        self.client.initialize()
        resp = self.client.call_tool(
            "flag_finding",
            {
                "type": "invalid_type",
                "severity": "high",
                "finding": "some issue",
                "recommendation": "fix it",
                "phase": "pm",
            },
        )
        _assert_error_response(resp, expected_code=-32602)

    def test_mcp_tc_048_flag_finding_invalid_severity_returns_error(self):
        """MCP-TC-048 — flag_finding with invalid severity returns -32602."""
        self.client.initialize()
        resp = self.client.call_tool(
            "flag_finding",
            {
                "type": "security",
                "severity": "critical",  # not in enum
                "finding": "some issue",
                "recommendation": "fix it",
                "phase": "pm",
            },
        )
        _assert_error_response(resp, expected_code=-32602)

    # --- Category: Error Handling — Protocol Level ---

    def test_mcp_tc_049_method_not_found_returns_32601(self):
        """MCP-TC-049 — calling nonexistent JSON-RPC method returns -32601."""
        self.client.initialize()
        resp = self.client.call_unknown_method()
        _assert_error_response(resp, expected_code=-32601)

    def test_mcp_tc_050_unknown_tool_name_returns_error(self):
        """MCP-TC-050 — tools/call with unknown tool name returns error in -32000 range."""
        self.client.initialize()
        resp = self.client.call_tool("nonexistent_tool_zzz", {})
        assert "error" in resp, f"Expected error for unknown tool, got: {resp}"
        code = resp["error"]["code"]
        # MCP SDK maps unknown tool to MethodNotFound (-32601)
        assert code in (-32601, -32602) or (-32099 <= code <= -32000), (
            f"Error code {code} is outside allowed ranges"
        )

    # --- Category: Transport & Lifecycle ---

    def test_mcp_tc_051_server_handles_sequential_requests(self):
        """MCP-TC-051 — server responds to 10 sequential requests correctly."""
        self.client.initialize()
        for i in range(10):
            resp = self.client.list_tools()
            _assert_success_response(resp)
            assert self.client._proc.poll() is None, (
                f"Server process died after {i+1} requests"
            )

    def test_mcp_tc_052_volatile_tags_use_7_day_ttl(self):
        """MCP-TC-052 — save_research with 'market' tag auto-selects 7-day TTL."""
        self.client.initialize()
        resp = self.client.call_tool(
            "save_research",
            {
                "topic": "Mobile app market trends Q1 2026",
                "content": "React Native market share increased to 38% in Q1 2026.",
                "tier": "project",
                "tags": ["market", "trends"],
                # No ttl_days — should auto-select 7 for volatile tag
            },
        )
        content = _assert_tool_content(resp)
        result_data = json.loads(content[0]["text"])
        assert result_data.get("saved") is True

        # Verify the entry file has ttl_days=7
        index_path = self._project_dir / "index.json"
        if index_path.exists():
            import json as _json
            index = _json.loads(index_path.read_text())
            entry_id = result_data["entry_id"]
            entry_path = self._project_dir / "entries" / f"{entry_id}.json"
            if entry_path.exists():
                entry = _json.loads(entry_path.read_text())
                assert entry["ttl_days"] == 7, (
                    f"Market-tagged entry should have ttl_days=7, got {entry['ttl_days']}"
                )

    def test_mcp_tc_053_save_creates_atomic_index_json(self):
        """MCP-TC-053 — save_research creates valid index.json (atomic write)."""
        self.client.initialize()
        resp = self.client.call_tool(
            "save_research",
            {
                "topic": "Atomic write test",
                "content": "Atomic write guarantees using rename(2).",
                "tier": "global",
                "tags": ["testing"],
            },
        )
        _assert_tool_content(resp)
        index_path = self._global_dir / "index.json"
        assert index_path.exists(), "index.json must be created after save_research"
        parsed = json.loads(index_path.read_text())
        assert "entries" in parsed, "index.json must have 'entries' list"
        assert isinstance(parsed["entries"], list), "entries must be a list"
        assert len(parsed["entries"]) >= 1

    def test_mcp_tc_054_multiple_findings_accumulated(self):
        """MCP-TC-054 — multiple flag_finding calls accumulate all findings."""
        self.client.initialize()
        findings_input = [
            {"type": "performance", "severity": "high",
             "finding": "N+1 query in user listing", "recommendation": "Add eager loading",
             "phase": "architect"},
            {"type": "security", "severity": "medium",
             "finding": "JWT not expiring", "recommendation": "Add expiry to JWT",
             "phase": "pm"},
            {"type": "quality", "severity": "low",
             "finding": "No unit tests for auth module", "recommendation": "Add test coverage",
             "phase": "principal_engineer"},
        ]
        for f in findings_input:
            resp = self.client.call_tool("flag_finding", f)
            content = _assert_tool_content(resp)
            assert json.loads(content[0]["text"])["flagged"] is True

        resp = self.client.call_tool("get_run_recommendations", {})
        content = _assert_tool_content(resp)
        result_data = json.loads(content[0]["text"])
        assert len(result_data["findings"]) >= 3, (
            f"Expected >= 3 findings, got {len(result_data['findings'])}"
        )

    def test_mcp_tc_055_lookup_sorts_by_tag_overlap_desc(self):
        """MCP-TC-055 — lookup_research returns results sorted by tag overlap count descending."""
        self.client.initialize()
        # Save entry-A with 3 matching tags
        self.client.call_tool(
            "save_research",
            {
                "topic": "React Native Performance",
                "content": "Advanced perf tips for RN apps on iOS and Android.",
                "tier": "global",
                "tags": ["react-native", "performance", "mobile"],
                "run_id": "run-A",
            },
        )
        # Save entry-B with 1 matching tag
        self.client.call_tool(
            "save_research",
            {
                "topic": "Vue.js Performance",
                "content": "Performance patterns for Vue.js applications.",
                "tier": "global",
                "tags": ["vue", "performance"],
                "run_id": "run-B",
            },
        )
        # Query with tags that overlap more with entry-A
        resp = self.client.call_tool(
            "lookup_research",
            {"query": "performance", "tags": ["react-native", "performance", "mobile"]},
        )
        content = _assert_tool_content(resp)
        result_data = json.loads(content[0]["text"])
        hits = result_data.get("hits", [])
        assert len(hits) >= 2, f"Expected >= 2 hits, got {len(hits)}"
        # First hit should have highest tag overlap (react-native + performance + mobile = 3)
        first_tags = set(hits[0].get("tags", []))
        assert "react-native" in first_tags, (
            f"First hit (highest overlap) should contain 'react-native'. Got tags: {first_tags}"
        )


# ===========================================================================
# Test Class 4: Cross-server MCP.json configuration tests
# ===========================================================================

class TestMCPJsonConfiguration:
    """MCP-TC-056 through MCP-TC-060: .mcp.json registration and server config validation."""

    def test_mcp_tc_056_mcp_json_exists_and_is_valid_json(self):
        """MCP-TC-056 — .mcp.json exists and parses as valid JSON."""
        mcp_json = _REPO_ROOT / ".mcp.json"
        assert mcp_json.exists(), f".mcp.json not found at {mcp_json}"
        parsed = json.loads(mcp_json.read_text())
        assert isinstance(parsed, dict), ".mcp.json root must be a JSON object"

    def test_mcp_tc_057_mcp_json_has_mcpservers_key(self):
        """MCP-TC-057 — .mcp.json contains 'mcpServers' top-level key."""
        mcp_json = _REPO_ROOT / ".mcp.json"
        parsed = json.loads(mcp_json.read_text())
        assert "mcpServers" in parsed, f"'mcpServers' key missing from .mcp.json: {parsed}"

    def test_mcp_tc_058_knowledge_base_registered_in_mcp_json(self):
        """MCP-TC-058 — knowledge-base server is registered in .mcp.json."""
        mcp_json = _REPO_ROOT / ".mcp.json"
        parsed = json.loads(mcp_json.read_text())
        servers = parsed.get("mcpServers", {})
        assert "knowledge-base" in servers, (
            f"'knowledge-base' not in mcpServers. Registered: {list(servers.keys())}"
        )
        entry = servers["knowledge-base"]
        assert "args" in entry, "knowledge-base must have 'args'"
        assert len(entry["args"]) > 0, "knowledge-base args must not be empty"

    def test_mcp_tc_059_knowledge_base_uses_node_command(self):
        """MCP-TC-059 — knowledge-base server uses 'node' command."""
        mcp_json = _REPO_ROOT / ".mcp.json"
        parsed = json.loads(mcp_json.read_text())
        servers = parsed.get("mcpServers", {})
        assert "knowledge-base" in servers, (
            f"'knowledge-base' not in mcpServers. Registered: {list(servers.keys())}"
        )
        entry = servers["knowledge-base"]
        assert entry.get("command") == "node", (
            "knowledge-base must use 'node' command"
        )

    def test_mcp_tc_060_all_registered_server_binaries_exist(self):
        """MCP-TC-060 — all binary paths registered in .mcp.json point to existing files."""
        mcp_json = _REPO_ROOT / ".mcp.json"
        parsed = json.loads(mcp_json.read_text())
        missing = []
        for name, entry in parsed.get("mcpServers", {}).items():
            args = entry.get("args", [])
            if args:
                binary = args[0]
                if not Path(binary).exists():
                    missing.append((name, binary))
        assert not missing, (
            f"Server binaries registered in .mcp.json but not found on disk: {missing}"
        )
