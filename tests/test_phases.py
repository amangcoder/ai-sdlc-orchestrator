"""Tests for prompt builders and phase definitions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from orchestrator.models import AgentRole, OrchestratorConfig
from orchestrator.phases import (
    PHASE_DEFINITIONS,
    PROMPT_BUILDERS,
    build_backend_engineer_prompt,
    build_engineer_prompt,
    build_frontend_engineer_prompt,
    build_pm_prompt,
    build_principal_engineer_prompt,
    build_reviewer_prompt,
    build_security_engineer_prompt,
    get_engineer_tasks,
)


@pytest.fixture
def config() -> OrchestratorConfig:
    return OrchestratorConfig()


class TestBuildPmPrompt:
    def test_includes_feature_request(self, tmp_workspace, config):
        prompt = build_pm_prompt("Add dark mode", tmp_workspace, config)
        assert "Add dark mode" in prompt

    def test_references_prd_json(self, tmp_workspace, config):
        prompt = build_pm_prompt("Add dark mode", tmp_workspace, config)
        assert "prd.json" in prompt


class TestBuildEngineerPrompt:
    def test_with_task_data_embeds_task_id(self, tmp_workspace, config, valid_tasks_data):
        task = valid_tasks_data["tasks"][0]
        prompt = build_engineer_prompt("Add feature", tmp_workspace, config, task_data=task)
        assert "TASK-001" in prompt

    def test_without_task_data_references_tasks_json(self, tmp_workspace, config):
        prompt = build_engineer_prompt("Add feature", tmp_workspace, config)
        assert "tasks.json" in prompt


class TestBuildReviewerPrompt:
    def test_cycle1_no_previous_review(self, tmp_workspace, config):
        prompt = build_reviewer_prompt("Add feature", tmp_workspace, config, review_cycle=1)
        assert "Previous Review" not in prompt

    def test_cycle2_with_prior_review_includes_it(self, tmp_workspace, config):
        prior = {"verdict": "request_changes", "summary": "Needs work", "issues": []}
        prompt = build_reviewer_prompt(
            "Add feature", tmp_workspace, config, review_cycle=2, previous_review=prior
        )
        assert "Previous Review" in prompt
        assert "request_changes" in prompt


class TestNewPromptBuilders:
    def test_principal_engineer_prompt(self, tmp_workspace, config):
        prompt = build_principal_engineer_prompt("Add feature", tmp_workspace, config)
        assert "Principal Engineer" in prompt
        assert "engineering_plan.json" in prompt

    def test_frontend_engineer_prompt(self, tmp_workspace, config):
        prompt = build_frontend_engineer_prompt("Add feature", tmp_workspace, config)
        assert "Frontend Engineer" in prompt
        assert "accessibility" in prompt.lower()

    def test_backend_engineer_prompt(self, tmp_workspace, config):
        prompt = build_backend_engineer_prompt("Add feature", tmp_workspace, config)
        assert "Backend Engineer" in prompt

    def test_security_engineer_prompt(self, tmp_workspace, config):
        prompt = build_security_engineer_prompt("Add feature", tmp_workspace, config)
        assert "Security Engineer" in prompt
        assert "threat_model.json" in prompt


class TestPromptBuilderRegistry:
    def test_all_18_roles_have_builders(self):
        for role in AgentRole:
            assert role in PROMPT_BUILDERS, f"Missing prompt builder for {role}"

    def test_builders_are_callable(self):
        for role, builder in PROMPT_BUILDERS.items():
            assert callable(builder), f"Builder for {role} is not callable"


class TestGetEngineerTasks:
    def test_empty_without_tasks_json(self, tmp_workspace):
        tasks = get_engineer_tasks(tmp_workspace)
        assert tasks == []

    def test_filters_to_engineer_role(self, tmp_workspace, valid_tasks_data):
        qa_task = {
            "task_id": "TASK-002",
            "title": "Write tests",
            "description": "Write integration tests for the toggle",
            "assigned_role": "qa",
            "dependencies": [],
            "acceptance_criteria": ["Tests pass"],
            "files_to_modify": [],
            "estimated_complexity": "low",
        }
        valid_tasks_data["tasks"].append(qa_task)
        tasks_path = tmp_workspace / "artifacts" / "tasks.json"
        tasks_path.write_text(json.dumps(valid_tasks_data))

        tasks = get_engineer_tasks(tmp_workspace)
        assert len(tasks) == 1
        assert tasks[0]["task_id"] == "TASK-001"

    def test_includes_expanded_roles(self, tmp_workspace):
        data = {
            "tasks": [
                {
                    "task_id": "TASK-001",
                    "title": "Backend work",
                    "description": "Implement backend API endpoints",
                    "assigned_role": "backend_engineer",
                    "dependencies": [],
                    "acceptance_criteria": ["API works"],
                    "files_to_modify": ["src/api.py"],
                    "estimated_complexity": "medium",
                },
                {
                    "task_id": "TASK-002",
                    "title": "Frontend work",
                    "description": "Implement frontend components",
                    "assigned_role": "frontend_engineer",
                    "dependencies": [],
                    "acceptance_criteria": ["UI works"],
                    "files_to_modify": ["src/app.tsx"],
                    "estimated_complexity": "medium",
                },
            ]
        }
        tasks_path = tmp_workspace / "artifacts" / "tasks.json"
        tasks_path.write_text(json.dumps(data))

        tasks = get_engineer_tasks(tmp_workspace)
        assert len(tasks) == 2


class TestPhaseDefinitions:
    def test_all_five_phases_present(self):
        assert set(PHASE_DEFINITIONS.keys()) == {"pm", "architect", "engineer", "qa", "reviewer"}

    def test_all_phases_have_nonempty_agent_name(self):
        for name, phase in PHASE_DEFINITIONS.items():
            assert phase.agent_name, f"{name} has empty agent_name"
