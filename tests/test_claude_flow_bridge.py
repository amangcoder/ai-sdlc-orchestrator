"""Tests for the claude-flow MCP bridge module."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from orchestrator.claude_flow_bridge import (
    ROLE_TOOL_FILTER,
    _ALLOWED_TOOLS,
    _DEFAULT_TOOLS,
    build_claude_flow_prompt_section,
    find_claude_flow_root,
    get_claude_flow_mcp_config,
    get_tools_for_role,
)


# ---------------------------------------------------------------------------
# find_claude_flow_root
# ---------------------------------------------------------------------------


class TestFindClaudeFlowRoot:
    def test_returns_first_matching_path(self, tmp_path: Path) -> None:
        root = tmp_path / "ruflo"
        server_entry = root / "v3" / "mcp" / "server-entry.ts"
        server_entry.parent.mkdir(parents=True)
        server_entry.touch()

        with patch(
            "orchestrator.claude_flow_bridge._SEARCH_PATHS", [root]
        ):
            result = find_claude_flow_root()
        assert result == root

    def test_returns_none_when_no_paths_exist(self) -> None:
        with patch(
            "orchestrator.claude_flow_bridge._SEARCH_PATHS",
            [Path("/nonexistent/a"), Path("/nonexistent/b")],
        ):
            assert find_claude_flow_root() is None

    def test_respects_path_order(self, tmp_path: Path) -> None:
        first = tmp_path / "first"
        second = tmp_path / "second"
        for d in (first, second):
            entry = d / "v3" / "mcp" / "server-entry.ts"
            entry.parent.mkdir(parents=True)
            entry.touch()

        with patch(
            "orchestrator.claude_flow_bridge._SEARCH_PATHS", [first, second]
        ):
            assert find_claude_flow_root() == first


# ---------------------------------------------------------------------------
# get_claude_flow_mcp_config
# ---------------------------------------------------------------------------


class TestGetClaudeFlowMcpConfig:
    def _make_ruflo(self, tmp_path: Path) -> Path:
        """Create a minimal ruflo directory with server-entry.ts."""
        root = tmp_path / "ruflo"
        entry = root / "v3" / "mcp" / "server-entry.ts"
        entry.parent.mkdir(parents=True)
        entry.touch()
        return root

    def test_returns_none_when_root_not_found(self) -> None:
        with patch(
            "orchestrator.claude_flow_bridge.find_claude_flow_root", return_value=None
        ):
            assert get_claude_flow_mcp_config() is None

    def test_returns_none_when_server_entry_missing(self, tmp_path: Path) -> None:
        root = tmp_path / "ruflo"
        root.mkdir()
        # No server-entry.ts created
        result = get_claude_flow_mcp_config(ruflo_path=str(root))
        assert result is None

    def test_returns_none_when_npx_missing(self, tmp_path: Path) -> None:
        root = self._make_ruflo(tmp_path)
        with patch("orchestrator.claude_flow_bridge._check_tsx_available", return_value=False):
            assert get_claude_flow_mcp_config(ruflo_path=str(root)) is None

    def test_returns_valid_config_with_all_tools(self, tmp_path: Path) -> None:
        root = self._make_ruflo(tmp_path)
        with patch("orchestrator.claude_flow_bridge._check_tsx_available", return_value=True):
            result = get_claude_flow_mcp_config(ruflo_path=str(root))

        assert result is not None
        assert "claude-flow" in result
        args = result["claude-flow"]["args"]
        assert "--tools" in args
        tools_csv = args[args.index("--tools") + 1]
        for tool in _ALLOWED_TOOLS:
            assert tool in tools_csv

    def test_filters_memory_tools_when_disabled(self, tmp_path: Path) -> None:
        root = self._make_ruflo(tmp_path)
        tools_config = SimpleNamespace(memory=False, session=False, tasks=True)
        with patch("orchestrator.claude_flow_bridge._check_tsx_available", return_value=True):
            result = get_claude_flow_mcp_config(
                ruflo_path=str(root), tools_config=tools_config
            )

        assert result is not None
        tools_csv = result["claude-flow"]["args"][-1]
        assert "memory/" not in tools_csv
        assert "tasks/create" in tools_csv

    def test_filters_task_tools_when_disabled(self, tmp_path: Path) -> None:
        root = self._make_ruflo(tmp_path)
        tools_config = SimpleNamespace(memory=True, session=False, tasks=False)
        with patch("orchestrator.claude_flow_bridge._check_tsx_available", return_value=True):
            result = get_claude_flow_mcp_config(
                ruflo_path=str(root), tools_config=tools_config
            )

        assert result is not None
        tools_csv = result["claude-flow"]["args"][-1]
        assert "memory/store" in tools_csv
        assert "tasks/" not in tools_csv

    def test_returns_none_when_all_categories_disabled(self, tmp_path: Path) -> None:
        root = self._make_ruflo(tmp_path)
        tools_config = SimpleNamespace(memory=False, session=False, tasks=False)
        with patch("orchestrator.claude_flow_bridge._check_tsx_available", return_value=True):
            result = get_claude_flow_mcp_config(
                ruflo_path=str(root), tools_config=tools_config
            )
        assert result is None

    def test_explicit_ruflo_path_overrides_search(self, tmp_path: Path) -> None:
        root = self._make_ruflo(tmp_path)
        with patch("orchestrator.claude_flow_bridge._check_tsx_available", return_value=True):
            result = get_claude_flow_mcp_config(ruflo_path=str(root))

        assert result is not None
        server_path = result["claude-flow"]["args"][1]
        assert str(root) in server_path


# ---------------------------------------------------------------------------
# get_tools_for_role
# ---------------------------------------------------------------------------


class TestGetToolsForRole:
    @pytest.mark.parametrize(
        "role,expected_subset",
        [
            ("product_manager", ["memory/store", "memory/search", "memory/list"]),
            ("backend_engineer", ["memory/store", "tasks/create", "tasks/update"]),
            ("security_engineer", ["memory/search", "tasks/list"]),
        ],
    )
    def test_known_role_returns_expected_tools(
        self, role: str, expected_subset: list[str]
    ) -> None:
        tools = get_tools_for_role(role)
        for tool in expected_subset:
            assert tool in tools

    def test_unknown_role_returns_defaults(self) -> None:
        assert get_tools_for_role("unknown_role") == _DEFAULT_TOOLS

    def test_default_tools_are_read_only(self) -> None:
        assert set(_DEFAULT_TOOLS) == {"memory/search", "memory/list"}


# ---------------------------------------------------------------------------
# build_claude_flow_prompt_section
# ---------------------------------------------------------------------------


class TestBuildClaudeFlowPromptSection:
    def test_memory_only_role(self) -> None:
        # product_manager has only memory tools
        section = build_claude_flow_prompt_section("product_manager")
        assert "memory_store" in section
        assert "memory_search" in section
        assert "memory_list" in section
        assert "tasks_create" not in section

    def test_combined_memory_and_tasks(self) -> None:
        # backend_engineer has both memory and task tools
        section = build_claude_flow_prompt_section("backend_engineer")
        assert "memory_store" in section
        assert "tasks_create" in section
        assert "tasks_update" in section
        assert "shared task coordination" in section

    def test_empty_tools_returns_empty_string(self) -> None:
        with patch.dict(ROLE_TOOL_FILTER, {"empty_role": []}):
            with patch(
                "orchestrator.claude_flow_bridge._DEFAULT_TOOLS", []
            ):
                assert build_claude_flow_prompt_section("empty_role") == ""

    def test_prompt_section_starts_with_header(self) -> None:
        section = build_claude_flow_prompt_section("product_manager")
        assert section.startswith("## Cross-Agent Coordination Tools")

    def test_includes_tasks_list_instruction(self) -> None:
        # qa_planner has tasks/list
        section = build_claude_flow_prompt_section("qa_planner")
        assert "tasks_list" in section
        assert "full task board" in section

    def test_includes_tasks_update_instruction(self) -> None:
        # backend_engineer has tasks/update
        section = build_claude_flow_prompt_section("backend_engineer")
        assert "tasks_update" in section

    def test_includes_tasks_results_instruction(self) -> None:
        # qa_executor has tasks/results
        section = build_claude_flow_prompt_section("qa_executor")
        assert "tasks_results" in section

    def test_no_session_tools_in_any_role(self) -> None:
        for role in ROLE_TOOL_FILTER:
            tools = get_tools_for_role(role)
            assert not any(t.startswith("session/") for t in tools), (
                f"Role {role} should not have session tools"
            )


# ---------------------------------------------------------------------------
# _ALLOWED_TOOLS consistency
# ---------------------------------------------------------------------------


class TestAllowedToolsConsistency:
    def test_no_session_tools_in_allowed_list(self) -> None:
        session_tools = [t for t in _ALLOWED_TOOLS if t.startswith("session/")]
        assert session_tools == [], (
            f"Session tools should be removed from _ALLOWED_TOOLS: {session_tools}"
        )

    def test_all_role_tools_are_in_allowed_list(self) -> None:
        for role, tools in ROLE_TOOL_FILTER.items():
            for tool in tools:
                assert tool in _ALLOWED_TOOLS, (
                    f"Role {role} references tool '{tool}' not in _ALLOWED_TOOLS"
                )
