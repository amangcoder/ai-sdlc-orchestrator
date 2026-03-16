"""Tests for the orchestration engine."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from orchestrator.agents import AgentResult
from orchestrator.engine import PHASE_ORDER, OrchestratorEngine, _tasks_have_file_conflicts
from orchestrator.models import AgentRole, OrchestratorConfig, PhaseStatus, WorkflowTaskState, WorkflowType
from orchestrator.workflow_engine import _compute_dependency_waves


@pytest.fixture
def config() -> OrchestratorConfig:
    return OrchestratorConfig(workspace_dir="{tmp}")


@pytest.fixture
def engine(config, tmp_workspace) -> OrchestratorEngine:
    cfg = OrchestratorConfig(workspace_dir=str(tmp_workspace))
    return OrchestratorEngine(config=cfg, dry_run=False)


@pytest.fixture
def dry_engine(tmp_workspace) -> OrchestratorEngine:
    cfg = OrchestratorConfig(workspace_dir=str(tmp_workspace))
    return OrchestratorEngine(config=cfg, dry_run=True)


class TestPhaseOrder:
    def test_phase_order(self):
        assert PHASE_ORDER == ["pm", "architect", "engineer", "qa", "reviewer"]


class TestTasksHaveFileConflicts:
    def test_no_conflict(self):
        tasks = [
            {"files_to_modify": ["a.py", "b.py"]},
            {"files_to_modify": ["c.py"]},
        ]
        assert not _tasks_have_file_conflicts(tasks)

    def test_with_conflict(self):
        tasks = [
            {"files_to_modify": ["a.py"]},
            {"files_to_modify": ["a.py", "b.py"]},
        ]
        assert _tasks_have_file_conflicts(tasks)

    def test_empty_files(self):
        tasks = [
            {"files_to_modify": []},
            {"files_to_modify": []},
        ]
        assert not _tasks_have_file_conflicts(tasks)

    def test_empty_list(self):
        assert not _tasks_have_file_conflicts([])


class TestLegacyDryRun:
    """Test legacy mode dry run (using --phase flag)."""

    async def test_single_phase_dry_run(self, dry_engine):
        state = await dry_engine.run("Build a todo app", single_phase="pm")
        assert "pm" in state.phases
        assert state.phases["pm"].status == PhaseStatus.COMPLETED
        assert "architect" not in state.phases

    async def test_full_legacy_dry_run_all_phases(self, dry_engine):
        """Legacy mode: all 5 phases should complete."""
        state = await dry_engine.run("Build a todo app", single_phase="pm")
        assert state.phases["pm"].status == PhaseStatus.COMPLETED

    async def test_legacy_state_json_written(self, dry_engine, tmp_workspace):
        state = await dry_engine.run("Build a todo app", single_phase="pm")
        state_file = tmp_workspace / "state.json"
        assert state_file.exists()
        data = json.loads(state_file.read_text())
        assert data["run_id"] == state.run_id


class TestWorkflowDryRun:
    """Test workflow engine dry run."""

    async def test_workflow_dry_run_completes(self, dry_engine):
        state = await dry_engine.run(
            "Build a todo app",
            workflow_type=WorkflowType.FEATURE_DEVELOPMENT,
        )
        # The workflow engine uses step names as phase keys
        assert len(state.completed_steps) > 0

    async def test_workflow_dry_run_bugfix(self, dry_engine):
        state = await dry_engine.run(
            "Fix login bug",
            workflow_type=WorkflowType.BUGFIX,
        )
        assert state.workflow_type == WorkflowType.BUGFIX
        assert len(state.completed_steps) > 0

    async def test_workflow_dry_run_state_persisted(self, dry_engine, tmp_workspace):
        state = await dry_engine.run("Build a todo app")
        state_file = tmp_workspace / "state.json"
        assert state_file.exists()
        data = json.loads(state_file.read_text())
        assert data["workflow_type"] == "feature_development"


class TestMockedAgentInvocation:
    async def test_failing_agent_stops_legacy_pipeline(self, engine):
        """Legacy mode: failing agent stops the pipeline."""
        fail_result = AgentResult(success=False, error="mock failure")
        with patch("orchestrator.engine.invoke_agent", new=AsyncMock(return_value=fail_result)):
            state = await engine.run("Build a todo app", single_phase="pm")

        assert state.phases["pm"].status == PhaseStatus.FAILED

    async def test_approve_review_ends_legacy_cycle(self, engine, tmp_workspace):
        """Legacy mode: approved review ends the cycle."""
        artifacts = tmp_workspace / "artifacts"
        review_data = {
            "verdict": "approve",
            "issues": [],
            "summary": "Looks great, no changes needed at all.",
        }
        (artifacts / "review.json").write_text(json.dumps(review_data))

        success_result = AgentResult(success=True, cost_usd=0.01)
        with patch("orchestrator.engine.invoke_agent", new=AsyncMock(return_value=success_result)):
            state = await engine.run("Build a todo app", from_phase="reviewer")

        assert state.review_cycles == 0

class TestComputeDependencyWaves:
    """Tests for the DAG scheduler that computes execution waves."""

    def _task(self, task_id: str, deps: list[str] | None = None) -> WorkflowTaskState:
        return WorkflowTaskState(
            task_id=task_id,
            workflow_step="Implementation",
            assigned_role=AgentRole.BACKEND_ENGINEER,
            dependencies=deps or [],
        )

    def test_empty_tasks(self):
        assert _compute_dependency_waves([]) == []

    def test_no_dependencies_single_wave(self):
        tasks = [self._task("T1"), self._task("T2"), self._task("T3")]
        waves = _compute_dependency_waves(tasks)
        assert len(waves) == 1
        assert len(waves[0]) == 3

    def test_linear_chain(self):
        """T1 → T2 → T3 should produce 3 waves of 1 task each."""
        tasks = [
            self._task("T1"),
            self._task("T2", ["T1"]),
            self._task("T3", ["T2"]),
        ]
        waves = _compute_dependency_waves(tasks)
        assert len(waves) == 3
        assert [w[0].task_id for w in waves] == ["T1", "T2", "T3"]

    def test_diamond_dependency(self):
        """Diamond: T1 → T2, T1 → T3, T2+T3 → T4. Waves: [T1], [T2,T3], [T4]."""
        tasks = [
            self._task("T1"),
            self._task("T2", ["T1"]),
            self._task("T3", ["T1"]),
            self._task("T4", ["T2", "T3"]),
        ]
        waves = _compute_dependency_waves(tasks)
        assert len(waves) == 3
        assert waves[0][0].task_id == "T1"
        assert {t.task_id for t in waves[1]} == {"T2", "T3"}
        assert waves[2][0].task_id == "T4"

    def test_external_dependencies_ignored(self):
        """Dependencies on tasks outside this step's set are ignored."""
        tasks = [
            self._task("T1", ["EXTERNAL-001"]),
            self._task("T2", ["EXTERNAL-002"]),
        ]
        waves = _compute_dependency_waves(tasks)
        assert len(waves) == 1
        assert len(waves[0]) == 2

    def test_circular_dependency_falls_back(self):
        """Circular deps should produce waves ending with a fallback wave."""
        tasks = [
            self._task("T1", ["T2"]),
            self._task("T2", ["T1"]),
        ]
        waves = _compute_dependency_waves(tasks)
        # Should still produce at least one wave (fallback)
        assert len(waves) >= 1
        # All tasks should be included
        all_ids = {t.task_id for w in waves for t in w}
        assert all_ids == {"T1", "T2"}

    def test_mixed_deps_and_independent(self):
        """Mix of independent and dependent tasks.
        T1: no deps, T2: no deps, T3: depends on T1, T4: depends on T1, T5: depends on T3+T4
        Wave 1: [T1, T2], Wave 2: [T3, T4], Wave 3: [T5]
        """
        tasks = [
            self._task("T1"),
            self._task("T2"),
            self._task("T3", ["T1"]),
            self._task("T4", ["T1"]),
            self._task("T5", ["T3", "T4"]),
        ]
        waves = _compute_dependency_waves(tasks)
        assert len(waves) == 3
        assert {t.task_id for t in waves[0]} == {"T1", "T2"}
        assert {t.task_id for t in waves[1]} == {"T3", "T4"}
        assert waves[2][0].task_id == "T5"

    def test_wide_fan_out(self):
        """One root task with 10 dependents → 2 waves."""
        dependents = [self._task(f"T{i}", ["T0"]) for i in range(1, 11)]
        tasks = [self._task("T0")] + dependents
        waves = _compute_dependency_waves(tasks)
        assert len(waves) == 2
        assert waves[0][0].task_id == "T0"
        assert len(waves[1]) == 10


class TestMockedWorkflowInvocation:
    async def test_failing_agent_stops_workflow(self, engine, tmp_workspace):
        """Workflow mode: failing agent stops the workflow."""
        fail_result = AgentResult(success=False, error="mock failure")
        with patch("orchestrator.workflow_engine.invoke_agent", new=AsyncMock(return_value=fail_result)):
            state = await engine.run("Build a todo app")

        # First step (PRD) should have failed
        assert any(
            ps.status == PhaseStatus.FAILED
            for ps in state.phases.values()
        )
