"""Shared fixtures for Mobile API integration tests.

IMPORTANT — module-level env var injection:
  orchestrator.mobile_api.auth reads ORCHESTRATOR_API_KEY at import time and
  raises RuntimeError if it is absent. The pytest_configure() hook below
  ensures the env var is present before pytest begins collecting test modules,
  which is the only safe moment to inject it without triggering the guard.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

# ── Constants ──────────────────────────────────────────────────────────────
TEST_API_KEY = "test-integration-key-xN8qP2wZ9r"
WRONG_API_KEY = "completely-wrong-key-000"

# Representative run IDs used across test modules
RUN_COMPLETED = "aabb1122ccdd3344"
RUN_RUNNING = "eeff5566aabb7788"
RUN_FAILED = "ffaa9900bbcc1122"


def pytest_configure(config: pytest.Config) -> None:
    """Set ORCHESTRATOR_API_KEY before any test module is imported."""
    os.environ["ORCHESTRATOR_API_KEY"] = TEST_API_KEY


# ── Workspace helpers ──────────────────────────────────────────────────────

def _write_state(workspace: Path, run_id: str, data: dict) -> None:
    (workspace / f"state-{run_id}.json").write_text(json.dumps(data))


def _write_jsonl(workspace: Path, run_id: str, events: list[dict]) -> None:
    lines = "\n".join(json.dumps(e) for e in events)
    (workspace / "logs" / f"run-{run_id}.jsonl").write_text(lines)


# ── Core workspace fixtures ────────────────────────────────────────────────

@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Empty workspace directory with required sub-directories."""
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "logs").mkdir()
    return tmp_path


@pytest.fixture
def workspace_with_runs(workspace: Path) -> tuple[Path, str, str, str]:
    """
    Workspace containing:
      - A completed run (RUN_COMPLETED) with full JSONL log and state file
      - A running run  (RUN_RUNNING)  with partial JSONL log and state file
      - A failed run   (RUN_FAILED)   with error state file
      - workspace/artifacts/prd.json  (sample artifact)
      - workspace/state.json          (global state — points to running run)
    """
    # ---- Completed run -------------------------------------------------
    state_completed = {
        "run_id": RUN_COMPLETED,
        "status": "completed",
        "workflow_type": "feature_development",
        "feature_request": "Add dark mode toggle to settings page",
        "start_time": "2024-01-15T10:00:00Z",
        "end_time": "2024-01-15T10:30:00Z",
        "total_cost_usd": 1.50,
        "steps_completed": 5,
        "steps_total": 5,
        "current_step": None,
        "phases": {
            "pm": {"status": "completed", "cost_usd": 0.30, "model_tier": "haiku"},
            "architect": {"status": "completed", "cost_usd": 0.40, "model_tier": "sonnet"},
            "engineer": {"status": "completed", "cost_usd": 0.50, "model_tier": "sonnet"},
            "qa": {"status": "completed", "cost_usd": 0.20, "model_tier": "haiku"},
            "reviewer": {"status": "completed", "cost_usd": 0.10, "model_tier": "haiku"},
        },
        "workflow_tasks": [],
    }
    _write_state(workspace, RUN_COMPLETED, state_completed)
    _write_jsonl(workspace, RUN_COMPLETED, [
        {"event": "run_start", "run_id": RUN_COMPLETED, "workflow_type": "feature_development"},
        {"event": "phase_start", "phase": "pm", "step": "PM Phase"},
        {"event": "task_result", "phase": "pm", "cost_usd": 0.30, "agent_role": "pm"},
        {"event": "phase_complete", "phase": "pm"},
        {"event": "run_complete", "run_id": RUN_COMPLETED, "status": "completed"},
    ])

    # ---- Running run ---------------------------------------------------
    state_running = {
        "run_id": RUN_RUNNING,
        "status": "running",
        "workflow_type": "bugfix",
        "feature_request": "Fix login button crash on Android 12 due to null pointer",
        "start_time": "2024-01-15T11:00:00Z",
        "end_time": None,
        "total_cost_usd": 0.25,
        "steps_completed": 2,
        "steps_total": 5,
        "current_step": "Architecture Phase",
        "phases": {
            "pm": {"status": "completed", "cost_usd": 0.15, "model_tier": "haiku"},
            "architect": {"status": "running", "cost_usd": 0.10, "model_tier": "sonnet"},
        },
        "workflow_tasks": [],
    }
    _write_state(workspace, RUN_RUNNING, state_running)
    # Global state.json points to the most recent (running) run
    (workspace / "state.json").write_text(json.dumps(state_running))
    _write_jsonl(workspace, RUN_RUNNING, [
        {"event": "run_start", "run_id": RUN_RUNNING, "workflow_type": "bugfix"},
        {"event": "phase_start", "phase": "pm", "step": "PM Phase"},
        {"event": "task_invoke", "phase": "pm", "agent_role": "pm", "model_tier": "haiku"},
        {"event": "task_result", "phase": "pm", "cost_usd": 0.15},
        {"event": "phase_complete", "phase": "pm"},
        {"event": "phase_start", "phase": "architect", "step": "Architecture Phase"},
    ])

    # ---- Failed run ----------------------------------------------------
    state_failed = {
        "run_id": RUN_FAILED,
        "status": "failed",
        "workflow_type": "refactor",
        "feature_request": "Refactor authentication module for better testability",
        "start_time": "2024-01-14T09:00:00Z",
        "end_time": "2024-01-14T09:05:00Z",
        "total_cost_usd": 0.08,
        "steps_completed": 1,
        "steps_total": 5,
        "current_step": None,
        "phases": {
            "pm": {"status": "failed", "cost_usd": 0.08, "model_tier": "haiku",
                   "error": "Budget exceeded mid-phase"},
        },
        "workflow_tasks": [],
    }
    _write_state(workspace, RUN_FAILED, state_failed)
    _write_jsonl(workspace, RUN_FAILED, [
        {"event": "run_start", "run_id": RUN_FAILED},
        {"event": "phase_start", "phase": "pm"},
        {"event": "error", "phase": "pm", "message": "Budget exceeded mid-phase"},
    ])

    # ---- Artifact ------------------------------------------------------
    prd_artifact = {
        "title": "Fix login button crash",
        "overview": "The login button crashes on Android 12 due to a null pointer "
                    "dereference triggered when the user taps the button in rapid succession.",
        "goals": ["Fix the crash", "Add regression test"],
        "requirements": [
            {"id": "REQ-001", "description": "Login button MUST NOT crash", "priority": "must"},
            {"id": "REQ-002", "description": "Add unit test for rapid-tap scenario", "priority": "should"},
        ],
        "constraints": ["Must not break iOS login flow"],
        "acceptance_criteria": [
            "Login button works on Android 12",
            "No NullPointerException in device logs during rapid-tap sequence",
        ],
    }
    (workspace / "artifacts" / "prd.json").write_text(json.dumps(prd_artifact))

    return workspace, RUN_COMPLETED, RUN_RUNNING, RUN_FAILED


# ── Config fixtures ────────────────────────────────────────────────────────

@pytest.fixture
def config_yaml(workspace: Path) -> Path:
    """Minimal valid OrchestratorConfig YAML with no secrets."""
    config_path = workspace / "config.yaml"
    config_path.write_text(
        f'workspace_dir: "{workspace}"\n'
        f'workspace_root: "{workspace.parent}"\n'
        f'project_name: "{workspace.name}"\n'
        'default_model: claude-haiku-4-5\n'
        'max_budget_usd: 50.0\n'
        'max_concurrent_agents: 5\n'
    )
    return config_path


@pytest.fixture
def config_yaml_with_secrets(workspace: Path) -> Path:
    """Config YAML containing sensitive fields that must be redacted in API responses."""
    config_path = workspace / "config.yaml"
    config_path.write_text(
        f'workspace_dir: "{workspace}"\n'
        f'workspace_root: "{workspace.parent}"\n'
        f'project_name: "{workspace.name}"\n'
        'default_model: claude-haiku-4-5\n'
        'max_budget_usd: 50.0\n'
        'api_key: "super-secret-api-key-12345"\n'
        'anthropic_token: "tok-anthro-secret-value"\n'
        'monitoring:\n'
        '  webhook_secret: "wh-secret-abc"\n'
        '  endpoint: "https://hooks.example.com/notify"\n'
    )
    return config_path


# ── RunTracker mock ────────────────────────────────────────────────────────

@pytest.fixture
def mock_tracker() -> MagicMock:
    """
    RunTracker mock that never spawns actual orchestration processes.

    Tests that exercise POST /api/v1/runs or cancellation should configure
    this mock's return values as needed.
    """
    tracker = MagicMock()
    tracker.active_run_ids.return_value = []
    tracker.is_active.return_value = False
    tracker.start_run = AsyncMock(return_value="newrunid12345678")
    tracker.cancel_run = AsyncMock(return_value=True)
    tracker.get_error.return_value = None
    return tracker


@pytest.fixture
def mock_tracker_with_active_run() -> MagicMock:
    """RunTracker mock simulating one active running run (RUN_RUNNING)."""
    tracker = MagicMock()
    tracker.active_run_ids.return_value = [RUN_RUNNING]
    tracker.is_active.side_effect = lambda run_id: run_id == RUN_RUNNING
    tracker.start_run = AsyncMock(side_effect=ValueError(
        f"A run is already active ({RUN_RUNNING}). "
        "Cancel or wait for it to finish before starting another."
    ))
    tracker.cancel_run = AsyncMock(return_value=True)
    tracker.get_error.return_value = None
    return tracker


# ── FastAPI app fixtures ───────────────────────────────────────────────────

@pytest.fixture
def mobile_app(workspace: Path, config_yaml: Path, mock_tracker: MagicMock):
    """Mobile FastAPI app with real workspace and mocked RunTracker."""
    from orchestrator.mobile_api.app import create_mobile_app

    app = create_mobile_app(workspace_dir=workspace, config_path=config_yaml)
    app.state.tracker = mock_tracker
    return app


@pytest.fixture
def mobile_app_with_runs(
    workspace_with_runs: tuple,
    config_yaml: Path,
    mock_tracker: MagicMock,
):
    """Mobile app with run state pre-populated from workspace_with_runs fixture."""
    workspace, run_completed, run_running, run_failed = workspace_with_runs
    from orchestrator.mobile_api.app import create_mobile_app

    app = create_mobile_app(workspace_dir=workspace, config_path=config_yaml)
    app.state.tracker = mock_tracker
    return app, workspace, run_completed, run_running, run_failed


@pytest.fixture
def mobile_app_active_run(
    workspace_with_runs: tuple,
    config_yaml: Path,
    mock_tracker_with_active_run: MagicMock,
):
    """Mobile app where tracker reports RUN_RUNNING as the active run."""
    workspace, run_completed, run_running, run_failed = workspace_with_runs
    from orchestrator.mobile_api.app import create_mobile_app

    app = create_mobile_app(workspace_dir=workspace, config_path=config_yaml)
    app.state.tracker = mock_tracker_with_active_run
    return app, workspace, run_completed, run_running


# ── Async HTTP client fixtures ─────────────────────────────────────────────

@pytest_asyncio.fixture
async def client(mobile_app) -> AsyncGenerator:
    """Authenticated async HTTP client for the mobile API."""
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(
        transport=ASGITransport(app=mobile_app),
        base_url="http://testserver",
        headers={"Authorization": f"Bearer {TEST_API_KEY}"},
    ) as c:
        yield c


@pytest_asyncio.fixture
async def unauthed_client(mobile_app) -> AsyncGenerator:
    """HTTP client WITHOUT any Authorization header."""
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(
        transport=ASGITransport(app=mobile_app),
        base_url="http://testserver",
    ) as c:
        yield c


@pytest_asyncio.fixture
async def client_with_runs(mobile_app_with_runs) -> AsyncGenerator:
    """Authenticated client with pre-populated run data."""
    from httpx import ASGITransport, AsyncClient

    app, workspace, run_completed, run_running, run_failed = mobile_app_with_runs
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        headers={"Authorization": f"Bearer {TEST_API_KEY}"},
    ) as c:
        yield c, workspace, run_completed, run_running, run_failed


@pytest_asyncio.fixture
async def client_active_run(mobile_app_active_run) -> AsyncGenerator:
    """Authenticated client where a run is currently active in the tracker."""
    from httpx import ASGITransport, AsyncClient

    app, workspace, run_completed, run_running = mobile_app_active_run
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        headers={"Authorization": f"Bearer {TEST_API_KEY}"},
    ) as c:
        yield c, workspace, run_completed, run_running


# ── Synchronous WebSocket-capable client ──────────────────────────────────

@pytest.fixture
def ws_client_with_runs(mobile_app_with_runs):
    """
    Starlette synchronous TestClient — required for WebSocket tests.
    httpx AsyncClient does not support WebSocket upgrades.
    """
    from starlette.testclient import TestClient

    app, workspace, run_completed, run_running, run_failed = mobile_app_with_runs
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c, workspace, run_completed, run_running
