"""Tests for verdict gate logic in WorkflowEngine.

Covers:
- _check_verdict_gate: detects negative verdicts in review, QA, security artifacts
- _archive_verdict_artifact: renames rejected artifacts for audit trail
- _handle_verdict_rework: runs fix→re-review cycles on negative verdicts
- Integration with execute() main loop: verdict_rejected triggers rework
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestrator.models import (
    AgentRole,
    OrchestratorConfig,
    PhaseState,
    PhaseStatus,
    QAVerdict,
    ReviewVerdict,
    RunState,
    SecurityVerdict,
    WorkflowStepDefinition,
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
        run_id="test-verdict-001",
        feature_request="Build a todo app",
        workspace_dir=str(workspace),
    )


@pytest.fixture
def review_workflow() -> WorkflowDefinition:
    return WorkflowDefinition(
        name="Test Review Workflow",
        workflow_type=WorkflowType.FEATURE_DEVELOPMENT,
        steps=[
            WorkflowStepDefinition(
                name="Implementation",
                agent_role=AgentRole.BACKEND_ENGINEER,
                inputs=[],
                outputs=[],
                next="Code Review",
                parallel=True,
            ),
            WorkflowStepDefinition(
                name="Code Review",
                agent_role=AgentRole.BACKEND_CODE_REVIEWER,
                inputs=[],
                outputs=["review"],
                next="QA",
                on_fail="Implementation",
            ),
            WorkflowStepDefinition(
                name="QA",
                agent_role=AgentRole.QA_EXECUTOR,
                inputs=[],
                outputs=["qa_report"],
                on_fail="Implementation",
            ),
        ],
    )


@pytest.fixture
def engine(review_workflow, run_state, workspace) -> WorkflowEngine:
    config = OrchestratorConfig()
    return WorkflowEngine(
        workflow=review_workflow,
        state=run_state,
        config=config,
        project_root=workspace,
    )


@pytest.fixture
def review_step(review_workflow) -> WorkflowStepDefinition:
    return next(s for s in review_workflow.steps if s.name == "Code Review")


@pytest.fixture
def qa_step(review_workflow) -> WorkflowStepDefinition:
    return next(s for s in review_workflow.steps if s.name == "QA")


# ---------------------------------------------------------------------------
# Helper: write artifact JSON to disk
# ---------------------------------------------------------------------------


def _write_artifact(workspace: Path, name: str, data: dict) -> Path:
    path = workspace / "artifacts" / f"{name}.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Tests: _check_verdict_gate
# ---------------------------------------------------------------------------


class TestCheckVerdictGate:
    """Tests for _check_verdict_gate — detects negative verdicts."""

    def test_returns_none_when_no_verdict_artifacts(self, engine, workspace):
        """Steps with no verdict-bearing outputs pass unconditionally."""
        step = WorkflowStepDefinition(
            name="Implementation",
            agent_role=AgentRole.BACKEND_ENGINEER,
            outputs=[],
        )
        assert engine._check_verdict_gate(step, workspace) is None

    def test_returns_none_when_review_approved(self, engine, workspace, review_step):
        _write_artifact(workspace, "review", {
            "verdict": "approve",
            "summary": "Code looks great, well structured.",
            "issues": [],
        })
        assert engine._check_verdict_gate(review_step, workspace) is None

    def test_accepts_review_pass_with_warnings_when_only_nit(self, engine, workspace, review_step):
        """pass_with_warnings with only nit issues — no rework needed."""
        _write_artifact(workspace, "review", {
            "verdict": "pass_with_warnings",
            "summary": "Minor issues noted but acceptable.",
            "issues": [{"severity": "nit", "description": "Consider renaming var"}],
        })
        assert engine._check_verdict_gate(review_step, workspace) is None

    def test_returns_error_when_review_rejected(self, engine, workspace, review_step):
        _write_artifact(workspace, "review", {
            "verdict": "reject",
            "summary": "Critical security flaw in auth handler.",
            "issues": [
                {"severity": "critical", "file": "auth.py", "line": 42, "description": "SQL injection"},
            ],
        })
        result = engine._check_verdict_gate(review_step, workspace)
        assert result is not None
        assert "reject" in result
        assert "1 issue(s)" in result

    def test_returns_error_when_review_request_changes(self, engine, workspace, review_step):
        _write_artifact(workspace, "review", {
            "verdict": "request_changes",
            "summary": "Several improvements needed before merge.",
            "issues": [
                {"severity": "major", "description": "Missing error handling"},
                {"severity": "minor", "description": "Dead import"},
            ],
        })
        result = engine._check_verdict_gate(review_step, workspace)
        assert result is not None
        assert "request_changes" in result
        assert "2 issue(s)" in result

    def test_normalizes_aicoder_verdict_pass(self, engine, workspace, review_step):
        """AICoder uses 'pass' instead of 'approve' — must be normalized."""
        _write_artifact(workspace, "review", {
            "verdict": "pass",
            "summary": "All checks pass, code is clean.",
            "issues": [],
        })
        assert engine._check_verdict_gate(review_step, workspace) is None

    def test_normalizes_aicoder_verdict_fail(self, engine, workspace, review_step):
        """AICoder uses 'fail' instead of 'reject' — must be normalized."""
        _write_artifact(workspace, "review", {
            "verdict": "fail",
            "summary": "Tests are failing after changes.",
            "issues": [],
        })
        result = engine._check_verdict_gate(review_step, workspace)
        assert result is not None
        assert "reject" in result

    def test_accepts_review_reject_when_only_minor_issues(self, engine, workspace, review_step):
        """Reject verdict with only minor issues — no rework, move on."""
        _write_artifact(workspace, "review", {
            "verdict": "reject",
            "summary": "A few style issues but nothing serious.",
            "issues": [
                {"severity": "minor", "description": "Dead import on line 3"},
                {"severity": "nit", "description": "Consider using f-string"},
            ],
        })
        assert engine._check_verdict_gate(review_step, workspace) is None

    def test_blocks_review_reject_when_mixed_severities(self, engine, workspace, review_step):
        """Reject with a mix of minor and major — must block."""
        _write_artifact(workspace, "review", {
            "verdict": "reject",
            "summary": "Major issue plus minor nits.",
            "issues": [
                {"severity": "minor", "description": "Unused variable"},
                {"severity": "major", "description": "Missing input validation"},
            ],
        })
        result = engine._check_verdict_gate(review_step, workspace)
        assert result is not None
        assert "reject" in result

    def test_blocks_review_reject_with_empty_issues(self, engine, workspace, review_step):
        """Reject with no issues listed — trust the verdict, block."""
        _write_artifact(workspace, "review", {
            "verdict": "reject",
            "summary": "Fundamental design problem, needs rethink.",
            "issues": [],
        })
        result = engine._check_verdict_gate(review_step, workspace)
        assert result is not None
        assert "reject" in result

    def test_returns_none_when_qa_passes(self, engine, workspace, qa_step):
        _write_artifact(workspace, "qa_report", {
            "test_results": {"passed": 10, "failed": 0, "skipped": 0},
            "verdict": "pass",
            "issues": [],
        })
        assert engine._check_verdict_gate(qa_step, workspace) is None

    def test_returns_error_when_qa_fails(self, engine, workspace, qa_step):
        _write_artifact(workspace, "qa_report", {
            "test_results": {"passed": 8, "failed": 2, "skipped": 0},
            "verdict": "fail",
            "issues": [
                {"severity": "critical", "description": "test_login fails"},
                {"severity": "major", "description": "test_signup fails"},
            ],
        })
        result = engine._check_verdict_gate(qa_step, workspace)
        assert result is not None
        assert "fail" in result
        assert "2 issue(s)" in result

    def test_threat_model_pass(self, engine, workspace):
        step = WorkflowStepDefinition(
            name="Threat Model",
            agent_role=AgentRole.SECURITY_ENGINEER,
            outputs=["threat_model"],
        )
        _write_artifact(workspace, "threat_model", {
            "threats": [{"id": "THREAT-001", "description": "XSS via user input fields", "severity": "major", "mitigation": "Sanitize all inputs"}],
            "attack_surface": "Web application with user-facing forms and API endpoints",
            "recommendations": ["Enable CSP headers"],
            "verdict": "pass",
        })
        assert engine._check_verdict_gate(step, workspace) is None

    def test_threat_model_fail(self, engine, workspace):
        step = WorkflowStepDefinition(
            name="Threat Model",
            agent_role=AgentRole.SECURITY_ENGINEER,
            outputs=["threat_model"],
        )
        _write_artifact(workspace, "threat_model", {
            "threats": [{"id": "THREAT-001", "description": "Unmitigated RCE via deserialization", "severity": "critical", "mitigation": "None identified"}],
            "attack_surface": "API accepts serialized objects from untrusted sources",
            "recommendations": ["Block deserialization of untrusted data"],
            "verdict": "fail",
        })
        result = engine._check_verdict_gate(step, workspace)
        assert result is not None
        assert "fail" in result

    def test_vulnerability_report_pass(self, engine, workspace):
        step = WorkflowStepDefinition(
            name="Verification",
            agent_role=AgentRole.SECURITY_ENGINEER,
            outputs=["vulnerability_report"],
        )
        _write_artifact(workspace, "vulnerability_report", {
            "vulnerabilities": [],
            "scan_tools_used": ["bandit"],
            "summary": "No vulnerabilities found after remediation.",
            "verdict": "pass",
        })
        assert engine._check_verdict_gate(step, workspace) is None

    def test_vulnerability_report_fail(self, engine, workspace):
        step = WorkflowStepDefinition(
            name="Verification",
            agent_role=AgentRole.SECURITY_ENGINEER,
            outputs=["vulnerability_report"],
        )
        _write_artifact(workspace, "vulnerability_report", {
            "vulnerabilities": [
                {"id": "V-001", "severity": "critical", "file": "app.py",
                 "description": "Hardcoded credentials found", "fix": "Use env vars"},
            ],
            "scan_tools_used": ["bandit"],
            "summary": "Critical vulnerability still present.",
            "verdict": "fail",
        })
        result = engine._check_verdict_gate(step, workspace)
        assert result is not None
        assert "fail" in result

    def test_env_setup_report_fail(self, engine, workspace):
        step = WorkflowStepDefinition(
            name="Env Setup",
            agent_role=AgentRole.ENV_SETUP_ENGINEER,
            outputs=["env_setup_report"],
        )
        _write_artifact(workspace, "env_setup_report", {
            "docker_compose_written": False,
            "seed_script_written": False,
            "verdict": "fail",
            "issues": ["Docker compose generation failed"],
        })
        result = engine._check_verdict_gate(step, workspace)
        assert result is not None
        assert "fail" in result

    def test_fixer_report_escalate(self, engine, workspace):
        step = WorkflowStepDefinition(
            name="Fix",
            agent_role=AgentRole.BACKEND_ENGINEER,
            outputs=["fixer_report"],
        )
        _write_artifact(workspace, "fixer_report", {
            "failed_step": "QA",
            "error_summary": "Cannot resolve import error",
            "root_cause_category": "dependency",
            "root_cause_description": "Missing package",
            "fix_description": "Attempted install but version conflict",
            "confidence": 0.2,
            "verdict": "escalate",
        })
        result = engine._check_verdict_gate(step, workspace)
        assert result is not None
        assert "escalate" in result

    def test_returns_none_when_artifact_file_missing(self, engine, workspace, review_step):
        """Missing artifact file should not block — artifact validation handles that."""
        assert engine._check_verdict_gate(review_step, workspace) is None

    def test_returns_none_when_verdict_field_missing(self, engine, workspace, review_step):
        """Artifact without verdict field passes (backward compat)."""
        _write_artifact(workspace, "review", {
            "summary": "Old-format review without verdict field.",
            "issues": [],
        })
        assert engine._check_verdict_gate(review_step, workspace) is None


# ---------------------------------------------------------------------------
# Tests: _archive_verdict_artifact
# ---------------------------------------------------------------------------


class TestArchiveVerdictArtifact:
    def test_renames_artifact_with_cycle_number(self, engine, workspace):
        _write_artifact(workspace, "review", {"verdict": "reject", "summary": "x" * 20, "issues": []})
        archive_path = engine._archive_verdict_artifact("review", workspace, cycle=0)
        assert archive_path is not None
        assert archive_path.name == "review-cycle-0.json"
        assert archive_path.exists()
        assert not (workspace / "artifacts" / "review.json").exists()

    def test_returns_none_when_artifact_missing(self, engine, workspace):
        result = engine._archive_verdict_artifact("review", workspace, cycle=0)
        assert result is None

    def test_multiple_cycles_produce_sequential_archives(self, engine, workspace):
        for cycle in range(3):
            _write_artifact(workspace, "review", {"verdict": "reject", "summary": "x" * 20, "issues": []})
            engine._archive_verdict_artifact("review", workspace, cycle=cycle)

        assert (workspace / "artifacts" / "review-cycle-0.json").exists()
        assert (workspace / "artifacts" / "review-cycle-1.json").exists()
        assert (workspace / "artifacts" / "review-cycle-2.json").exists()


# ---------------------------------------------------------------------------
# Tests: _handle_verdict_rework
# ---------------------------------------------------------------------------


class TestHandleVerdictRework:
    """Tests for the fix→re-review rework loop."""

    @pytest.mark.asyncio
    async def test_rework_succeeds_after_one_cycle(self, engine, workspace, review_step):
        """Fix agent resolves issues, re-evaluation approves on first rework cycle."""
        _write_artifact(workspace, "review", {
            "verdict": "reject",
            "summary": "SQL injection in auth handler must be fixed.",
            "issues": [{"severity": "critical", "description": "SQL injection"}],
        })

        async def mock_invoke_agent(invocation, run_state=None):
            if "-reeval-" in invocation.display_name:
                _write_artifact(workspace, "review", {
                    "verdict": "approve",
                    "summary": "All issues resolved, code looks good now.",
                    "issues": [],
                })
            return MagicMock(success=True, cost_usd=0.01, input_tokens=100, output_tokens=50, output="")

        with patch("orchestrator.workflow_engine.invoke_agent", side_effect=mock_invoke_agent):
            result = await engine._handle_verdict_rework(review_step)

        assert result is True
        # Original review should be archived
        assert (workspace / "artifacts" / "review-cycle-0.json").exists()

    @pytest.mark.asyncio
    async def test_rework_exhausts_max_cycles(self, engine, workspace, review_step):
        """When all rework cycles fail, returns False."""
        engine.config.max_review_cycles = 2

        _write_artifact(workspace, "review", {
            "verdict": "reject",
            "summary": "Persistent issue that fix agent cannot resolve.",
            "issues": [{"severity": "critical", "description": "Unresolvable bug"}],
        })

        async def mock_invoke_agent(invocation, run_state=None):
            if "-reeval-" in invocation.display_name:
                _write_artifact(workspace, "review", {
                    "verdict": "reject",
                    "summary": "Same issue persists after attempted fix.",
                    "issues": [{"severity": "critical", "description": "Unresolvable bug"}],
                })
            return MagicMock(success=True, cost_usd=0.01, input_tokens=100, output_tokens=50, output="")

        with patch("orchestrator.workflow_engine.invoke_agent", side_effect=mock_invoke_agent):
            result = await engine._handle_verdict_rework(review_step)

        assert result is False
        phase_key = engine._step_to_phase_key(review_step)
        assert engine.state.phases[phase_key].status == PhaseStatus.FAILED
        assert "2 rework cycle(s)" in engine.state.phases[phase_key].error

    @pytest.mark.asyncio
    async def test_rework_stops_on_fix_agent_failure(self, engine, workspace, review_step):
        """If the fix agent itself crashes, rework stops immediately."""
        # Initialize phase state (normally done by _execute_step before verdict check)
        phase_key = engine._step_to_phase_key(review_step)
        engine.state.phases[phase_key] = PhaseState(status=PhaseStatus.RUNNING)

        _write_artifact(workspace, "review", {
            "verdict": "reject",
            "summary": "Code needs changes but fix agent will fail.",
            "issues": [{"severity": "major", "description": "Logic error"}],
        })

        async def mock_invoke_agent(invocation, run_state=None):
            return MagicMock(success=False, cost_usd=0.01, input_tokens=100, output_tokens=50)

        with patch("orchestrator.workflow_engine.invoke_agent", side_effect=mock_invoke_agent):
            result = await engine._handle_verdict_rework(review_step)

        assert result is False
        assert engine.state.phases[phase_key].status == PhaseStatus.FAILED

    @pytest.mark.asyncio
    async def test_rework_succeeds_on_second_cycle(self, engine, workspace, qa_step):
        """QA fails first rework, passes on second rework cycle."""
        engine.config.max_review_cycles = 3

        _write_artifact(workspace, "qa_report", {
            "test_results": {"passed": 5, "failed": 2, "skipped": 0},
            "verdict": "fail",
            "issues": [
                {"severity": "critical", "description": "test_login fails"},
                {"severity": "major", "description": "test_signup fails"},
            ],
        })

        reeval_call_count = [0]

        async def mock_invoke_agent(invocation, run_state=None):
            if "-reeval-" in invocation.display_name:
                reeval_call_count[0] += 1
                if reeval_call_count[0] == 1:
                    # First re-evaluation: still failing
                    _write_artifact(workspace, "qa_report", {
                        "test_results": {"passed": 6, "failed": 1, "skipped": 0},
                        "verdict": "fail",
                        "issues": [{"severity": "major", "description": "test_signup still fails"}],
                    })
                else:
                    # Second re-evaluation: passes
                    _write_artifact(workspace, "qa_report", {
                        "test_results": {"passed": 7, "failed": 0, "skipped": 0},
                        "verdict": "pass",
                        "issues": [],
                    })
            return MagicMock(success=True, cost_usd=0.01, input_tokens=100, output_tokens=50, output="")

        with patch("orchestrator.workflow_engine.invoke_agent", side_effect=mock_invoke_agent):
            result = await engine._handle_verdict_rework(qa_step)

        assert result is True
        # Both cycles should have archived artifacts
        assert (workspace / "artifacts" / "qa_report-cycle-0.json").exists()
        assert (workspace / "artifacts" / "qa_report-cycle-1.json").exists()

    @pytest.mark.asyncio
    async def test_rework_stops_on_hard_failure(self, engine, workspace, review_step):
        """If re-evaluation agent hard-fails (not verdict_rejected), rework stops."""
        _write_artifact(workspace, "review", {
            "verdict": "reject",
            "summary": "Issues found but re-evaluation will crash.",
            "issues": [{"severity": "major", "description": "Bug"}],
        })

        async def mock_invoke_agent(invocation, run_state=None):
            if "-reeval-" in invocation.display_name:
                # Re-evaluation agent crashes
                return MagicMock(success=False, cost_usd=0.0, input_tokens=0, output_tokens=0, output="")
            return MagicMock(success=True, cost_usd=0.01, input_tokens=100, output_tokens=50, output="")

        with patch("orchestrator.workflow_engine.invoke_agent", side_effect=mock_invoke_agent):
            result = await engine._handle_verdict_rework(review_step)

        assert result is False


# ---------------------------------------------------------------------------
# Tests: _build_fix_prompt
# ---------------------------------------------------------------------------


class TestBuildFixPrompt:
    def test_contains_targeted_fix_instruction(self, engine, workspace, review_step):
        feedback = {
            "verdict": "reject",
            "summary": "SQL injection in login handler.",
            "issues": [
                {"severity": "critical", "file": "auth.py", "line": 42,
                 "description": "SQL injection via user input",
                 "suggestion": "Use parameterized queries"},
            ],
        }
        prompt = engine._build_fix_prompt(review_step, workspace, feedback, "review", cycle=0)

        assert "Do NOT re-implement" in prompt
        assert "targeted fixes" in prompt.lower() or "Targeted Fix" in prompt
        assert "SQL injection" in prompt
        assert "auth.py" in prompt
        assert "parameterized queries" in prompt
        assert "Rework Cycle 1" in prompt

    def test_includes_test_results_for_qa(self, engine, workspace, qa_step):
        feedback = {
            "verdict": "fail",
            "summary": "Two test failures detected.",
            "test_results": {"passed": 8, "failed": 2, "skipped": 1},
            "issues": [
                {"severity": "critical", "description": "test_login assertion error"},
            ],
        }
        prompt = engine._build_fix_prompt(qa_step, workspace, feedback, "qa_report", cycle=0)

        assert "Passed: 8" in prompt
        assert "Failed: 2" in prompt
        assert "test_login" in prompt

    def test_handles_empty_issues_list(self, engine, workspace, review_step):
        feedback = {
            "verdict": "reject",
            "summary": "General quality concerns with the implementation.",
            "issues": [],
        }
        prompt = engine._build_fix_prompt(review_step, workspace, feedback, "review", cycle=0)

        assert "General quality concerns" in prompt
        assert "Do NOT re-implement" in prompt
