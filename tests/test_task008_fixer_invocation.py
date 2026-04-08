"""Tests for TASK-008: Fixer agent invocation in WorkflowEngine._execute_step.

Acceptance criteria verified:
  REQ-009  Fixer reads artifacts, traces decision chain, identifies root cause,
           applies minimal targeted fix.
  REQ-010  Fixer is gated by fixer.enabled and speed_mode; individual steps can
           opt out via skip_fixer flag.
  REQ-011  verdict='fixed' + retry_count < max_attempts → step retry;
           verdict='escalate' or max_attempts exceeded → on_fail.
  AC-013   Fixer is invoked with failed step name, error output, and artifacts.
  AC-014   Fixer verdict='fixed' triggers step retry.
  AC-015   Fixer verdict='escalate' routes to on_fail with fixer_report attached.
  AC-016   max_attempts limit stops retry loop and falls through to on_fail.
  AC-017   fixer.enabled=false skips Fixer entirely.
  AC-018   Fixer crashes are caught and swallowed; pipeline continues to on_fail.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestrator.agents import AgentResult
from orchestrator.models import (
    AgentRole,
    FixerConfig,
    OrchestratorConfig,
    PhaseState,
    PhaseStatus,
    RunState,
    SpeedMode,
    TaskStatus,
    WorkflowStepDefinition,
    WorkflowTaskState,
    WorkflowType,
)
from orchestrator.workflow_engine import WorkflowEngine
from orchestrator.workflows import WorkflowDefinition


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "artifacts").mkdir(parents=True, exist_ok=True)
    return tmp_path


@pytest.fixture
def run_state(workspace: Path) -> RunState:
    return RunState(
        run_id="test-fixer-run",
        feature_request="Build a test app with fixer",
        workspace_dir=str(workspace),
    )


@pytest.fixture
def fixer_config_enabled() -> FixerConfig:
    return FixerConfig(
        enabled=True,
        max_attempts=2,
        speed_modes=["thorough", "paranoid"],
        model="sonnet",
    )


@pytest.fixture
def minimal_workflow() -> WorkflowDefinition:
    """Single-step workflow for testing fixer invocation."""
    return WorkflowDefinition(
        name="Test Workflow",
        workflow_type=WorkflowType.FEATURE_DEVELOPMENT,
        steps=[
            WorkflowStepDefinition(
                name="Implementation",
                agent_role=AgentRole.BACKEND_ENGINEER,
                inputs=[],
                outputs=["tasks"],
            ),
        ],
    )


@pytest.fixture
def engine(minimal_workflow, run_state, workspace) -> WorkflowEngine:
    config = OrchestratorConfig(
        fixer=FixerConfig(enabled=True, max_attempts=2, speed_modes=["thorough", "paranoid"]),
    )
    eng = WorkflowEngine(
        workflow=minimal_workflow,
        state=run_state,
        config=config,
        project_root=workspace,
    )
    return eng


@pytest.fixture
def impl_step(minimal_workflow) -> WorkflowStepDefinition:
    return minimal_workflow.steps[0]


@pytest.fixture
def failed_task(impl_step) -> WorkflowTaskState:
    task = WorkflowTaskState(
        task_id="TASK-001",
        workflow_step=impl_step.name,
        assigned_role=AgentRole.BACKEND_ENGINEER,
        description="Implement the backend",
        status=TaskStatus.FAILED,
        error="ModuleNotFoundError: No module named 'missing_lib'",
    )
    return task


# ---------------------------------------------------------------------------
# Helper: write a fixer_report.json to the artifacts directory
# ---------------------------------------------------------------------------


def _write_fixer_report(workspace: Path, verdict: str) -> None:
    report = {
        "failed_step": "Implementation",
        "error_summary": "Missing module dependency",
        "root_cause_category": "missing_dependency",
        "root_cause_description": "The package 'missing_lib' is not installed.",
        "files_changed": ["requirements.txt"],
        "fix_description": "Added missing_lib to requirements.txt",
        "confidence": 0.9,
        "verdict": verdict,
    }
    (workspace / "artifacts" / "fixer_report.json").write_text(json.dumps(report))


# ---------------------------------------------------------------------------
# Tests: OrchestratorConfig.fixer and WorkflowStepDefinition.skip_fixer fields
# ---------------------------------------------------------------------------


class TestModelFields:
    """Verify new model fields are present with correct defaults."""

    def test_orchestrator_config_has_fixer_field(self):
        """OrchestratorConfig has a 'fixer' field of type FixerConfig."""
        config = OrchestratorConfig()
        assert hasattr(config, "fixer")
        assert isinstance(config.fixer, FixerConfig)

    def test_fixer_config_defaults(self):
        """FixerConfig default values: enabled=False, max_attempts=2."""
        fc = FixerConfig()
        assert fc.enabled is False
        assert fc.max_attempts == 2
        assert "thorough" in fc.speed_modes
        assert "paranoid" in fc.speed_modes

    def test_workflow_step_has_skip_fixer_field(self):
        """WorkflowStepDefinition has a 'skip_fixer' field defaulting to False."""
        step = WorkflowStepDefinition(
            name="Test Step",
            agent_role=AgentRole.BACKEND_ENGINEER,
        )
        assert hasattr(step, "skip_fixer")
        assert step.skip_fixer is False

    def test_skip_fixer_can_be_set_true(self):
        """skip_fixer can be set to True to opt a step out of fixer invocation."""
        step = WorkflowStepDefinition(
            name="Test Step",
            agent_role=AgentRole.BACKEND_ENGINEER,
            skip_fixer=True,
        )
        assert step.skip_fixer is True

    def test_fixer_config_in_orchestrator_can_be_customised(self):
        """OrchestratorConfig.fixer can be set to a custom FixerConfig."""
        config = OrchestratorConfig(
            fixer=FixerConfig(enabled=True, max_attempts=3),
        )
        assert config.fixer.enabled is True
        assert config.fixer.max_attempts == 3


# ---------------------------------------------------------------------------
# Tests: _invoke_fixer — unit tests for the method itself
# ---------------------------------------------------------------------------


class TestInvokeFixer:
    """Unit tests for WorkflowEngine._invoke_fixer."""

    @pytest.mark.asyncio
    async def test_returns_false_when_fixer_disabled(
        self, engine: WorkflowEngine, impl_step, workspace
    ):
        """AC-017: When fixer.enabled=False, _invoke_fixer returns False without invoking agent."""
        engine.config.fixer.enabled = False

        with patch("orchestrator.agents.invoke_agent") as mock_invoke:
            result = await engine._invoke_fixer(impl_step, "some error", workspace)

        assert result is False
        mock_invoke.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_false_when_speed_mode_excluded(
        self, engine: WorkflowEngine, impl_step, workspace
    ):
        """REQ-010 / AC-017: When speed_mode not in fixer.speed_modes, fixer is skipped."""
        engine.config.fixer.enabled = True
        engine.config.fixer.speed_modes = ["thorough", "paranoid"]
        engine.config.speed_mode = SpeedMode.TURBO  # NOT in the allowed list

        with patch("orchestrator.agents.invoke_agent") as mock_invoke:
            result = await engine._invoke_fixer(impl_step, "some error", workspace)

        assert result is False
        mock_invoke.assert_not_called()

    @pytest.mark.asyncio
    async def test_invokes_agent_when_enabled_and_speed_mode_ok(
        self, engine: WorkflowEngine, impl_step, workspace
    ):
        """AC-013: Fixer agent is invoked with Sonnet model and 25 max_turns."""
        engine.config.fixer.enabled = True
        engine.config.speed_mode = SpeedMode.THOROUGH

        _write_fixer_report(workspace, "fixed")

        mock_result = MagicMock(spec=AgentResult)
        mock_result.success = True
        mock_result.cost_usd = 0.05
        mock_result.turns_used = 10

        with patch(
            "orchestrator.workflow_engine.invoke_agent",
            new_callable=AsyncMock,
            return_value=mock_result,
        ) as mock_invoke, patch(
            "orchestrator.phases.build_fixer_prompt", return_value="fixer prompt text"
        ):
            result = await engine._invoke_fixer(impl_step, "error output here", workspace)

        # Verify invoke_agent was called
        mock_invoke.assert_called_once()
        invocation = mock_invoke.call_args[0][0]  # first positional arg
        assert invocation.agent_name == "fixer"
        assert invocation.max_turns == 25
        # Model tier should be SONNET
        from orchestrator.models import ModelTier
        assert invocation.model == ModelTier.SONNET

    @pytest.mark.asyncio
    async def test_returns_true_on_verdict_fixed(
        self, engine: WorkflowEngine, impl_step, workspace
    ):
        """AC-014: When fixer_report.verdict='fixed', _invoke_fixer returns True."""
        engine.config.fixer.enabled = True
        engine.config.speed_mode = SpeedMode.THOROUGH

        _write_fixer_report(workspace, "fixed")

        mock_result = MagicMock(spec=AgentResult)
        mock_result.success = True
        mock_result.cost_usd = 0.05
        mock_result.turns_used = 8

        with patch(
            "orchestrator.workflow_engine.invoke_agent",
            new_callable=AsyncMock,
            return_value=mock_result,
        ), patch("orchestrator.phases.build_fixer_prompt", return_value="prompt"):
            result = await engine._invoke_fixer(impl_step, "error", workspace)

        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_on_verdict_escalate(
        self, engine: WorkflowEngine, impl_step, workspace
    ):
        """AC-015: When fixer_report.verdict='escalate', _invoke_fixer returns False."""
        engine.config.fixer.enabled = True
        engine.config.speed_mode = SpeedMode.THOROUGH

        _write_fixer_report(workspace, "escalate")

        mock_result = MagicMock(spec=AgentResult)
        mock_result.success = False
        mock_result.cost_usd = 0.03
        mock_result.turns_used = 5

        with patch(
            "orchestrator.workflow_engine.invoke_agent",
            new_callable=AsyncMock,
            return_value=mock_result,
        ), patch("orchestrator.phases.build_fixer_prompt", return_value="prompt"):
            result = await engine._invoke_fixer(impl_step, "error", workspace)

        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_report_missing(
        self, engine: WorkflowEngine, impl_step, workspace
    ):
        """If fixer_report.json is not written, _invoke_fixer returns False (escalate)."""
        engine.config.fixer.enabled = True
        engine.config.speed_mode = SpeedMode.THOROUGH

        # Do NOT write fixer_report.json

        mock_result = MagicMock(spec=AgentResult)
        mock_result.success = True
        mock_result.cost_usd = 0.02
        mock_result.turns_used = 3

        with patch(
            "orchestrator.workflow_engine.invoke_agent",
            new_callable=AsyncMock,
            return_value=mock_result,
        ), patch("orchestrator.phases.build_fixer_prompt", return_value="prompt"):
            result = await engine._invoke_fixer(impl_step, "error", workspace)

        assert result is False

    @pytest.mark.asyncio
    async def test_swallows_exception_and_returns_false(
        self, engine: WorkflowEngine, impl_step, workspace
    ):
        """AC-018: Any exception inside _invoke_fixer is swallowed; returns False."""
        engine.config.fixer.enabled = True
        engine.config.speed_mode = SpeedMode.THOROUGH

        with patch(
            "orchestrator.workflow_engine.invoke_agent",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Agent SDK exploded"),
        ), patch("orchestrator.phases.build_fixer_prompt", return_value="prompt"):
            result = await engine._invoke_fixer(impl_step, "error", workspace)

        # Must return False, not raise
        assert result is False

    @pytest.mark.asyncio
    async def test_swallows_prompt_build_exception(
        self, engine: WorkflowEngine, impl_step, workspace
    ):
        """AC-018: Even prompt-building failures are swallowed."""
        engine.config.fixer.enabled = True
        engine.config.speed_mode = SpeedMode.THOROUGH

        with patch(
            "orchestrator.phases.build_fixer_prompt",
            side_effect=ValueError("prompt build failed"),
        ):
            result = await engine._invoke_fixer(impl_step, "error", workspace)

        assert result is False

    @pytest.mark.asyncio
    async def test_accumulates_cost_to_run_state(
        self, engine: WorkflowEngine, impl_step, workspace, run_state
    ):
        """Fixer agent cost is added to run_state.total_cost_usd."""
        engine.config.fixer.enabled = True
        engine.config.speed_mode = SpeedMode.THOROUGH

        _write_fixer_report(workspace, "fixed")

        initial_cost = run_state.total_cost_usd
        mock_result = MagicMock(spec=AgentResult)
        mock_result.success = True
        mock_result.cost_usd = 0.07
        mock_result.turns_used = 12

        with patch(
            "orchestrator.workflow_engine.invoke_agent",
            new_callable=AsyncMock,
            return_value=mock_result,
        ), patch("orchestrator.phases.build_fixer_prompt", return_value="prompt"):
            await engine._invoke_fixer(impl_step, "error", workspace)

        assert run_state.total_cost_usd == pytest.approx(initial_cost + 0.07)

    @pytest.mark.asyncio
    async def test_builds_prompt_with_failed_step_and_error(
        self, engine: WorkflowEngine, impl_step, workspace
    ):
        """AC-013: Fixer prompt includes failed step name and error output."""
        engine.config.fixer.enabled = True
        engine.config.speed_mode = SpeedMode.THOROUGH

        _write_fixer_report(workspace, "escalate")

        mock_result = MagicMock(spec=AgentResult)
        mock_result.success = False
        mock_result.cost_usd = 0.01
        mock_result.turns_used = 2

        captured_task_data: list[dict] = []

        def _capture_prompt(feature_request, workspace, config, task_data=None):
            captured_task_data.append(task_data or {})
            return "fixer prompt"

        with patch(
            "orchestrator.workflow_engine.invoke_agent",
            new_callable=AsyncMock,
            return_value=mock_result,
        ), patch("orchestrator.phases.build_fixer_prompt", side_effect=_capture_prompt):
            await engine._invoke_fixer(
                impl_step, "ModuleNotFoundError: missing_lib", workspace
            )

        assert captured_task_data, "build_fixer_prompt was not called"
        task_data = captured_task_data[0]
        assert task_data.get("failed_step") == impl_step.name
        assert "ModuleNotFoundError" in task_data.get("error_output", "")

    @pytest.mark.asyncio
    async def test_no_speed_mode_set_fixer_runs(
        self, engine: WorkflowEngine, impl_step, workspace
    ):
        """When speed_mode is None (not set), fixer invocation proceeds."""
        engine.config.fixer.enabled = True
        engine.config.speed_mode = None  # no speed mode gate

        _write_fixer_report(workspace, "fixed")

        mock_result = MagicMock(spec=AgentResult)
        mock_result.success = True
        mock_result.cost_usd = 0.04
        mock_result.turns_used = 7

        with patch(
            "orchestrator.workflow_engine.invoke_agent",
            new_callable=AsyncMock,
            return_value=mock_result,
        ), patch("orchestrator.phases.build_fixer_prompt", return_value="prompt"):
            result = await engine._invoke_fixer(impl_step, "error", workspace)

        assert result is True

    @pytest.mark.asyncio
    async def test_handles_malformed_fixer_report_json(
        self, engine: WorkflowEngine, impl_step, workspace
    ):
        """Malformed fixer_report.json causes _invoke_fixer to return False (escalate)."""
        engine.config.fixer.enabled = True
        engine.config.speed_mode = SpeedMode.THOROUGH

        # Write invalid JSON
        (workspace / "artifacts" / "fixer_report.json").write_text("{bad json}")

        mock_result = MagicMock(spec=AgentResult)
        mock_result.success = True
        mock_result.cost_usd = 0.02
        mock_result.turns_used = 4

        with patch(
            "orchestrator.workflow_engine.invoke_agent",
            new_callable=AsyncMock,
            return_value=mock_result,
        ), patch("orchestrator.phases.build_fixer_prompt", return_value="prompt"):
            result = await engine._invoke_fixer(impl_step, "error", workspace)

        assert result is False


# ---------------------------------------------------------------------------
# Tests: _execute_step — fixer retry integration
# ---------------------------------------------------------------------------


class TestExecuteStepFixerIntegration:
    """Integration-style tests for fixer retry in _execute_step."""

    def _make_step(self, skip_fixer: bool = False) -> WorkflowStepDefinition:
        return WorkflowStepDefinition(
            name="Implementation",
            agent_role=AgentRole.BACKEND_ENGINEER,
            inputs=[],
            outputs=[],  # no outputs to avoid artifact rescue complexity
        )

    @pytest.mark.asyncio
    async def test_fixer_not_invoked_when_disabled(
        self, engine: WorkflowEngine, workspace, run_state
    ):
        """AC-017: When fixer.enabled=False, fixer is NOT invoked on step failure."""
        engine.config.fixer.enabled = False

        step = self._make_step()
        engine.workflow.steps = [step]
        run_state.phases = {}
        run_state.completed_steps = []

        invoke_fixer_called = []

        async def _mock_invoke_fixer(s, err, ws):
            invoke_fixer_called.append(True)
            return False

        with (
            patch.object(engine, "_execute_step_tasks", return_value=False),
            patch.object(engine, "_stop_knowledge_watcher", new_callable=AsyncMock),
            patch.object(engine, "_start_knowledge_watcher", new_callable=AsyncMock),
            patch.object(engine, "_emit_phase_complete"),
            patch.object(engine, "_invoke_fixer", side_effect=_mock_invoke_fixer),
        ):
            result = await engine._execute_step(step)

        assert result == "failed"
        assert not invoke_fixer_called, "Fixer must not be invoked when disabled"

    @pytest.mark.asyncio
    async def test_fixer_not_invoked_when_skip_fixer_true(
        self, engine: WorkflowEngine, workspace, run_state
    ):
        """REQ-010: Steps with skip_fixer=True are never submitted to the fixer."""
        engine.config.fixer.enabled = True
        engine.config.speed_mode = SpeedMode.THOROUGH

        step = WorkflowStepDefinition(
            name="Implementation",
            agent_role=AgentRole.BACKEND_ENGINEER,
            inputs=[],
            outputs=[],
            skip_fixer=True,
        )
        engine.workflow.steps = [step]
        run_state.phases = {}
        run_state.completed_steps = []

        invoke_fixer_called = []

        async def _mock_invoke_fixer(s, err, ws):
            invoke_fixer_called.append(True)
            return False

        with (
            patch.object(engine, "_execute_step_tasks", return_value=False),
            patch.object(engine, "_stop_knowledge_watcher", new_callable=AsyncMock),
            patch.object(engine, "_start_knowledge_watcher", new_callable=AsyncMock),
            patch.object(engine, "_emit_phase_complete"),
            patch.object(engine, "_invoke_fixer", side_effect=_mock_invoke_fixer),
        ):
            result = await engine._execute_step(step)

        assert result == "failed"
        assert not invoke_fixer_called, "Fixer must not be invoked when skip_fixer=True"

    @pytest.mark.asyncio
    async def test_step_retried_after_fixer_returns_true(
        self, engine: WorkflowEngine, workspace, run_state
    ):
        """AC-014: When fixer returns True (verdict='fixed'), the step is retried."""
        engine.config.fixer.enabled = True
        engine.config.speed_mode = SpeedMode.THOROUGH

        step = self._make_step()
        engine.workflow.steps = [step]
        run_state.phases = {}
        run_state.completed_steps = []

        call_count = []

        async def _mock_execute_tasks(s, tasks):
            call_count.append(len(call_count) + 1)
            # Fail on first call, succeed on second (after fixer)
            return len(call_count) > 1

        # Fixer returns True (fixed) on first call
        async def _mock_invoke_fixer(s, err, ws):
            return True

        with (
            patch.object(engine, "_execute_step_tasks", side_effect=_mock_execute_tasks),
            patch.object(engine, "_stop_knowledge_watcher", new_callable=AsyncMock),
            patch.object(engine, "_start_knowledge_watcher", new_callable=AsyncMock),
            patch.object(engine, "_emit_phase_complete"),
            patch.object(engine, "_refresh_knowledge", new_callable=AsyncMock),
            patch.object(engine, "_invoke_fixer", side_effect=_mock_invoke_fixer),
        ):
            result = await engine._execute_step(step)

        assert result == "completed", "Step should succeed after fixer retry"
        assert len(call_count) == 2, "Step should be executed exactly twice (initial + retry)"

    @pytest.mark.asyncio
    async def test_step_fails_after_fixer_returns_false(
        self, engine: WorkflowEngine, workspace, run_state
    ):
        """AC-015: When fixer returns False (verdict='escalate'), falls through to on_fail."""
        engine.config.fixer.enabled = True
        engine.config.speed_mode = SpeedMode.THOROUGH

        step = self._make_step()
        engine.workflow.steps = [step]
        run_state.phases = {}
        run_state.completed_steps = []

        async def _mock_invoke_fixer(s, err, ws):
            return False  # escalate

        with (
            patch.object(engine, "_execute_step_tasks", return_value=False),
            patch.object(engine, "_stop_knowledge_watcher", new_callable=AsyncMock),
            patch.object(engine, "_start_knowledge_watcher", new_callable=AsyncMock),
            patch.object(engine, "_emit_phase_complete"),
            patch.object(engine, "_invoke_fixer", side_effect=_mock_invoke_fixer),
        ):
            result = await engine._execute_step(step)

        assert result == "failed"

    @pytest.mark.asyncio
    async def test_max_attempts_stops_retry_loop(
        self, engine: WorkflowEngine, workspace, run_state
    ):
        """AC-016: Fixer retry stops after max_attempts and falls through to on_fail."""
        engine.config.fixer.enabled = True
        engine.config.fixer.max_attempts = 2
        engine.config.speed_mode = SpeedMode.THOROUGH

        step = self._make_step()
        engine.workflow.steps = [step]
        run_state.phases = {}
        run_state.completed_steps = []

        fixer_call_count = []
        execute_call_count = []

        async def _mock_execute_tasks(s, tasks):
            execute_call_count.append(1)
            return False  # always fail

        async def _mock_invoke_fixer(s, err, ws):
            fixer_call_count.append(1)
            return True  # always say "fixed" to keep retrying

        with (
            patch.object(engine, "_execute_step_tasks", side_effect=_mock_execute_tasks),
            patch.object(engine, "_stop_knowledge_watcher", new_callable=AsyncMock),
            patch.object(engine, "_start_knowledge_watcher", new_callable=AsyncMock),
            patch.object(engine, "_emit_phase_complete"),
            patch.object(engine, "_invoke_fixer", side_effect=_mock_invoke_fixer),
        ):
            result = await engine._execute_step(step)

        assert result == "failed", "Should eventually fall through to on_fail"
        # With max_attempts=2: initial fail + 2 fixer retries = 3 total executions
        # After 2 fixer retries, the loop exits because _fixer_retry_count >= max_attempts
        assert len(execute_call_count) == 3, (
            f"Expected 3 executions (1 initial + 2 fixer retries), got {len(execute_call_count)}"
        )
        assert len(fixer_call_count) == 2, (
            f"Expected fixer called 2 times (max_attempts=2), got {len(fixer_call_count)}"
        )

    @pytest.mark.asyncio
    async def test_fixer_crash_swallowed_pipeline_continues(
        self, engine: WorkflowEngine, workspace, run_state
    ):
        """AC-018: If _invoke_fixer raises an exception, it is swallowed and
        the pipeline falls through to on_fail (step returns 'failed')."""
        engine.config.fixer.enabled = True
        engine.config.speed_mode = SpeedMode.THOROUGH

        step = self._make_step()
        engine.workflow.steps = [step]
        run_state.phases = {}
        run_state.completed_steps = []

        async def _crashing_invoke_fixer(s, err, ws):
            raise RuntimeError("Fixer SDK completely broken")

        with (
            patch.object(engine, "_execute_step_tasks", return_value=False),
            patch.object(engine, "_stop_knowledge_watcher", new_callable=AsyncMock),
            patch.object(engine, "_start_knowledge_watcher", new_callable=AsyncMock),
            patch.object(engine, "_emit_phase_complete"),
            patch.object(engine, "_invoke_fixer", side_effect=_crashing_invoke_fixer),
        ):
            # Must not raise — fixer crash must be swallowed
            result = await engine._execute_step(step)

        assert result == "failed", "Pipeline should continue to on_fail on fixer crash"

    @pytest.mark.asyncio
    async def test_fixer_invoked_with_task_error_details(
        self, engine: WorkflowEngine, workspace, run_state
    ):
        """AC-013: Error output passed to _invoke_fixer contains task error messages."""
        engine.config.fixer.enabled = True
        engine.config.speed_mode = SpeedMode.THOROUGH

        step = self._make_step()
        engine.workflow.steps = [step]
        run_state.phases = {}
        run_state.completed_steps = []

        captured_errors: list[str] = []

        async def _capture_fixer(s, error_output, ws):
            captured_errors.append(error_output)
            return False

        # Create a task with error info and stub _recover_or_create_tasks
        failed_task = WorkflowTaskState(
            task_id="TASK-001",
            workflow_step="Implementation",
            assigned_role=AgentRole.BACKEND_ENGINEER,
            description="Implement something",
            status=TaskStatus.FAILED,
            error="ImportError: cannot import name 'foo'",
        )

        with (
            patch.object(engine, "_execute_step_tasks", return_value=False),
            patch.object(engine, "_stop_knowledge_watcher", new_callable=AsyncMock),
            patch.object(engine, "_start_knowledge_watcher", new_callable=AsyncMock),
            patch.object(engine, "_emit_phase_complete"),
            patch.object(engine, "_invoke_fixer", side_effect=_capture_fixer),
            patch.object(engine, "_recover_or_create_tasks", return_value=[failed_task]),
            patch.object(engine, "_validate_step_inputs", return_value=[]),
        ):
            await engine._execute_step(step)

        assert captured_errors, "_invoke_fixer must be called with error_output"
        error_output = captured_errors[0]
        # Error output should reference the task's error
        assert "ImportError" in error_output or "TASK-001" in error_output

    @pytest.mark.asyncio
    async def test_step_phase_status_failed_when_fixer_escalates(
        self, engine: WorkflowEngine, workspace, run_state
    ):
        """AC-015: Phase status is FAILED after fixer escalation."""
        engine.config.fixer.enabled = True
        engine.config.speed_mode = SpeedMode.THOROUGH

        step = self._make_step()
        engine.workflow.steps = [step]
        run_state.phases = {}
        run_state.completed_steps = []

        phase_key = engine._step_to_phase_key(step)

        async def _mock_invoke_fixer(s, err, ws):
            return False

        with (
            patch.object(engine, "_execute_step_tasks", return_value=False),
            patch.object(engine, "_stop_knowledge_watcher", new_callable=AsyncMock),
            patch.object(engine, "_start_knowledge_watcher", new_callable=AsyncMock),
            patch.object(engine, "_emit_phase_complete"),
            patch.object(engine, "_invoke_fixer", side_effect=_mock_invoke_fixer),
        ):
            await engine._execute_step(step)

        assert run_state.phases[phase_key].status == PhaseStatus.FAILED

    @pytest.mark.asyncio
    async def test_fixer_not_invoked_on_success(
        self, engine: WorkflowEngine, workspace, run_state
    ):
        """Fixer is only invoked on failure — not on step success."""
        engine.config.fixer.enabled = True
        engine.config.speed_mode = SpeedMode.THOROUGH

        step = self._make_step()
        engine.workflow.steps = [step]
        run_state.phases = {}
        run_state.completed_steps = []

        fixer_called = []

        async def _mock_invoke_fixer(s, err, ws):
            fixer_called.append(True)
            return False

        with (
            patch.object(engine, "_execute_step_tasks", return_value=True),
            patch.object(engine, "_stop_knowledge_watcher", new_callable=AsyncMock),
            patch.object(engine, "_start_knowledge_watcher", new_callable=AsyncMock),
            patch.object(engine, "_emit_phase_complete"),
            patch.object(engine, "_refresh_knowledge", new_callable=AsyncMock),
            patch.object(engine, "_invoke_fixer", side_effect=_mock_invoke_fixer),
        ):
            await engine._execute_step(step)

        assert not fixer_called, "Fixer must NOT be invoked when step succeeds"

    @pytest.mark.asyncio
    async def test_failed_tasks_reset_to_pending_before_retry(
        self, engine: WorkflowEngine, workspace, run_state
    ):
        """AC-014: Failed tasks are reset to PENDING before fixer retry execution."""
        engine.config.fixer.enabled = True
        engine.config.speed_mode = SpeedMode.THOROUGH

        step = self._make_step()
        engine.workflow.steps = [step]
        run_state.phases = {}
        run_state.completed_steps = []

        tasks_at_retry: list[list[WorkflowTaskState]] = []

        async def _mock_execute_tasks(s, tasks):
            tasks_at_retry.append(list(tasks))
            if len(tasks_at_retry) == 1:
                # First call: mark task as failed and return False
                for t in tasks:
                    t.status = TaskStatus.FAILED
                    t.error = "first failure"
                return False
            else:
                # Second call (after fixer): all tasks should be PENDING
                return True

        async def _mock_invoke_fixer(s, err, ws):
            return True

        failed_task = WorkflowTaskState(
            task_id="TASK-001",
            workflow_step="Implementation",
            assigned_role=AgentRole.BACKEND_ENGINEER,
            description="Do something",
        )

        with (
            patch.object(engine, "_execute_step_tasks", side_effect=_mock_execute_tasks),
            patch.object(engine, "_stop_knowledge_watcher", new_callable=AsyncMock),
            patch.object(engine, "_start_knowledge_watcher", new_callable=AsyncMock),
            patch.object(engine, "_emit_phase_complete"),
            patch.object(engine, "_refresh_knowledge", new_callable=AsyncMock),
            patch.object(engine, "_invoke_fixer", side_effect=_mock_invoke_fixer),
            patch.object(engine, "_recover_or_create_tasks", return_value=[failed_task]),
            patch.object(engine, "_validate_step_inputs", return_value=[]),
        ):
            result = await engine._execute_step(step)

        assert result == "completed"
        # Tasks passed to the retry call should all be PENDING
        assert len(tasks_at_retry) == 2
        retry_tasks = tasks_at_retry[1]
        for task in retry_tasks:
            assert task.status == TaskStatus.PENDING, (
                f"Task {task.task_id} should be reset to PENDING for retry, got {task.status}"
            )
            assert task.error is None, "Task error should be cleared before retry"
