"""Tests for the orchestration engine."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from orchestrator.agents import AgentResult
from orchestrator.engine import PHASE_ORDER, OrchestratorEngine, _tasks_have_file_conflicts
from orchestrator.models import OrchestratorConfig, PhaseStatus


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


class TestDryRun:
    async def test_full_dry_run_all_phases_completed(self, dry_engine):
        state = await dry_engine.run("Build a todo app")
        for phase_name in PHASE_ORDER:
            assert state.phases[phase_name].status == PhaseStatus.COMPLETED

    async def test_full_dry_run_state_json_written(self, dry_engine, tmp_workspace):
        state = await dry_engine.run("Build a todo app")
        state_file = tmp_workspace / "state.json"
        assert state_file.exists()
        data = json.loads(state_file.read_text())
        assert data["run_id"] == state.run_id

    async def test_single_phase_dry_run(self, dry_engine):
        state = await dry_engine.run("Build a todo app", single_phase="pm")
        assert "pm" in state.phases
        assert state.phases["pm"].status == PhaseStatus.COMPLETED
        assert "architect" not in state.phases


class TestMockedAgentInvocation:
    async def test_failing_agent_stops_pipeline(self, engine):
        fail_result = AgentResult(success=False, error="mock failure")
        with patch("orchestrator.engine.invoke_agent", new=AsyncMock(return_value=fail_result)):
            state = await engine.run("Build a todo app")

        # pm should be the first phase; it fails and pipeline stops
        assert state.phases["pm"].status == PhaseStatus.FAILED
        assert "architect" not in state.phases

    async def test_approve_review_ends_cycle_immediately(self, engine, tmp_workspace):
        # Write a pre-existing review.json with approve verdict
        artifacts = tmp_workspace / "artifacts"
        review_data = {
            "verdict": "approve",
            "issues": [],
            "summary": "Looks great, no changes needed at all.",
        }
        (artifacts / "review.json").write_text(json.dumps(review_data))

        success_result = AgentResult(success=True, cost_usd=0.01)
        with patch("orchestrator.engine.invoke_agent", new=AsyncMock(return_value=success_result)):
            state = await engine.run("Build a todo app")

        assert state.review_cycles == 0
