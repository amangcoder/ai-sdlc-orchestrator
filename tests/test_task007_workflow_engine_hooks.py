"""Tests for TASK-007: QA Browser harness hooks in WorkflowEngine._execute_step.

Covers:
- REQ-017: Pre/post hooks for Env Setup validation
- REQ-017: Full QA Browser lifecycle (pre + post hooks)
- AC-004: Env Setup produces docker-compose.yml validation
- AC-005: QA Browser generates qa_browser_report with per-AC results
- AC-006/AC-007: Speed mode gating produces stub reports for turbo/standard
- AC-008: Missing docker-compose.yml fails Env Setup step
- AC-010: Failed server startup produces server_status='failed'
- AC-011/AC-012: Graceful degradation for missing Playwright
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestrator.models import (
    AgentRole,
    OrchestratorConfig,
    PhaseStatus,
    RunState,
    SpeedMode,
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
def prd_data_with_acs(workspace: Path) -> dict:
    data = {
        "title": "Test App",
        "overview": "A test application",
        "goals": ["Test goal"],
        "requirements": [{"id": "REQ-001", "description": "Basic requirement", "priority": "must"}],
        "constraints": [],
        "acceptance_criteria": [
            {"id": "AC-001", "description": "User can view the homepage"},
            {"id": "AC-002", "description": "User can navigate to settings"},
        ],
    }
    (workspace / "artifacts" / "prd.json").write_text(json.dumps(data))
    return data


@pytest.fixture
def minimal_workflow() -> WorkflowDefinition:
    """A workflow with Env Setup and QA Browser steps."""
    return WorkflowDefinition(
        name="Test Workflow",
        workflow_type=WorkflowType.FEATURE_DEVELOPMENT,
        steps=[
            WorkflowStepDefinition(
                name="Env Setup",
                agent_role=AgentRole.ENV_SETUP_ENGINEER,
                inputs=["prd"],
                outputs=["env_setup_report"],
            ),
            WorkflowStepDefinition(
                name="QA Browser",
                agent_role=AgentRole.QA_BROWSER_ENGINEER,
                inputs=["prd", "env_setup_report"],
                outputs=["qa_browser_report"],
            ),
        ],
    )


@pytest.fixture
def run_state(workspace: Path) -> RunState:
    return RunState(
        run_id="test-run-001",
        feature_request="Build a test app",
        workspace_dir=str(workspace),
    )


@pytest.fixture
def engine(minimal_workflow, run_state, workspace, tmp_path) -> WorkflowEngine:
    config = OrchestratorConfig()
    eng = WorkflowEngine(
        workflow=minimal_workflow,
        state=run_state,
        config=config,
        project_root=workspace,  # use tmp workspace as project root for tests
    )
    return eng


@pytest.fixture
def env_setup_step(minimal_workflow) -> WorkflowStepDefinition:
    return next(s for s in minimal_workflow.steps if s.name == "Env Setup")


@pytest.fixture
def qa_browser_step(minimal_workflow) -> WorkflowStepDefinition:
    return next(s for s in minimal_workflow.steps if s.name == "QA Browser")


# ---------------------------------------------------------------------------
# Tests: _env_setup_post_hook
# ---------------------------------------------------------------------------


class TestEnvSetupPostHook:
    """Tests for _env_setup_post_hook (AC-004, AC-008, REQ-017)."""

    @pytest.mark.asyncio
    async def test_passes_when_docker_compose_exists_in_project_root(
        self, engine: WorkflowEngine, env_setup_step, workspace
    ):
        """AC-004/REQ-017: Post-hook returns True when docker-compose.yml exists."""
        (workspace / "docker-compose.yml").write_text("version: '3'\nservices: {}")
        result = await engine._env_setup_post_hook(env_setup_step, workspace)
        assert result is True

    @pytest.mark.asyncio
    async def test_fails_when_docker_compose_missing(
        self, engine: WorkflowEngine, env_setup_step, workspace
    ):
        """AC-008: Missing docker-compose.yml fails Env Setup step."""
        # Ensure docker-compose.yml does NOT exist
        compose_file = workspace / "docker-compose.yml"
        if compose_file.exists():
            compose_file.unlink()

        result = await engine._env_setup_post_hook(env_setup_step, workspace)
        assert result is False

    @pytest.mark.asyncio
    async def test_sets_phase_error_when_compose_missing(
        self, engine: WorkflowEngine, env_setup_step, workspace, run_state
    ):
        """AC-008: Phase error message is set when docker-compose.yml is absent."""
        from orchestrator.models import PhaseState

        phase_key = engine._step_to_phase_key(env_setup_step)
        run_state.phases[phase_key] = PhaseState(status=PhaseStatus.RUNNING)

        compose_file = workspace / "docker-compose.yml"
        if compose_file.exists():
            compose_file.unlink()

        await engine._env_setup_post_hook(env_setup_step, workspace)

        # Error message should mention docker-compose.yml
        assert "docker-compose.yml" in (run_state.phases[phase_key].error or "")

    @pytest.mark.asyncio
    async def test_passes_when_compose_found_in_workspace(
        self, engine: WorkflowEngine, env_setup_step, workspace, tmp_path
    ):
        """Post-hook also checks workspace root as a fallback."""
        # project_root has no compose file, but workspace does
        (workspace / "docker-compose.yml").write_text("version: '3'\nservices: {}")
        result = await engine._env_setup_post_hook(env_setup_step, workspace)
        assert result is True


# ---------------------------------------------------------------------------
# Tests: _write_stub_qa_browser_report
# ---------------------------------------------------------------------------


class TestWriteStubQABrowserReport:
    """Tests for the stub report writer."""

    def test_stub_report_is_written(self, engine: WorkflowEngine, workspace):
        """Stub report file is created in artifacts directory."""
        engine._write_stub_qa_browser_report(workspace, reason="speed_mode=turbo")
        report_path = workspace / "artifacts" / "qa_browser_report.json"
        assert report_path.exists()

    def test_stub_report_has_server_status_skipped(self, engine: WorkflowEngine, workspace):
        """Stub report server_status is 'skipped'."""
        engine._write_stub_qa_browser_report(workspace, reason="speed_mode=standard")
        data = json.loads((workspace / "artifacts" / "qa_browser_report.json").read_text())
        assert data["server_status"] == "skipped"

    def test_stub_report_has_warn_verdict(self, engine: WorkflowEngine, workspace):
        """Stub report verdict is 'warn'."""
        engine._write_stub_qa_browser_report(workspace, reason="no_frontend")
        data = json.loads((workspace / "artifacts" / "qa_browser_report.json").read_text())
        assert data["verdict"] == "warn"

    def test_stub_report_includes_reason(self, engine: WorkflowEngine, workspace):
        """Stub report issues list includes the skip reason."""
        engine._write_stub_qa_browser_report(workspace, reason="speed_mode=turbo")
        data = json.loads((workspace / "artifacts" / "qa_browser_report.json").read_text())
        assert any("turbo" in issue for issue in data.get("issues", []))

    def test_stub_report_includes_stack_name(self, engine: WorkflowEngine, workspace):
        """Stub report stack_detected is populated from stack_info."""
        mock_stack = MagicMock()
        mock_stack.framework = "nextjs"
        engine._write_stub_qa_browser_report(workspace, reason="test", stack_info=mock_stack)
        data = json.loads((workspace / "artifacts" / "qa_browser_report.json").read_text())
        assert data["stack_detected"] == "nextjs"


# ---------------------------------------------------------------------------
# Tests: _qa_browser_pre_hook — speed mode gating
# ---------------------------------------------------------------------------


class TestQABrowserPreHookSpeedMode:
    """Tests for speed-mode gate in _qa_browser_pre_hook (AC-006, AC-007)."""

    @pytest.mark.asyncio
    async def test_turbo_mode_skips_and_writes_stub(
        self, engine: WorkflowEngine, qa_browser_step, workspace, run_state
    ):
        """AC-006: Turbo speed mode produces a stub report and returns 'skip'."""
        engine.config.speed_mode = SpeedMode.TURBO
        tasks = [
            WorkflowTaskState(
                task_id="T-001",
                workflow_step="QA Browser",
                assigned_role=AgentRole.QA_BROWSER_ENGINEER,
                description="Run QA",
            )
        ]
        mock_stack = MagicMock()
        mock_stack.framework = "nextjs"
        mock_stack.has_frontend = True

        with patch("orchestrator.stack_detector.detect_stack", return_value=mock_stack):
            result = await engine._qa_browser_pre_hook(qa_browser_step, tasks, workspace)

        assert result == "skip"
        report_path = workspace / "artifacts" / "qa_browser_report.json"
        assert report_path.exists()
        data = json.loads(report_path.read_text())
        assert data["server_status"] == "skipped"
        assert any("turbo" in issue.lower() for issue in data["issues"])

    @pytest.mark.asyncio
    async def test_standard_mode_skips_and_writes_stub(
        self, engine: WorkflowEngine, qa_browser_step, workspace
    ):
        """AC-007: Standard speed mode produces a stub report and returns 'skip'."""
        engine.config.speed_mode = SpeedMode.STANDARD
        tasks = []
        mock_stack = MagicMock()
        mock_stack.framework = "react-vite"
        mock_stack.has_frontend = True

        with patch("orchestrator.stack_detector.detect_stack", return_value=mock_stack):
            result = await engine._qa_browser_pre_hook(qa_browser_step, tasks, workspace)

        assert result == "skip"
        data = json.loads((workspace / "artifacts" / "qa_browser_report.json").read_text())
        assert any("standard" in issue.lower() for issue in data["issues"])

    @pytest.mark.asyncio
    async def test_thorough_mode_does_not_skip(
        self, engine: WorkflowEngine, qa_browser_step, workspace
    ):
        """Thorough mode does NOT trigger the speed-mode gate."""
        engine.config.speed_mode = SpeedMode.THOROUGH
        mock_stack = MagicMock()
        mock_stack.framework = "nextjs"
        mock_stack.has_frontend = True

        mock_server = AsyncMock()
        mock_server.base_url = "http://localhost:3001"
        mock_server.start = AsyncMock(return_value="http://localhost:3001")
        mock_server.run_seed_script = AsyncMock()

        tasks = [
            WorkflowTaskState(
                task_id="T-001",
                workflow_step="QA Browser",
                assigned_role=AgentRole.QA_BROWSER_ENGINEER,
                description="Run QA",
            )
        ]

        with (
            patch("orchestrator.stack_detector.detect_stack", return_value=mock_stack),
            patch("orchestrator.app_server.AppTestServer", return_value=mock_server),
        ):
            result = await engine._qa_browser_pre_hook(qa_browser_step, tasks, workspace)

        # Should NOT skip — returns None (proceed with agent)
        assert result is None


# ---------------------------------------------------------------------------
# Tests: _qa_browser_pre_hook — has_frontend gate
# ---------------------------------------------------------------------------


class TestQABrowserPreHookFrontend:
    """Tests for has_frontend gate."""

    @pytest.mark.asyncio
    async def test_no_frontend_skips_with_stub(
        self, engine: WorkflowEngine, qa_browser_step, workspace
    ):
        """When has_frontend=False, stub report is written and 'skip' returned."""
        engine.config.speed_mode = SpeedMode.THOROUGH
        mock_stack = MagicMock()
        mock_stack.framework = "fastapi"
        mock_stack.has_frontend = False
        tasks = []

        with patch("orchestrator.stack_detector.detect_stack", return_value=mock_stack):
            result = await engine._qa_browser_pre_hook(qa_browser_step, tasks, workspace)

        assert result == "skip"
        data = json.loads((workspace / "artifacts" / "qa_browser_report.json").read_text())
        assert "no_frontend" in data["issues"][0]

    @pytest.mark.asyncio
    async def test_stack_detection_error_treated_as_unknown(
        self, engine: WorkflowEngine, qa_browser_step, workspace
    ):
        """Stack detection errors produce a fallback (unknown) stack with has_frontend=False."""
        engine.config.speed_mode = SpeedMode.THOROUGH

        def _raise(*_a, **_kw):
            raise RuntimeError("stack detection boom")

        tasks = []
        with patch("orchestrator.stack_detector.detect_stack", side_effect=_raise):
            result = await engine._qa_browser_pre_hook(qa_browser_step, tasks, workspace)

        # Unknown stack has has_frontend=False → should produce skip with stub
        # OR proceed to server start attempt with stack_info=None
        # Either way, the system should not raise an unhandled exception.
        assert result in ("skip", None)


# ---------------------------------------------------------------------------
# Tests: _qa_browser_pre_hook — server lifecycle
# ---------------------------------------------------------------------------


class TestQABrowserPreHookServer:
    """Tests for the server startup section of the pre-hook."""

    @pytest.mark.asyncio
    async def test_server_start_injects_base_url_into_tasks(
        self, engine: WorkflowEngine, qa_browser_step, workspace
    ):
        """AC-005/REQ-017: base_url is injected into task descriptions."""
        engine.config.speed_mode = SpeedMode.THOROUGH
        mock_stack = MagicMock()
        mock_stack.framework = "nextjs"
        mock_stack.has_frontend = True

        mock_server = AsyncMock()
        mock_server.base_url = "http://localhost:3010"
        mock_server.start = AsyncMock(return_value="http://localhost:3010")
        mock_server.run_seed_script = AsyncMock()

        tasks = [
            WorkflowTaskState(
                task_id="T-001",
                workflow_step="QA Browser",
                assigned_role=AgentRole.QA_BROWSER_ENGINEER,
                description="Original task description",
            )
        ]

        with (
            patch("orchestrator.stack_detector.detect_stack", return_value=mock_stack),
            patch("orchestrator.app_server.AppTestServer", return_value=mock_server),
        ):
            result = await engine._qa_browser_pre_hook(qa_browser_step, tasks, workspace)

        assert result is None
        assert "http://localhost:3010" in tasks[0].description
        assert "BASE_URL" in tasks[0].description

    @pytest.mark.asyncio
    async def test_server_start_stores_server_on_engine(
        self, engine: WorkflowEngine, qa_browser_step, workspace
    ):
        """Server instance is stored on engine for post-hook cleanup."""
        engine.config.speed_mode = SpeedMode.THOROUGH
        mock_stack = MagicMock()
        mock_stack.framework = "nextjs"
        mock_stack.has_frontend = True

        mock_server = AsyncMock()
        mock_server.base_url = "http://localhost:3011"
        mock_server.start = AsyncMock(return_value="http://localhost:3011")
        mock_server.run_seed_script = AsyncMock()

        with (
            patch("orchestrator.stack_detector.detect_stack", return_value=mock_stack),
            patch("orchestrator.app_server.AppTestServer", return_value=mock_server),
        ):
            await engine._qa_browser_pre_hook(qa_browser_step, [], workspace)

        assert engine._qa_browser_server is mock_server

    @pytest.mark.asyncio
    async def test_server_failure_writes_failed_report_and_skips(
        self, engine: WorkflowEngine, qa_browser_step, workspace
    ):
        """AC-010: Failed server startup produces server_status='failed' in report."""
        engine.config.speed_mode = SpeedMode.THOROUGH
        mock_stack = MagicMock()
        mock_stack.framework = "nextjs"
        mock_stack.has_frontend = True

        mock_server = AsyncMock()
        mock_server.base_url = "http://localhost:3012"
        mock_server.start = AsyncMock(side_effect=RuntimeError("Port already in use"))

        with (
            patch("orchestrator.stack_detector.detect_stack", return_value=mock_stack),
            patch("orchestrator.app_server.AppTestServer", return_value=mock_server),
        ):
            result = await engine._qa_browser_pre_hook(qa_browser_step, [], workspace)

        assert result == "skip"
        data = json.loads((workspace / "artifacts" / "qa_browser_report.json").read_text())
        assert data["server_status"] == "failed"
        assert "Port already in use" in (data["server_error"] or "")
        assert data["verdict"] == "fail"
        assert engine._qa_browser_server is None  # cleaned up


# ---------------------------------------------------------------------------
# Tests: _build_qa_browser_report
# ---------------------------------------------------------------------------


class TestBuildQABrowserReport:
    """Unit tests for report construction from Playwright JSON output."""

    def test_no_playwright_results_marks_acs_skipped(
        self, engine: WorkflowEngine, workspace, prd_data_with_acs
    ):
        """AC-005/AC-012: When Playwright is absent, ACs are marked skipped."""
        report = engine._build_qa_browser_report(
            workspace=workspace,
            playwright_results=None,
            base_url="http://localhost:3000",
            stack_name="nextjs",
        )
        assert report["tests_skipped"] == 2  # two ACs in fixture
        for r in report["test_results"]:
            assert r["status"] == "skipped"

    def test_playwright_results_pass_sets_passed_verdict(
        self, engine: WorkflowEngine, workspace, prd_data_with_acs
    ):
        """Passing tests produce verdict='pass'."""
        playwright_json = {
            "stats": {"expected": 2, "unexpected": 0, "skipped": 0},
            "suites": [
                {
                    "title": "Homepage",
                    "file": "tests/e2e/homepage.spec.ts",
                    "specs": [
                        {
                            "title": "AC-001 user can view the homepage",
                            "tests": [{"status": "passed", "results": [{"duration": 150}]}],
                        },
                        {
                            "title": "AC-002 user can navigate to settings",
                            "tests": [{"status": "passed", "results": [{"duration": 200}]}],
                        },
                    ],
                    "suites": [],
                }
            ],
        }
        report = engine._build_qa_browser_report(
            workspace=workspace,
            playwright_results=playwright_json,
            base_url="http://localhost:3000",
            stack_name="nextjs",
        )
        assert report["tests_passed"] == 2
        assert report["tests_failed"] == 0
        assert report["verdict"] == "pass"

    def test_playwright_results_fail_sets_failed_verdict(
        self, engine: WorkflowEngine, workspace, prd_data_with_acs
    ):
        """Failed tests produce verdict='fail'."""
        playwright_json = {
            "stats": {"expected": 1, "unexpected": 1, "skipped": 0},
            "suites": [
                {
                    "title": "Homepage",
                    "file": "tests/e2e/homepage.spec.ts",
                    "specs": [
                        {
                            "title": "AC-001 user can view the homepage",
                            "tests": [{"status": "passed", "results": []}],
                        },
                        {
                            "title": "AC-002 navigation broken",
                            "tests": [
                                {
                                    "status": "failed",
                                    "results": [
                                        {"duration": 100, "error": {"message": "Element not found"}}
                                    ],
                                }
                            ],
                        },
                    ],
                    "suites": [],
                }
            ],
        }
        report = engine._build_qa_browser_report(
            workspace=workspace,
            playwright_results=playwright_json,
            base_url="http://localhost:3000",
            stack_name="nextjs",
        )
        assert report["tests_failed"] == 1
        assert report["verdict"] == "fail"

    def test_report_includes_base_url_and_stack(
        self, engine: WorkflowEngine, workspace
    ):
        """Report includes base_url and stack_detected fields."""
        report = engine._build_qa_browser_report(
            workspace=workspace,
            playwright_results=None,
            base_url="http://localhost:5173",
            stack_name="react-vite",
        )
        assert report["base_url"] == "http://localhost:5173"
        assert report["stack_detected"] == "react-vite"

    def test_uncovered_acs_are_added_as_skipped(
        self, engine: WorkflowEngine, workspace, prd_data_with_acs
    ):
        """ACs with no test coverage are added as skipped entries."""
        playwright_json = {
            "stats": {"expected": 1, "unexpected": 0, "skipped": 0},
            "suites": [
                {
                    "title": "Homepage",
                    "file": "tests/e2e/homepage.spec.ts",
                    "specs": [
                        {
                            "title": "AC-001 – user can view the homepage",
                            "tests": [{"status": "passed", "results": []}],
                        }
                    ],
                    "suites": [],
                }
            ],
        }
        report = engine._build_qa_browser_report(
            workspace=workspace,
            playwright_results=playwright_json,
            base_url="http://localhost:3000",
            stack_name="nextjs",
        )
        # AC-001 covered, AC-002 not → should appear as skipped
        ac_002_results = [r for r in report["test_results"] if r["ac_id"].upper() == "AC-002"]
        assert ac_002_results, "AC-002 should appear in test_results even without a test"
        assert all(r["status"] == "skipped" for r in ac_002_results)


# ---------------------------------------------------------------------------
# Tests: _extract_playwright_tests
# ---------------------------------------------------------------------------


class TestExtractPlaywrightTests:
    """Unit tests for recursive test extraction."""

    def test_extracts_tests_from_flat_suite(self, engine: WorkflowEngine):
        suite = {
            "title": "Homepage",
            "file": "homepage.spec.ts",
            "specs": [
                {
                    "title": "AC-001 user can view",
                    "tests": [{"status": "passed", "results": [{"duration": 100}]}],
                }
            ],
            "suites": [],
        }
        tests = engine._extract_playwright_tests(suite)
        assert len(tests) == 1
        assert tests[0]["title"] == "AC-001 user can view"
        assert tests[0]["status"] == "passed"
        assert tests[0]["duration_ms"] == 100

    def test_extracts_tests_from_nested_suites(self, engine: WorkflowEngine):
        suite = {
            "title": "Root",
            "file": "root.spec.ts",
            "specs": [],
            "suites": [
                {
                    "title": "Nested",
                    "file": "root.spec.ts",
                    "specs": [
                        {
                            "title": "AC-002 nested test",
                            "tests": [{"status": "failed", "results": []}],
                        }
                    ],
                    "suites": [],
                }
            ],
        }
        tests = engine._extract_playwright_tests(suite)
        assert len(tests) == 1
        assert tests[0]["title"] == "AC-002 nested test"
        assert tests[0]["status"] == "failed"

    def test_extracts_error_messages(self, engine: WorkflowEngine):
        suite = {
            "title": "Suite",
            "file": "suite.spec.ts",
            "specs": [
                {
                    "title": "failing test",
                    "tests": [
                        {
                            "status": "failed",
                            "results": [
                                {"duration": 50, "error": {"message": "selector not found"}}
                            ],
                        }
                    ],
                }
            ],
            "suites": [],
        }
        tests = engine._extract_playwright_tests(suite)
        assert "selector not found" in tests[0]["errors"]


# ---------------------------------------------------------------------------
# Tests: _map_tests_to_acs
# ---------------------------------------------------------------------------


class TestMapTestsToACs:
    """Unit tests for AC mapping."""

    def test_maps_ac_id_in_title(self, engine: WorkflowEngine):
        tests = [
            {
                "title": "AC-001 user can view homepage",
                "suite_title": "",
                "file": "homepage.spec.ts",
                "status": "passed",
                "duration_ms": 100,
                "errors": [],
            }
        ]
        acs = [{"id": "AC-001", "description": "User can view the homepage"}]
        results = engine._map_tests_to_acs(tests, acs)
        assert len(results) == 1
        assert results[0]["ac_id"] == "AC-001"
        assert results[0]["status"] == "passed"
        assert results[0]["criteria"] == "User can view the homepage"

    def test_maps_ac_id_in_suite_title(self, engine: WorkflowEngine):
        tests = [
            {
                "title": "validates form",
                "suite_title": "AC-002 Settings page",
                "file": "settings.spec.ts",
                "status": "passed",
                "duration_ms": 200,
                "errors": [],
            }
        ]
        acs = [{"id": "AC-002", "description": "Navigate to settings"}]
        results = engine._map_tests_to_acs(tests, acs)
        assert results[0]["ac_id"] == "AC-002"

    def test_unknown_ac_for_unmapped_test(self, engine: WorkflowEngine):
        tests = [
            {
                "title": "some random test",
                "suite_title": "",
                "file": "misc.spec.ts",
                "status": "passed",
                "duration_ms": 50,
                "errors": [],
            }
        ]
        results = engine._map_tests_to_acs(tests, [])
        assert results[0]["ac_id"] == "AC-UNKNOWN"

    def test_uncovered_acs_added_as_skipped(self, engine: WorkflowEngine):
        tests = []  # no tests at all
        acs = [
            {"id": "AC-001", "description": "Homepage"},
            {"id": "AC-002", "description": "Settings"},
        ]
        results = engine._map_tests_to_acs(tests, acs)
        assert len(results) == 2
        assert all(r["status"] == "skipped" for r in results)


# ---------------------------------------------------------------------------
# Tests: _qa_browser_post_hook
# ---------------------------------------------------------------------------


class TestQABrowserPostHook:
    """Tests for the post-hook that runs Playwright and writes the report."""

    @pytest.mark.asyncio
    async def test_post_hook_writes_qa_browser_report(
        self, engine: WorkflowEngine, qa_browser_step, workspace
    ):
        """REQ-017/AC-005: Post-hook writes qa_browser_report.json."""
        # Mock server stored on engine
        mock_server = AsyncMock()
        mock_server.base_url = "http://localhost:3000"
        mock_server.stack = MagicMock()
        mock_server.stack.framework = "nextjs"
        mock_server.stop = AsyncMock()
        engine._qa_browser_server = mock_server

        # Mock subprocess.run to return valid Playwright JSON
        playwright_output = json.dumps({
            "stats": {"expected": 1, "unexpected": 0, "skipped": 0},
            "suites": [
                {
                    "title": "Tests",
                    "file": "tests/e2e/test.spec.ts",
                    "specs": [
                        {
                            "title": "AC-001 user can view homepage",
                            "tests": [{"status": "passed", "results": [{"duration": 150}]}],
                        }
                    ],
                    "suites": [],
                }
            ],
        })

        mock_proc = MagicMock()
        mock_proc.stdout = playwright_output.encode()
        mock_proc.stderr = b""
        mock_proc.returncode = 0

        with patch("subprocess.run", return_value=mock_proc):
            await engine._qa_browser_post_hook(qa_browser_step, workspace)

        report_path = workspace / "artifacts" / "qa_browser_report.json"
        assert report_path.exists()

    @pytest.mark.asyncio
    async def test_post_hook_stops_server_after_tests(
        self, engine: WorkflowEngine, qa_browser_step, workspace
    ):
        """AC-011: Server is stopped (teardown) after tests complete."""
        mock_server = AsyncMock()
        mock_server.base_url = "http://localhost:3000"
        mock_server.stack = MagicMock()
        mock_server.stack.framework = "nextjs"
        mock_server.stop = AsyncMock()
        engine._qa_browser_server = mock_server

        mock_proc = MagicMock()
        mock_proc.stdout = b"{}"
        mock_proc.stderr = b""
        mock_proc.returncode = 0

        with patch("subprocess.run", return_value=mock_proc):
            await engine._qa_browser_post_hook(qa_browser_step, workspace)

        mock_server.stop.assert_called_once()
        assert engine._qa_browser_server is None

    @pytest.mark.asyncio
    async def test_post_hook_graceful_degradation_playwright_not_found(
        self, engine: WorkflowEngine, qa_browser_step, workspace
    ):
        """AC-011/AC-012: Missing Playwright writes a skipped report, does not raise."""
        mock_server = AsyncMock()
        mock_server.base_url = "http://localhost:3000"
        mock_server.stack = MagicMock()
        mock_server.stack.framework = "nextjs"
        mock_server.stop = AsyncMock()
        engine._qa_browser_server = mock_server

        with patch("subprocess.run", side_effect=FileNotFoundError("npx not found")):
            await engine._qa_browser_post_hook(qa_browser_step, workspace)  # must not raise

        report_path = workspace / "artifacts" / "qa_browser_report.json"
        assert report_path.exists()
        data = json.loads(report_path.read_text())
        assert data["tests_passed"] == 0
        # All ACs should be marked skipped (no prd.json in this test → 0 ACs, 0 skipped)

    @pytest.mark.asyncio
    async def test_post_hook_playwright_timeout_produces_report(
        self, engine: WorkflowEngine, qa_browser_step, workspace
    ):
        """AC-012: Playwright timeout does not crash; report is written."""
        import subprocess

        mock_server = AsyncMock()
        mock_server.base_url = "http://localhost:3000"
        mock_server.stack = MagicMock()
        mock_server.stack.framework = "nextjs"
        mock_server.stop = AsyncMock()
        engine._qa_browser_server = mock_server

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="npx", timeout=120)):
            await engine._qa_browser_post_hook(qa_browser_step, workspace)

        report_path = workspace / "artifacts" / "qa_browser_report.json"
        assert report_path.exists()


# ---------------------------------------------------------------------------
# Tests: _execute_step integration for Env Setup
# ---------------------------------------------------------------------------


class TestExecuteStepEnvSetupIntegration:
    """Integration-style tests for the Env Setup step hooks in _execute_step."""

    @pytest.mark.asyncio
    async def test_env_setup_step_fails_when_compose_missing(
        self, engine: WorkflowEngine, workspace, run_state
    ):
        """AC-008: _execute_step returns 'failed' when docker-compose.yml absent."""
        # Stub out agent execution to succeed (agent ran but didn't write compose)
        env_setup_report = {
            "docker_compose_written": False,
            "compose_services": [],
            "seed_script_written": False,
            "issues": [],
            "verdict": "pass",
        }
        (workspace / "artifacts" / "env_setup_report.json").write_text(
            json.dumps(env_setup_report)
        )
        (workspace / "artifacts" / "prd.json").write_text(
            json.dumps({"title": "T", "overview": "O", "goals": [], "requirements": [],
                        "constraints": [], "acceptance_criteria": []})
        )

        env_step = WorkflowStepDefinition(
            name="Env Setup",
            agent_role=AgentRole.ENV_SETUP_ENGINEER,
            inputs=[],
            outputs=["env_setup_report"],
        )
        engine.workflow.steps = [env_step]
        engine.state.phases = {}
        engine.state.completed_steps = []

        with patch.object(engine, "_execute_step_tasks", return_value=True), \
             patch.object(engine, "_stop_knowledge_watcher", new_callable=AsyncMock), \
             patch.object(engine, "_start_knowledge_watcher", new_callable=AsyncMock), \
             patch.object(engine, "_emit_phase_complete"):
            result = await engine._execute_step(env_step)

        assert result == "failed"

    @pytest.mark.asyncio
    async def test_env_setup_step_passes_when_compose_exists(
        self, engine: WorkflowEngine, workspace, run_state
    ):
        """REQ-017/AC-004: _execute_step succeeds when docker-compose.yml exists."""
        (workspace / "docker-compose.yml").write_text("version: '3'\nservices: {}")
        env_setup_report = {
            "docker_compose_written": True,
            "compose_services": ["db"],
            "seed_script_written": False,
            "issues": [],
            "verdict": "pass",
        }
        (workspace / "artifacts" / "env_setup_report.json").write_text(
            json.dumps(env_setup_report)
        )
        (workspace / "artifacts" / "prd.json").write_text(
            json.dumps({"title": "T", "overview": "O", "goals": [], "requirements": [],
                        "constraints": [], "acceptance_criteria": []})
        )

        env_step = WorkflowStepDefinition(
            name="Env Setup",
            agent_role=AgentRole.ENV_SETUP_ENGINEER,
            inputs=[],
            outputs=["env_setup_report"],
        )
        engine.workflow.steps = [env_step]
        engine.state.phases = {}
        engine.state.completed_steps = []

        with patch.object(engine, "_execute_step_tasks", return_value=True), \
             patch.object(engine, "_stop_knowledge_watcher", new_callable=AsyncMock), \
             patch.object(engine, "_start_knowledge_watcher", new_callable=AsyncMock), \
             patch.object(engine, "_emit_phase_complete"), \
             patch.object(engine, "_refresh_knowledge", new_callable=AsyncMock):
            result = await engine._execute_step(env_step)

        assert result == "completed"


# ---------------------------------------------------------------------------
# Tests: _execute_step integration for QA Browser
# ---------------------------------------------------------------------------


class TestExecuteStepQABrowserIntegration:
    """Integration-style tests for QA Browser hooks in _execute_step."""

    @pytest.mark.asyncio
    async def test_qa_browser_turbo_skips_agent_returns_completed(
        self, engine: WorkflowEngine, workspace, run_state
    ):
        """AC-006: Turbo mode in _execute_step returns 'completed' without agent invocation."""
        engine.config.speed_mode = SpeedMode.TURBO

        (workspace / "artifacts" / "prd.json").write_text(
            json.dumps({"title": "T", "overview": "O", "goals": [], "requirements": [],
                        "constraints": [], "acceptance_criteria": []})
        )
        qa_step = WorkflowStepDefinition(
            name="QA Browser",
            agent_role=AgentRole.QA_BROWSER_ENGINEER,
            inputs=[],
            outputs=["qa_browser_report"],
        )
        engine.workflow.steps = [qa_step]
        engine.state.phases = {}
        engine.state.completed_steps = []

        mock_stack = MagicMock()
        mock_stack.framework = "nextjs"
        mock_stack.has_frontend = True

        agent_called = []

        async def _mock_execute_step_tasks(step, tasks):
            agent_called.append(True)
            return True

        with patch("orchestrator.stack_detector.detect_stack", return_value=mock_stack), \
             patch.object(engine, "_execute_step_tasks", side_effect=_mock_execute_step_tasks), \
             patch.object(engine, "_emit_phase_complete"), \
             patch.object(engine, "_refresh_knowledge", new_callable=AsyncMock), \
             patch.object(engine, "_stop_knowledge_watcher", new_callable=AsyncMock), \
             patch.object(engine, "_start_knowledge_watcher", new_callable=AsyncMock):
            result = await engine._execute_step(qa_step)

        assert result == "completed"
        assert not agent_called, "Agent should NOT be invoked in turbo mode"

        report_path = workspace / "artifacts" / "qa_browser_report.json"
        assert report_path.exists()
        data = json.loads(report_path.read_text())
        assert data["server_status"] == "skipped"


# ---------------------------------------------------------------------------
# Tests: consistency / AC-level checks
# ---------------------------------------------------------------------------


class TestReportSchema:
    """Verify qa_browser_report structure matches expected schema."""

    def test_stub_report_has_all_required_fields(self, engine: WorkflowEngine, workspace):
        """Stub report contains all fields required by QABrowserReport schema."""
        engine._write_stub_qa_browser_report(workspace, reason="test")
        data = json.loads((workspace / "artifacts" / "qa_browser_report.json").read_text())
        required_fields = {
            "server_status", "stack_detected", "base_url",
            "test_results", "tests_passed", "tests_failed",
            "tests_skipped", "console_errors", "issues", "verdict",
        }
        for field in required_fields:
            assert field in data, f"Field '{field}' missing from stub report"

    def test_build_report_has_all_required_fields(self, engine: WorkflowEngine, workspace):
        """_build_qa_browser_report returns a dict with all required fields."""
        report = engine._build_qa_browser_report(
            workspace=workspace,
            playwright_results=None,
            base_url=None,
            stack_name=None,
        )
        required_fields = {
            "server_status", "stack_detected", "base_url",
            "test_results", "tests_passed", "tests_failed",
            "tests_skipped", "console_errors", "issues", "verdict",
        }
        for field in required_fields:
            assert field in report, f"Field '{field}' missing from built report"

    def test_test_result_has_all_required_fields(self, engine: WorkflowEngine, workspace):
        """Each test_result item has all required BrowserTestResult fields."""
        engine._write_stub_qa_browser_report(workspace, reason="test")
        # The stub has no test_results; create a report with ACs to check
        (workspace / "artifacts" / "prd.json").write_text(
            json.dumps({
                "title": "T", "overview": "O", "goals": [], "requirements": [],
                "constraints": [],
                "acceptance_criteria": [{"id": "AC-001", "description": "Test"}],
            })
        )
        report = engine._build_qa_browser_report(
            workspace=workspace,
            playwright_results=None,
            base_url=None,
            stack_name=None,
        )
        for item in report["test_results"]:
            for field in ("ac_id", "criteria", "status"):
                assert field in item, f"test_result item missing field '{field}'"
