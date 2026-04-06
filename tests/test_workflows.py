"""Tests for workflow templates and custom workflow parsing."""

from __future__ import annotations

import pytest

from orchestrator.models import AgentRole, WorkflowType
from orchestrator.workflows import (
    BUILTIN_WORKFLOWS,
    BUGFIX,
    FEATURE_DEVELOPMENT,
    PERFORMANCE_OPTIMIZATION,
    REFACTOR,
    SECURITY_AUDIT,
    parse_custom_workflow,
    select_workflow,
)


class TestBuiltinWorkflows:
    def test_all_five_workflows_registered(self):
        assert len(BUILTIN_WORKFLOWS) == 5
        assert WorkflowType.FEATURE_DEVELOPMENT in BUILTIN_WORKFLOWS
        assert WorkflowType.BUGFIX in BUILTIN_WORKFLOWS
        assert WorkflowType.REFACTOR in BUILTIN_WORKFLOWS
        assert WorkflowType.PERFORMANCE_OPTIMIZATION in BUILTIN_WORKFLOWS
        assert WorkflowType.SECURITY_AUDIT in BUILTIN_WORKFLOWS

    def test_feature_development_has_10_steps(self):
        assert len(FEATURE_DEVELOPMENT.steps) == 10

    def test_feature_development_starts_with_prd(self):
        assert FEATURE_DEVELOPMENT.steps[0].name == "PRD"
        assert FEATURE_DEVELOPMENT.steps[0].agent_role == AgentRole.PRODUCT_MANAGER

    def test_feature_development_implementation_is_parallel(self):
        impl_step = next(s for s in FEATURE_DEVELOPMENT.steps if s.name == "Implementation")
        assert impl_step.parallel is True

    def test_feature_development_release_has_approval_gate(self):
        release_step = next(s for s in FEATURE_DEVELOPMENT.steps if s.name == "Release")
        assert release_step.gate == "approval"

    def test_bugfix_workflow_steps(self):
        assert BUGFIX.steps[0].name == "Bug Analysis"
        assert len(BUGFIX.steps) == 8

    def test_security_audit_uses_security_engineer(self):
        threat_step = SECURITY_AUDIT.steps[0]
        assert threat_step.agent_role == AgentRole.SECURITY_ENGINEER

    def test_all_workflows_have_valid_step_names(self):
        for wf_type, wf in BUILTIN_WORKFLOWS.items():
            for step in wf.steps:
                assert step.name, f"{wf_type}: step has empty name"
                assert step.agent_role, f"{wf_type}: step {step.name} has no agent_role"


class TestSelectWorkflow:
    def test_select_feature_development(self):
        wf = select_workflow(WorkflowType.FEATURE_DEVELOPMENT)
        assert wf.name == "Feature Development"

    def test_select_bugfix(self):
        wf = select_workflow(WorkflowType.BUGFIX)
        assert wf.name == "Bugfix"

    def test_custom_raises_error(self):
        with pytest.raises(ValueError, match="Custom"):
            select_workflow(WorkflowType.CUSTOM)


class TestParseCustomWorkflow:
    def test_parse_simple_workflow(self):
        definition = """
STEP: Analysis
  agent: Product Manager
  inputs:
  outputs: prd
  next: Build

STEP: Build
  agent: Backend Engineer
  inputs: prd
  outputs:
  parallel: true
"""
        wf = parse_custom_workflow(definition, name="Simple")
        assert wf.name == "Simple"
        assert wf.workflow_type == WorkflowType.CUSTOM
        assert len(wf.steps) == 2
        assert wf.steps[0].name == "Analysis"
        assert wf.steps[0].agent_role == AgentRole.PRODUCT_MANAGER
        assert wf.steps[1].parallel is True

    def test_parse_with_gate(self):
        definition = """
STEP: Deploy
  agent: DevOps Engineer
  gate: approval
"""
        wf = parse_custom_workflow(definition)
        assert wf.steps[0].gate == "approval"

    def test_parse_with_on_fail(self):
        definition = """
STEP: Review
  agent: Backend Code Reviewer
  on_fail: Implementation
"""
        wf = parse_custom_workflow(definition)
        assert wf.steps[0].on_fail == "Implementation"

    def test_empty_definition_raises(self):
        with pytest.raises(ValueError, match="No workflow steps"):
            parse_custom_workflow("")

    def test_unknown_role_raises(self):
        definition = """
STEP: Bad Step
  agent: Unknown Role
"""
        with pytest.raises(ValueError, match="Unknown agent role"):
            parse_custom_workflow(definition)
