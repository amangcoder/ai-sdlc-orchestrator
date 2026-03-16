"""Tests for Pydantic models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from orchestrator.models import (
    PhaseStatus,
    PRD,
    Requirement,
    RunState,
    Task,
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


class TestEnums:
    def test_phase_status_failed_value(self):
        assert PhaseStatus.FAILED.value == "failed"

    def test_phase_status_completed_value(self):
        assert PhaseStatus.COMPLETED.value == "completed"
