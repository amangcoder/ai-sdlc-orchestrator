"""Tests for Pydantic models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from orchestrator.models import (
    AgentRole,
    BenchmarkReport,
    BenchmarkResult,
    EngineeringPlan,
    PhaseStatus,
    PRD,
    Requirement,
    RunState,
    Task,
    TaskStatus,
    Threat,
    ThreatModel,
    WorkflowDefinition,
    WorkflowStepDefinition,
    WorkflowTaskState,
    WorkflowType,
)


class TestRequirement:
    def test_valid(self):
        r = Requirement(id="REQ-001", description="Do something", priority="must")
        assert r.id == "REQ-001"

    def test_invalid_id_pattern(self):
        with pytest.raises(ValidationError):
            Requirement(id="REQ001", description="Do something", priority="must")

    def test_invalid_priority(self):
        with pytest.raises(ValidationError):
            Requirement(id="REQ-001", description="Do something", priority="critical")


class TestPRD:
    def test_valid(self, valid_prd_data):
        prd = PRD(**valid_prd_data)
        assert prd.title == "Dark Mode Toggle"

    def test_short_overview_fails(self, valid_prd_data):
        valid_prd_data["overview"] = "Too short"
        with pytest.raises(ValidationError):
            PRD(**valid_prd_data)

    def test_empty_goals_fails(self, valid_prd_data):
        valid_prd_data["goals"] = []
        with pytest.raises(ValidationError):
            PRD(**valid_prd_data)

    def test_model_dump_round_trip(self, valid_prd_data):
        prd = PRD(**valid_prd_data)
        dumped = prd.model_dump()
        prd2 = PRD(**dumped)
        assert prd == prd2


class TestTask:
    def test_valid(self, valid_tasks_data):
        t = Task(**valid_tasks_data["tasks"][0])
        assert t.task_id == "TASK-001"

    def test_invalid_assigned_role(self, valid_tasks_data):
        data = dict(valid_tasks_data["tasks"][0])
        data["assigned_role"] = "manager"
        with pytest.raises(ValidationError):
            Task(**data)

    def test_backend_engineer_role_accepted(self, valid_tasks_data):
        data = dict(valid_tasks_data["tasks"][0])
        data["assigned_role"] = "backend_engineer"
        t = Task(**data)
        assert t.assigned_role == "backend_engineer"

    def test_frontend_engineer_role_accepted(self, valid_tasks_data):
        data = dict(valid_tasks_data["tasks"][0])
        data["assigned_role"] = "frontend_engineer"
        t = Task(**data)
        assert t.assigned_role == "frontend_engineer"


class TestRunState:
    def test_zero_cost_defaults(self):
        state = RunState(run_id="abc", feature_request="test", workspace_dir="/tmp")
        assert state.total_cost_usd == 0.0
        assert state.review_cycles == 0
        assert state.phases == {}

    def test_model_dump_shape(self):
        state = RunState(run_id="abc", feature_request="test", workspace_dir="/tmp")
        d = state.model_dump()
        assert "run_id" in d
        assert "total_cost_usd" in d
        assert "phases" in d
        assert "workflow_type" in d
        assert "workflow_tasks" in d

    def test_default_workflow_type(self):
        state = RunState(run_id="abc", feature_request="test", workspace_dir="/tmp")
        assert state.workflow_type == WorkflowType.FEATURE_DEVELOPMENT

    def test_completed_steps_tracking(self):
        state = RunState(run_id="abc", feature_request="test", workspace_dir="/tmp")
        state.completed_steps.append("PRD")
        assert "PRD" in state.completed_steps


class TestEnums:
    def test_phase_status_failed_value(self):
        assert PhaseStatus.FAILED.value == "failed"

    def test_phase_status_completed_value(self):
        assert PhaseStatus.COMPLETED.value == "completed"

    def test_task_status_new_states(self):
        assert TaskStatus.ASSIGNED.value == "assigned"
        assert TaskStatus.REVIEW.value == "review"
        assert TaskStatus.BLOCKED.value == "blocked"

    def test_workflow_types(self):
        assert WorkflowType.FEATURE_DEVELOPMENT.value == "feature_development"
        assert WorkflowType.BUGFIX.value == "bugfix"
        assert WorkflowType.SECURITY_AUDIT.value == "security_audit"

    def test_agent_roles(self):
        assert AgentRole.PRODUCT_MANAGER.value == "product_manager"
        assert AgentRole.FRONTEND_ENGINEER.value == "frontend_engineer"
        assert AgentRole.SECURITY_ENGINEER.value == "security_engineer"


class TestWorkflowModels:
    def test_workflow_step_definition(self):
        step = WorkflowStepDefinition(
            name="PRD",
            agent_role=AgentRole.PRODUCT_MANAGER,
            inputs=[],
            outputs=["prd"],
        )
        assert step.name == "PRD"
        assert step.parallel is False
        assert step.on_fail == "escalate"
        assert step.gate is None

    def test_workflow_definition(self):
        wf = WorkflowDefinition(
            name="Test Workflow",
            workflow_type=WorkflowType.CUSTOM,
            steps=[
                WorkflowStepDefinition(
                    name="Step 1",
                    agent_role=AgentRole.PRODUCT_MANAGER,
                )
            ],
        )
        assert len(wf.steps) == 1

    def test_workflow_definition_requires_steps(self):
        with pytest.raises(ValidationError):
            WorkflowDefinition(
                name="Empty",
                workflow_type=WorkflowType.CUSTOM,
                steps=[],
            )


class TestWorkflowTaskState:
    def test_defaults(self):
        task = WorkflowTaskState(
            task_id="STEP-001",
            workflow_step="PRD",
            assigned_role=AgentRole.PRODUCT_MANAGER,
        )
        assert task.status == TaskStatus.PENDING
        assert task.retry_count == 0
        assert task.started_at is None


class TestNewArtifactModels:
    def test_engineering_plan(self):
        plan = EngineeringPlan(
            strategy="Start with data layer then build API endpoints on top",
            implementation_order=["database", "api", "frontend"],
            risk_areas=["migration"],
            testing_strategy="Unit tests for each layer",
        )
        assert len(plan.implementation_order) == 3

    def test_threat_model(self):
        tm = ThreatModel(
            threats=[Threat(
                id="THREAT-001",
                description="SQL injection via user input fields",
                severity="critical",
                mitigation="Use parameterized queries",
            )],
            attack_surface="Web API accepts user input without validation on 5 endpoints",
            recommendations=["Add input validation"],
        )
        assert len(tm.threats) == 1

    def test_benchmark_report(self):
        br = BenchmarkReport(
            results=[BenchmarkResult(
                metric="response_time",
                before=500.0,
                after=200.0,
                unit="ms",
                improvement_pct=60.0,
            )],
            bottlenecks=["N+1 query"],
            recommendations=["Add caching"],
        )
        assert br.results[0].improvement_pct == 60.0
