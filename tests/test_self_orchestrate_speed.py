"""Tests for SpeedMode-aware self-orchestration planning and revision layers.

Covers TASK-005 acceptance criteria:
  AC-1  _build_speed_mode_section(STANDARD) returns empty string
  AC-2  _build_speed_mode_section(TURBO) contains skip instructions + 4-step limit
  AC-3  _build_speed_mode_section(THOROUGH) contains advisory security/accessibility hints
  AC-4  _build_speed_mode_section(PARANOID) contains debate + dual-reviewer advisory
  AC-5  self_orchestrate() default args produce a prompt without a SpeedMode section
  AC-6  revise_plan() returns OrchestrationPlan whose speed_mode matches input
  AC-7  No KeyError raised when formatting prompts with any SpeedMode value
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestrator.models import SpeedMode, WorkflowType
from orchestrator.self_orchestrate import (
    OrchestrationPlan,
    _build_speed_mode_section,
    _parse_plan,
    _SELF_ORCHESTRATE_PROMPT,
    _REVISE_PROMPT,
)


# ---------------------------------------------------------------------------
# Unit tests: _build_speed_mode_section
# ---------------------------------------------------------------------------

class TestBuildSpeedModeSection:
    """AC-1 through AC-4."""

    def test_standard_returns_empty_string(self):
        """AC-1: STANDARD → empty (or whitespace-only) string."""
        result = _build_speed_mode_section(SpeedMode.STANDARD)
        assert result.strip() == "", (
            f"Expected empty string for STANDARD, got: {result!r}"
        )

    def test_turbo_skips_qa_review_tpm_principal_debate(self):
        """AC-2: TURBO contains skip instructions."""
        result = _build_speed_mode_section(SpeedMode.TURBO)
        lower = result.lower()
        # Must mention what to skip
        assert "qa" in lower or "code review" in lower or "review" in lower, (
            "TURBO section should mention skipping QA/review"
        )
        assert "tpm" in lower or "technical project manager" in lower, (
            "TURBO section should mention skipping TPM"
        )
        assert "principal_engineer" in lower or "principal engineer" in lower, (
            "TURBO section should mention skipping principal_engineer"
        )
        assert "debate" in lower, "TURBO section should mention skipping debate"

    def test_turbo_mentions_4_step_limit(self):
        """AC-2: TURBO limits pipeline to 4 steps."""
        result = _build_speed_mode_section(SpeedMode.TURBO)
        assert "4" in result, "TURBO section should mention 4-step limit"

    def test_turbo_prefers_builtin_workflows(self):
        """AC-2: TURBO prefers built-in workflows over custom."""
        result = _build_speed_mode_section(SpeedMode.TURBO)
        lower = result.lower()
        assert "built-in" in lower or "builtin" in lower, (
            "TURBO section should prefer built-in workflows"
        )

    def test_thorough_contains_security_engineer(self):
        """AC-3: THOROUGH contains security_engineer with advisory language."""
        result = _build_speed_mode_section(SpeedMode.THOROUGH)
        assert "security_engineer" in result, (
            "THOROUGH section must reference security_engineer"
        )

    def test_thorough_contains_accessibility_auditor(self):
        """AC-3: THOROUGH contains accessibility_auditor with advisory language."""
        result = _build_speed_mode_section(SpeedMode.THOROUGH)
        assert "accessibility_auditor" in result, (
            "THOROUGH section must reference accessibility_auditor"
        )

    def test_thorough_uses_advisory_language(self):
        """AC-3: THOROUGH uses advisory language ('when relevant' or similar)."""
        result = _build_speed_mode_section(SpeedMode.THOROUGH)
        lower = result.lower()
        advisory_phrases = ["when relevant", "when applicable", "prefer", "if relevant", "if applicable"]
        assert any(phrase in lower for phrase in advisory_phrases), (
            f"THOROUGH section should use advisory language, got: {result!r}"
        )

    def test_paranoid_contains_debate(self):
        """AC-4: PARANOID contains 'debate'."""
        result = _build_speed_mode_section(SpeedMode.PARANOID)
        assert "debate" in result.lower(), (
            "PARANOID section must reference debate"
        )

    def test_paranoid_contains_frontend_reviewer(self):
        """AC-4: PARANOID contains 'frontend_reviewer'."""
        result = _build_speed_mode_section(SpeedMode.PARANOID)
        assert "frontend_reviewer" in result, (
            "PARANOID section must reference frontend_reviewer"
        )

    def test_paranoid_contains_backend_reviewer(self):
        """AC-4: PARANOID contains 'backend_reviewer'."""
        result = _build_speed_mode_section(SpeedMode.PARANOID)
        assert "backend_reviewer" in result, (
            "PARANOID section must reference backend_reviewer"
        )

    def test_paranoid_uses_advisory_language(self):
        """AC-4: PARANOID uses advisory language ('when applicable' or similar)."""
        result = _build_speed_mode_section(SpeedMode.PARANOID)
        lower = result.lower()
        advisory_phrases = ["when applicable", "when relevant", "prefer", "if applicable"]
        assert any(phrase in lower for phrase in advisory_phrases), (
            f"PARANOID section should use advisory language, got: {result!r}"
        )

    def test_auto_returns_empty_string(self):
        """AUTO (unresolved) returns empty string — callers should resolve first."""
        result = _build_speed_mode_section(SpeedMode.AUTO)
        assert result.strip() == "", (
            f"Expected empty string for AUTO, got: {result!r}"
        )


# ---------------------------------------------------------------------------
# Unit tests: prompt template formatting (AC-5, AC-7)
# ---------------------------------------------------------------------------

class TestPromptTemplateFormatting:
    """AC-5 and AC-7: No KeyError; STANDARD produces no section."""

    @pytest.mark.parametrize("speed_mode", list(SpeedMode))
    def test_self_orchestrate_prompt_no_key_error(self, speed_mode: SpeedMode):
        """AC-7: _SELF_ORCHESTRATE_PROMPT.format() works for all SpeedMode values."""
        # Should not raise KeyError
        result = _SELF_ORCHESTRATE_PROMPT.format(
            feature_request="Build a todo app",
            agent_catalog="- **Product Manager** (agent file: `pm`)",
            codebase_profile="- **Total files**: 10",
            speed_mode_section=_build_speed_mode_section(speed_mode),
        )
        assert isinstance(result, str)
        assert len(result) > 0

    @pytest.mark.parametrize("speed_mode", list(SpeedMode))
    def test_revise_prompt_no_key_error(self, speed_mode: SpeedMode):
        """AC-7: _REVISE_PROMPT.format() works for all SpeedMode values."""
        current_plan_json = json.dumps({
            "workflow_type": "feature_development",
            "custom_workflow": None,

            "rationale": "test",
            "speed_mode": speed_mode.value,
        })
        result = _REVISE_PROMPT.format(
            feature_request="Build a todo app",
            codebase_profile="- **Total files**: 10",
            agent_catalog="- **Product Manager** (agent file: `pm`)",
            current_plan_json=current_plan_json,
            user_feedback="Add security review",
            speed_mode_section=_build_speed_mode_section(speed_mode),
        )
        assert isinstance(result, str)
        assert len(result) > 0

    def test_standard_prompt_has_no_speed_mode_header(self):
        """AC-5: STANDARD produces a prompt without any SpeedMode section text."""
        result = _SELF_ORCHESTRATE_PROMPT.format(
            feature_request="Build a todo app",
            agent_catalog="- **Product Manager** (agent file: `pm`)",
            codebase_profile="- **Total files**: 10",
            speed_mode_section=_build_speed_mode_section(SpeedMode.STANDARD),
        )
        assert "Speed Mode:" not in result, (
            "STANDARD speed mode should not inject any SpeedMode section into prompt"
        )

    def test_turbo_prompt_contains_turbo_header(self):
        """TURBO mode injects a TURBO section into the prompt."""
        result = _SELF_ORCHESTRATE_PROMPT.format(
            feature_request="Build a todo app",
            agent_catalog="- **Product Manager** (agent file: `pm`)",
            codebase_profile="- **Total files**: 10",
            speed_mode_section=_build_speed_mode_section(SpeedMode.TURBO),
        )
        assert "TURBO" in result

    def test_paranoid_prompt_contains_paranoid_header(self):
        """PARANOID mode injects a PARANOID section into the prompt."""
        result = _SELF_ORCHESTRATE_PROMPT.format(
            feature_request="Build a todo app",
            agent_catalog="- **Product Manager** (agent file: `pm`)",
            codebase_profile="- **Total files**: 10",
            speed_mode_section=_build_speed_mode_section(SpeedMode.PARANOID),
        )
        assert "PARANOID" in result


# ---------------------------------------------------------------------------
# Unit tests: _parse_plan with speed_mode
# ---------------------------------------------------------------------------

class TestParsePlanSpeedMode:
    """_parse_plan stores the speed_mode argument on the returned plan."""

    _valid_json = json.dumps({
        "workflow_type": "feature_development",
        "custom_workflow": None,
        "rationale": "Test rationale",
    })

    @pytest.mark.parametrize("speed_mode", [
        SpeedMode.STANDARD,
        SpeedMode.TURBO,
        SpeedMode.THOROUGH,
        SpeedMode.PARANOID,
    ])
    def test_speed_mode_stored_on_plan(self, speed_mode: SpeedMode):
        plan = _parse_plan(self._valid_json, cost_usd=0.01, speed_mode=speed_mode)
        assert plan.speed_mode == speed_mode

    def test_default_speed_mode_is_standard(self):
        plan = _parse_plan(self._valid_json, cost_usd=0.0)
        assert plan.speed_mode == SpeedMode.STANDARD


# ---------------------------------------------------------------------------
# Unit tests: OrchestrationPlan dataclass default
# ---------------------------------------------------------------------------

class TestOrchestrationPlanSpeedModeField:
    """OrchestrationPlan.speed_mode defaults to STANDARD."""

    def test_default_speed_mode(self):
        plan = OrchestrationPlan(workflow_type=WorkflowType.FEATURE_DEVELOPMENT)
        assert plan.speed_mode == SpeedMode.STANDARD

    def test_explicit_speed_mode(self):
        plan = OrchestrationPlan(
            workflow_type=WorkflowType.BUGFIX,
            speed_mode=SpeedMode.PARANOID,
        )
        assert plan.speed_mode == SpeedMode.PARANOID


# ---------------------------------------------------------------------------
# Integration-style tests: self_orchestrate() with mocked LLM (AC-5, AC-6)
# ---------------------------------------------------------------------------

_MOCK_LLM_RESPONSE = json.dumps({
    "workflow_type": "feature_development",
    "custom_workflow": None,
    "rationale": "Simple feature, use standard pipeline",
})


@pytest.fixture
def mock_agent_result():
    result = MagicMock()
    result.success = True
    result.output = _MOCK_LLM_RESPONSE
    result.cost_usd = 0.005
    result.error = None
    return result


class TestSelfOrchestrateSpeedMode:
    """self_orchestrate() propagates speed_mode to the returned plan."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("speed_mode", [
        SpeedMode.STANDARD,
        SpeedMode.TURBO,
        SpeedMode.THOROUGH,
        SpeedMode.PARANOID,
    ])
    async def test_speed_mode_propagated_to_plan(self, mock_agent_result, speed_mode):
        """self_orchestrate() sets speed_mode on the returned OrchestrationPlan."""
        from orchestrator.self_orchestrate import self_orchestrate

        with patch(
            "orchestrator.agents._invoke_via_sdk",
            new=AsyncMock(return_value=mock_agent_result),
        ):
            plan = await self_orchestrate(
                "Build a todo app",
                speed_mode=speed_mode,
            )

        assert plan.speed_mode == speed_mode

    @pytest.mark.asyncio
    async def test_default_speed_mode_is_standard(self, mock_agent_result):
        """self_orchestrate() defaults to STANDARD speed_mode."""
        from orchestrator.self_orchestrate import self_orchestrate

        with patch(
            "orchestrator.agents._invoke_via_sdk",
            new=AsyncMock(return_value=mock_agent_result),
        ):
            plan = await self_orchestrate("Build a todo app")

        assert plan.speed_mode == SpeedMode.STANDARD

    @pytest.mark.asyncio
    async def test_standard_prompt_no_speed_mode_section(self, mock_agent_result):
        """AC-5: Default (STANDARD) call produces a prompt without SpeedMode section."""
        from orchestrator.self_orchestrate import self_orchestrate

        captured_prompts: list[str] = []

        async def capture_invocation(invocation):
            captured_prompts.append(invocation.prompt)
            return mock_agent_result

        with patch(
            "orchestrator.agents._invoke_via_sdk",
            new=capture_invocation,
        ):
            await self_orchestrate("Build a todo app")

        assert len(captured_prompts) == 1
        assert "Speed Mode:" not in captured_prompts[0], (
            "Default STANDARD call should not inject a Speed Mode section"
        )

    @pytest.mark.asyncio
    async def test_turbo_prompt_contains_turbo_section(self, mock_agent_result):
        """TURBO call injects the TURBO advisory section into the prompt."""
        from orchestrator.self_orchestrate import self_orchestrate

        captured_prompts: list[str] = []

        async def capture_invocation(invocation):
            captured_prompts.append(invocation.prompt)
            return mock_agent_result

        with patch(
            "orchestrator.agents._invoke_via_sdk",
            new=capture_invocation,
        ):
            await self_orchestrate("Build a todo app", speed_mode=SpeedMode.TURBO)

        assert "TURBO" in captured_prompts[0]

    @pytest.mark.asyncio
    async def test_fallback_plan_on_failure_carries_speed_mode(self):
        """When LLM fails, fallback OrchestrationPlan carries the speed_mode."""
        from orchestrator.self_orchestrate import self_orchestrate

        failed_result = MagicMock()
        failed_result.success = False
        failed_result.output = ""
        failed_result.cost_usd = 0.0
        failed_result.error = "network timeout"

        with patch(
            "orchestrator.agents._invoke_via_sdk",
            new=AsyncMock(return_value=failed_result),
        ):
            plan = await self_orchestrate(
                "Build a todo app",
                speed_mode=SpeedMode.THOROUGH,
            )

        assert plan.speed_mode == SpeedMode.THOROUGH


# ---------------------------------------------------------------------------
# Integration-style tests: revise_plan() (AC-6)
# ---------------------------------------------------------------------------

class TestRevisePlanSpeedMode:
    """revise_plan() returns a plan whose speed_mode matches the argument."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("speed_mode", [
        SpeedMode.STANDARD,
        SpeedMode.TURBO,
        SpeedMode.THOROUGH,
        SpeedMode.PARANOID,
    ])
    async def test_revised_plan_speed_mode_matches_input(self, mock_agent_result, speed_mode):
        """AC-6: revise_plan() speed_mode field matches argument."""
        from orchestrator.self_orchestrate import revise_plan

        current_plan = OrchestrationPlan(
            workflow_type=WorkflowType.FEATURE_DEVELOPMENT,
            speed_mode=speed_mode,
        )

        with patch(
            "orchestrator.agents._invoke_via_sdk",
            new=AsyncMock(return_value=mock_agent_result),
        ):
            revised = await revise_plan(
                feature_request="Build a todo app",
                current_plan=current_plan,
                user_feedback="Add a security review step",
                speed_mode=speed_mode,
            )

        assert revised.speed_mode == speed_mode

    @pytest.mark.asyncio
    async def test_revise_prompt_includes_speed_mode_context(self, mock_agent_result):
        """PARANOID revise_plan injects speed mode advisory into revision prompt."""
        from orchestrator.self_orchestrate import revise_plan

        current_plan = OrchestrationPlan(
            workflow_type=WorkflowType.FEATURE_DEVELOPMENT,
            speed_mode=SpeedMode.PARANOID,
        )

        captured_prompts: list[str] = []

        async def capture_invocation(invocation):
            captured_prompts.append(invocation.prompt)
            return mock_agent_result

        with patch(
            "orchestrator.agents._invoke_via_sdk",
            new=capture_invocation,
        ):
            await revise_plan(
                feature_request="Build a todo app",
                current_plan=current_plan,
                user_feedback="Improve security",
                speed_mode=SpeedMode.PARANOID,
            )

        assert "PARANOID" in captured_prompts[0]

    @pytest.mark.asyncio
    async def test_revise_prompt_includes_speed_mode_in_current_plan_json(
        self, mock_agent_result
    ):
        """The current_plan_json dict sent to the LLM includes the speed_mode field."""
        from orchestrator.self_orchestrate import revise_plan

        current_plan = OrchestrationPlan(
            workflow_type=WorkflowType.FEATURE_DEVELOPMENT,
            speed_mode=SpeedMode.THOROUGH,
        )

        captured_prompts: list[str] = []

        async def capture_invocation(invocation):
            captured_prompts.append(invocation.prompt)
            return mock_agent_result

        with patch(
            "orchestrator.agents._invoke_via_sdk",
            new=capture_invocation,
        ):
            await revise_plan(
                feature_request="Build a todo app",
                current_plan=current_plan,
                user_feedback="Improve security",
                speed_mode=SpeedMode.THOROUGH,
            )

        assert '"speed_mode"' in captured_prompts[0]
        assert '"thorough"' in captured_prompts[0]

    @pytest.mark.asyncio
    async def test_standard_revise_prompt_no_speed_mode_section(self, mock_agent_result):
        """STANDARD revise_plan does not inject a Speed Mode section (no regression)."""
        from orchestrator.self_orchestrate import revise_plan

        current_plan = OrchestrationPlan(
            workflow_type=WorkflowType.FEATURE_DEVELOPMENT,
            speed_mode=SpeedMode.STANDARD,
        )

        captured_prompts: list[str] = []

        async def capture_invocation(invocation):
            captured_prompts.append(invocation.prompt)
            return mock_agent_result

        with patch(
            "orchestrator.agents._invoke_via_sdk",
            new=capture_invocation,
        ):
            await revise_plan(
                feature_request="Build a todo app",
                current_plan=current_plan,
                user_feedback="Make it simpler",
                speed_mode=SpeedMode.STANDARD,
            )

        assert "Speed Mode:" not in captured_prompts[0], (
            "STANDARD revise_plan should not inject Speed Mode section"
        )
