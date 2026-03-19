"""Tests for AgentInvocation Pydantic model validation (TASK-010 / DEBT-016).

Covers:
- Field validation for agent_name, prompt, max_turns
- Path traversal protection at the model level
- Defense-in-depth check in invoke_agent()
- AgentResult remains a plain dataclass
- All existing construction patterns continue to work
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel, ValidationError

from orchestrator.agents import (
    AGENTS_DIR,
    AgentInvocation,
    AgentResult,
    invoke_agent,
)
from orchestrator.models import ModelTier


# ---------------------------------------------------------------------------
# AgentInvocation is a Pydantic BaseModel
# ---------------------------------------------------------------------------

class TestAgentInvocationIsBaseModel:
    def test_is_pydantic_basemodel(self):
        assert issubclass(AgentInvocation, BaseModel)

    def test_extra_fields_forbidden(self):
        """model_config = ConfigDict(extra='forbid') must reject unknown fields."""
        with pytest.raises(ValidationError):
            AgentInvocation(
                agent_name="pm",
                prompt="hello",
                unexpected_field="should fail",
            )

    def test_agent_result_remains_dataclass(self):
        """AgentResult must stay a plain dataclass (return-only)."""
        assert dataclasses.is_dataclass(AgentResult)
        # It must NOT be a Pydantic model
        assert not issubclass(AgentResult, BaseModel)


# ---------------------------------------------------------------------------
# agent_name validation
# ---------------------------------------------------------------------------

class TestAgentNameValidation:
    def test_valid_simple_name(self):
        inv = AgentInvocation(agent_name="pm", prompt="task")
        assert inv.agent_name == "pm"

    def test_valid_underscore_name(self):
        inv = AgentInvocation(agent_name="backend_engineer", prompt="task")
        assert inv.agent_name == "backend_engineer"

    def test_valid_hyphen_name(self):
        inv = AgentInvocation(agent_name="deep-researcher", prompt="task")
        assert inv.agent_name == "deep-researcher"

    def test_valid_mixed_case_with_digits(self):
        inv = AgentInvocation(agent_name="agent2_helper", prompt="task")
        assert inv.agent_name == "agent2_helper"

    def test_empty_agent_name_raises(self):
        """Empty agent_name must raise ValidationError at construction time."""
        with pytest.raises(ValidationError) as exc_info:
            AgentInvocation(agent_name="", prompt="task")
        errors = exc_info.value.errors()
        assert any("agent_name" in str(e) for e in errors)

    def test_path_traversal_dotdot_slash_raises(self):
        """'../secret' contains path traversal chars — must raise ValidationError."""
        with pytest.raises(ValidationError):
            AgentInvocation(agent_name="../secret", prompt="task")

    def test_path_traversal_dotdot_backslash_raises(self):
        with pytest.raises(ValidationError):
            AgentInvocation(agent_name="..\\windows\\secret", prompt="task")

    def test_slash_in_name_raises(self):
        with pytest.raises(ValidationError):
            AgentInvocation(agent_name="subdir/agent", prompt="task")

    def test_dot_in_name_raises(self):
        """A name like 'agent.name' contains a dot — must raise ValidationError."""
        with pytest.raises(ValidationError):
            AgentInvocation(agent_name="agent.name", prompt="task")

    def test_space_in_name_raises(self):
        with pytest.raises(ValidationError):
            AgentInvocation(agent_name="my agent", prompt="task")

    def test_null_byte_raises(self):
        with pytest.raises(ValidationError):
            AgentInvocation(agent_name="pm\x00evil", prompt="task")


# ---------------------------------------------------------------------------
# prompt validation
# ---------------------------------------------------------------------------

class TestPromptValidation:
    def test_valid_prompt(self):
        inv = AgentInvocation(agent_name="pm", prompt="Build a todo app")
        assert inv.prompt == "Build a todo app"

    def test_empty_prompt_raises(self):
        """Empty prompt must raise ValidationError at construction time."""
        with pytest.raises(ValidationError) as exc_info:
            AgentInvocation(agent_name="pm", prompt="")
        errors = exc_info.value.errors()
        assert any("prompt" in str(e) for e in errors)


# ---------------------------------------------------------------------------
# max_turns validation
# ---------------------------------------------------------------------------

class TestMaxTurnsValidation:
    def test_default_is_40(self):
        inv = AgentInvocation(agent_name="pm", prompt="task")
        assert inv.max_turns == 40

    def test_valid_max_turns_1(self):
        inv = AgentInvocation(agent_name="pm", prompt="task", max_turns=1)
        assert inv.max_turns == 1

    def test_valid_max_turns_200(self):
        inv = AgentInvocation(agent_name="pm", prompt="task", max_turns=200)
        assert inv.max_turns == 200

    def test_zero_max_turns_raises(self):
        """max_turns=0 must raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            AgentInvocation(agent_name="pm", prompt="task", max_turns=0)
        errors = exc_info.value.errors()
        assert any("max_turns" in str(e) for e in errors)

    def test_negative_max_turns_raises(self):
        """max_turns=-1 must raise ValidationError."""
        with pytest.raises(ValidationError):
            AgentInvocation(agent_name="pm", prompt="task", max_turns=-1)

    def test_large_max_turns_raises(self):
        """max_turns=201 exceeds the cap and must raise ValidationError."""
        with pytest.raises(ValidationError):
            AgentInvocation(agent_name="pm", prompt="task", max_turns=201)


# ---------------------------------------------------------------------------
# Optional fields and defaults
# ---------------------------------------------------------------------------

class TestOptionalFields:
    def test_defaults(self):
        inv = AgentInvocation(agent_name="pm", prompt="task")
        assert inv.model == ModelTier.SONNET
        assert inv.workspace_dir is None
        assert inv.project_root is None
        assert inv.isolation is None
        assert inv.display_name is None
        assert inv.mcp_servers is None

    def test_all_fields(self):
        inv = AgentInvocation(
            agent_name="backend_engineer",
            prompt="Implement the API",
            model=ModelTier.HAIKU,
            max_turns=10,
            workspace_dir="/tmp/ws",
            project_root="/tmp/proj",
            isolation="worktree",
            display_name="Orion (spawned:backend_engineer)",
            mcp_servers={"my_server": {"command": "python", "args": []}},
        )
        assert inv.agent_name == "backend_engineer"
        assert inv.model == ModelTier.HAIKU
        assert inv.max_turns == 10
        assert inv.isolation == "worktree"

    def test_display_name_allows_colons_and_parens(self):
        """display_name is free-form — not subject to agent_name pattern."""
        inv = AgentInvocation(
            agent_name="pm",
            prompt="task",
            display_name="Orion (spawned:product_manager)",
        )
        assert inv.display_name == "Orion (spawned:product_manager)"

    def test_mutation_is_allowed(self):
        """Pydantic model (not frozen) must allow attribute mutation (needed by invoke_agent worktree)."""
        inv = AgentInvocation(agent_name="pm", prompt="task", project_root="/old")
        inv.project_root = "/new"
        assert inv.project_root == "/new"


# ---------------------------------------------------------------------------
# Known good agent names from the codebase (regression: all should succeed)
# ---------------------------------------------------------------------------

class TestKnownGoodAgentNames:
    """Verify that all agent_name values currently used across the codebase
    remain valid after the Pydantic conversion."""

    _KNOWN_NAMES = [
        "pm",
        "architect",
        "principal_engineer",
        "tpm",
        "engineer",
        "frontend_engineer",
        "backend_engineer",
        "database_engineer",
        "caching_engineer",
        "backend_reviewer",
        "frontend_reviewer",
        "qa_planner",
        "qa_executor",
        "automation_engineer",
        "devops_engineer",
        "security_engineer",
        "observability_engineer",
        "documentation_engineer",
        "git_manager",
        "api_contract_designer",
        "migration_engineer",
        "ux_specifier",
        "tech_debt_assessor",
        "release_engineer",
        "incident_analyst",
        "deep_researcher",
        "brainstormer",
        "mediator",
        "injected_task",
    ]

    @pytest.mark.parametrize("name", _KNOWN_NAMES)
    def test_known_name_valid(self, name: str):
        inv = AgentInvocation(agent_name=name, prompt="task")
        assert inv.agent_name == name


# ---------------------------------------------------------------------------
# Defense-in-depth: invoke_agent() path traversal check
# ---------------------------------------------------------------------------

class TestInvokeAgentDefenseInDepth:
    """The agent_file path check in invoke_agent() is a defense-in-depth guard.

    Since agent_name is Pydantic-validated, we test the ValueError directly
    by bypassing validation via model_construct (which skips validation).
    """

    async def test_path_within_agents_dir_succeeds(self, tmp_path):
        """A well-formed invocation with an agent that resolves inside AGENTS_DIR
        should proceed to the SDK/CLI call (not raise a path-traversal error)."""
        inv = AgentInvocation(agent_name="pm", prompt="hello")

        # Simulate SDK not available, and CLI not available — we just need to
        # confirm no ValueError is raised from the path check itself.
        sdk_error = ImportError("sdk not installed")
        cli_result = AgentResult(success=True, output="done")

        with patch("orchestrator.agents._invoke_via_sdk", side_effect=sdk_error):
            with patch("orchestrator.agents._invoke_via_cli", new=AsyncMock(return_value=cli_result)):
                result = await invoke_agent(inv)

        assert result.success is True

    async def test_defense_in_depth_detects_traversal(self):
        """Bypass Pydantic validation via model_construct() and verify that
        invoke_agent() still raises ValueError for out-of-AGENTS_DIR paths."""
        # Construct an object that bypasses field validation
        inv = AgentInvocation.model_construct(
            agent_name="../etc/passwd",
            prompt="task",
            model=ModelTier.SONNET,
            max_turns=40,
        )

        with pytest.raises(ValueError, match="Security violation"):
            await invoke_agent(inv)
