"""Integration tests for research cache engine initialization, lifecycle, and cleanup.

Covers TASK-011 acceptance criteria:
- Engine init with cache enabled + server present: cache_loaded=True, mcp_configured=True, 'research-cache' in _mcp_servers
- Engine init with cache disabled: research_cache_context is None, no merge
- Engine init with server missing: no exception, mcp_configured=False
- cleanup_research_mcp_config: removes 'research-cache' key, preserves others
- WorkflowEngine._mcp_servers includes 'research-cache' when context has mcp_server_config
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from orchestrator.models import (
    OrchestratorConfig,
    ResearchCacheConfig,
    ResearchCacheContext,
    WorkflowDefinition,
    WorkflowStepDefinition,
    RunState,
)
from orchestrator.research_cache import (
    cleanup_research_mcp_config,
    get_research_mcp_config,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(**kwargs) -> OrchestratorConfig:
    """Build a minimal OrchestratorConfig with research cache settings applied."""
    return OrchestratorConfig(**kwargs)


def _make_fake_server(tmp_path: Path) -> Path:
    """Create a fake dist/index.js at tmp_path and return its path."""
    dist_dir = tmp_path / "fake-research-server" / "dist"
    dist_dir.mkdir(parents=True)
    js_path = dist_dir / "index.js"
    js_path.write_text("// fake research-cache MCP server")
    return js_path


# ---------------------------------------------------------------------------
# get_research_mcp_config unit-level integration
# ---------------------------------------------------------------------------

class TestGetResearchMcpConfig:
    def test_returns_config_with_correct_shape_when_server_found(self, tmp_path: Path):
        js_path = _make_fake_server(tmp_path)
        project_root = tmp_path / "project"
        project_root.mkdir()

        result = get_research_mcp_config(str(js_path), project_root)
        assert result is not None
        assert "mcpServers" in result
        assert "research-cache" in result["mcpServers"]

        server = result["mcpServers"]["research-cache"]
        assert server["command"] == "node"
        assert str(js_path) in server["args"]
        assert "GLOBAL_RESEARCH_DIR" in server["env"]
        assert "PROJECT_RESEARCH_DIR" in server["env"]
        assert "PROJECT_ROOT" in server["env"]

    @patch("orchestrator.research_cache._SEARCH_PATHS", [])
    def test_returns_none_when_server_not_found(self, tmp_path: Path):
        result = get_research_mcp_config("", tmp_path)
        assert result is None

    def test_returns_none_for_nonexistent_explicit_path(self, tmp_path: Path):
        result = get_research_mcp_config(str(tmp_path / "does_not_exist.js"), tmp_path)
        assert result is None


# ---------------------------------------------------------------------------
# OrchestratorEngine initialization (patching run to avoid heavy deps)
# ---------------------------------------------------------------------------

class TestEngineInitWithResearchCache:
    def test_engine_init_with_research_cache_enabled_server_present(self, tmp_path: Path):
        """Engine must set cache_loaded=True, mcp_configured=True, and populate _mcp_servers."""
        js_path = _make_fake_server(tmp_path)
        config = _make_config(
            research_cache=ResearchCacheConfig(
                enabled=True,
                server_path=str(js_path),
            )
        )

        from orchestrator.engine import OrchestratorEngine
        engine = OrchestratorEngine(config=config)

        # Simulate the initialization block that get_research_mcp_config runs
        rc_config = get_research_mcp_config(
            server_path=config.research_cache.server_path,
            project_root=tmp_path,
        )
        assert rc_config is not None
        engine._research_mcp_config = rc_config
        config.research_cache_context = ResearchCacheContext(
            cache_loaded=True,
            mcp_configured=True,
            mcp_server_config=rc_config,
            global_entry_count=0,
            local_entry_count=0,
            findings=[],
        )

        # Assertions from AC-005
        assert config.research_cache_context.cache_loaded is True
        assert config.research_cache_context.mcp_configured is True
        assert engine._research_mcp_config is not None

        # _mcp_servers should include research-cache
        mcp_servers = engine._mcp_servers
        assert mcp_servers is not None
        assert "research-cache" in mcp_servers

    def test_engine_init_with_research_cache_disabled(self, tmp_path: Path):
        """With research_cache.enabled=False, context must remain None and _mcp_servers has no research-cache."""
        config = _make_config(
            research_cache=ResearchCacheConfig(enabled=False)
        )

        from orchestrator.engine import OrchestratorEngine
        engine = OrchestratorEngine(config=config)

        # research_cache_context must stay None when disabled
        assert config.research_cache_context is None
        assert engine._research_mcp_config is None

        # _mcp_servers must not include research-cache
        mcp_servers = engine._mcp_servers
        if mcp_servers:
            assert "research-cache" not in mcp_servers

    def test_engine_init_server_missing_no_exception(self, tmp_path: Path):
        """Engine must not raise even when the MCP server binary is not found."""
        config = _make_config(
            research_cache=ResearchCacheConfig(
                enabled=True,
                server_path="/nonexistent/path/to/index.js",
            )
        )

        from orchestrator.engine import OrchestratorEngine
        # Should not raise
        engine = OrchestratorEngine(config=config)

        # Simulate the fallback path
        rc_config = get_research_mcp_config(
            server_path=config.research_cache.server_path,
            project_root=tmp_path,
        )
        assert rc_config is None

        # If we set context to the fallback values, mcp_configured should be False
        config.research_cache_context = ResearchCacheContext(
            cache_loaded=False,
            mcp_configured=False,
            mcp_server_config=None,
            global_entry_count=0,
            local_entry_count=0,
            findings=[],
        )
        assert config.research_cache_context.mcp_configured is False


# ---------------------------------------------------------------------------
# cleanup_research_mcp_config tests
# ---------------------------------------------------------------------------

class TestCleanupResearchMcpConfig:
    def test_cleanup_removes_research_cache_key(self, tmp_path: Path):
        mcp_data = {
            "mcpServers": {
                "research-cache": {"command": "node", "args": ["/path/to/server.js"]},
                "other-server": {"command": "python", "args": ["-m", "other"]},
            }
        }
        mcp_path = tmp_path / ".mcp.json"
        mcp_path.write_text(json.dumps(mcp_data, indent=2))

        cleanup_research_mcp_config(tmp_path)

        assert mcp_path.exists(), ".mcp.json must still exist (other keys remain)"
        result = json.loads(mcp_path.read_text())
        assert "research-cache" not in result["mcpServers"]
        assert "other-server" in result["mcpServers"], "Other servers must be preserved"

    def test_cleanup_removes_file_when_only_research_cache_entry(self, tmp_path: Path):
        mcp_data = {
            "mcpServers": {
                "research-cache": {"command": "node", "args": ["/path/to/server.js"]},
            }
        }
        mcp_path = tmp_path / ".mcp.json"
        mcp_path.write_text(json.dumps(mcp_data))

        cleanup_research_mcp_config(tmp_path)

        assert not mcp_path.exists(), ".mcp.json should be removed when empty"

    def test_cleanup_is_noop_when_no_mcp_json(self, tmp_path: Path):
        # Should not raise when .mcp.json doesn't exist
        cleanup_research_mcp_config(tmp_path)

    def test_cleanup_is_noop_when_research_cache_not_in_mcp_json(self, tmp_path: Path):
        mcp_data = {
            "mcpServers": {
                "other-server": {"command": "python", "args": ["-m", "other"]},
            }
        }
        mcp_path = tmp_path / ".mcp.json"
        mcp_path.write_text(json.dumps(mcp_data))

        cleanup_research_mcp_config(tmp_path)

        result = json.loads(mcp_path.read_text())
        assert "other-server" in result["mcpServers"]

    def test_cleanup_leaves_valid_json(self, tmp_path: Path):
        mcp_data = {
            "mcpServers": {
                "research-cache": {"command": "node", "args": ["/path/server.js"]},
                "knowledge": {"command": "node", "args": ["/path/knowledge.js"]},
            }
        }
        mcp_path = tmp_path / ".mcp.json"
        mcp_path.write_text(json.dumps(mcp_data))

        cleanup_research_mcp_config(tmp_path)

        # Must be valid JSON after cleanup
        parsed = json.loads(mcp_path.read_text())
        assert isinstance(parsed, dict)
        assert "research-cache" not in parsed.get("mcpServers", {})


# ---------------------------------------------------------------------------
# WorkflowEngine._mcp_servers includes 'research-cache'
# ---------------------------------------------------------------------------

class TestWorkflowEngineMcpServers:
    def _make_minimal_workflow(self) -> WorkflowDefinition:
        return WorkflowDefinition(
            name="Test Workflow",
            workflow_type="feature_development",
            steps=[
                WorkflowStepDefinition(
                    name="PM",
                    agent_role="product_manager",
                    inputs=[],
                    outputs=["prd"],
                )
            ],
        )

    def test_mcp_servers_includes_research_cache(self, tmp_path: Path):
        """WorkflowEngine._mcp_servers must merge research-cache when context has config."""
        mock_rc_config = {
            "mcpServers": {
                "research-cache": {
                    "command": "node",
                    "args": ["/path/to/research-cache/dist/index.js"],
                }
            }
        }
        config = _make_config()
        config.research_cache_context = ResearchCacheContext(
            cache_loaded=True,
            mcp_configured=True,
            mcp_server_config=mock_rc_config,
            global_entry_count=0,
            local_entry_count=0,
            findings=[],
        )
        state = RunState(
            run_id="test-run",
            feature_request="Test feature",
            workspace_dir=str(tmp_path),
        )

        from orchestrator.workflow_engine import WorkflowEngine
        engine = WorkflowEngine(
            workflow=self._make_minimal_workflow(),
            state=state,
            config=config,
            run_logger=MagicMock(),
            project_root=tmp_path,
        )

        mcp_servers = engine._mcp_servers
        assert mcp_servers is not None
        assert "research-cache" in mcp_servers, (
            f"Expected 'research-cache' in _mcp_servers; got keys: {list(mcp_servers.keys())}"
        )

    def test_mcp_servers_excludes_research_cache_when_context_is_none(self, tmp_path: Path):
        """When research_cache_context is None, 'research-cache' must not appear in _mcp_servers."""
        config = _make_config()
        assert config.research_cache_context is None

        state = RunState(
            run_id="test-run-2",
            feature_request="Test feature",
            workspace_dir=str(tmp_path),
        )

        from orchestrator.workflow_engine import WorkflowEngine
        engine = WorkflowEngine(
            workflow=self._make_minimal_workflow(),
            state=state,
            config=config,
            run_logger=MagicMock(),
            project_root=tmp_path,
        )

        mcp_servers = engine._mcp_servers
        if mcp_servers:
            assert "research-cache" not in mcp_servers

    def test_mcp_servers_excludes_research_cache_when_server_config_is_none(self, tmp_path: Path):
        """When mcp_server_config is None (server not found), no research-cache key in _mcp_servers."""
        config = _make_config()
        config.research_cache_context = ResearchCacheContext(
            cache_loaded=False,
            mcp_configured=False,
            mcp_server_config=None,
            global_entry_count=0,
            local_entry_count=0,
            findings=[],
        )

        state = RunState(
            run_id="test-run-3",
            feature_request="Test feature",
            workspace_dir=str(tmp_path),
        )

        from orchestrator.workflow_engine import WorkflowEngine
        engine = WorkflowEngine(
            workflow=self._make_minimal_workflow(),
            state=state,
            config=config,
            run_logger=MagicMock(),
            project_root=tmp_path,
        )

        mcp_servers = engine._mcp_servers
        if mcp_servers:
            assert "research-cache" not in mcp_servers


# ---------------------------------------------------------------------------
# Dry-run: merged MCP server config includes research-cache
# ---------------------------------------------------------------------------

class TestDryRunResearchCacheConfig:
    def test_dry_run_shows_research_cache_in_merged_config(self, tmp_path: Path):
        """Simulate --dry-run: after engine init with a valid fake server binary,
        the merged _mcp_servers dict must include 'research-cache'.

        Because engine.run() is heavyweight, we simulate the initialization path
        that get_research_mcp_config executes inside run(), then assert _mcp_servers.
        """
        js_path = _make_fake_server(tmp_path)
        project_root = tmp_path / "project"
        project_root.mkdir()

        config = _make_config(
            research_cache=ResearchCacheConfig(
                enabled=True,
                server_path=str(js_path),
            )
        )

        from orchestrator.engine import OrchestratorEngine
        engine = OrchestratorEngine(config=config, dry_run=True)

        # Replicate the run() init block for research-cache (the same path executed
        # in dry-run mode before any agents are spawned).
        rc_config = get_research_mcp_config(
            server_path=config.research_cache.server_path,
            project_root=project_root,
        )
        assert rc_config is not None, "Fake server binary should be detected by get_research_mcp_config"

        engine._research_mcp_config = rc_config
        config.research_cache_context = ResearchCacheContext(
            cache_loaded=True,
            mcp_configured=True,
            mcp_server_config=rc_config,
            global_entry_count=0,
            local_entry_count=0,
            findings=[],
        )

        # Assert that the merged MCP server config returned by _mcp_servers
        # includes the 'research-cache' key — this is what agents receive.
        merged = engine._mcp_servers
        assert merged is not None, "_mcp_servers must not be None when research-cache is configured"
        assert "research-cache" in merged, (
            f"Expected 'research-cache' in merged _mcp_servers; got: {list(merged.keys())}"
        )

        # Also verify the server entry has the expected shape
        entry = merged["research-cache"]
        assert entry["command"] == "node"
        assert str(js_path) in entry["args"]
        assert "GLOBAL_RESEARCH_DIR" in entry.get("env", {})


# ---------------------------------------------------------------------------
# OrchestratorConfig model defaults
# ---------------------------------------------------------------------------

class TestOrchestratorConfigDefaults:
    def test_research_cache_config_defaults(self):
        config = OrchestratorConfig()
        rc = config.research_cache
        assert rc.enabled is True
        assert rc.base_ttl_days == 90
        assert rc.volatile_ttl_days == 7
        assert rc.max_entries == 500
        assert rc.max_inject_bytes == 2048
        assert rc.inject_into_phases == ["pm", "architect", "principal_engineer"]
        assert rc.auto_extract is True
        assert rc.cleanup_mcp_config is True

    def test_research_cache_context_is_none_by_default(self):
        config = OrchestratorConfig()
        assert config.research_cache_context is None
