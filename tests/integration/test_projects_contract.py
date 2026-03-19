"""Integration tests for GET /api/v1/projects (TASK-007).

Tests project listing, path disclosure prevention, and auth.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import AsyncGenerator
from unittest.mock import MagicMock, AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from .conftest import TEST_API_KEY
from .test_dynamic_directories_contract import assert_no_path_key

from orchestrator.mobile_api.dynamic_directory_service import make_opaque_id


@pytest.fixture
def workspace_with_projects(tmp_path: Path) -> tuple[Path, Path, uuid.UUID]:
    """Workspace with projects_root having 3 subdirectories and state files."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "artifacts").mkdir()
    (workspace / "logs").mkdir()

    projects_root = tmp_path / "projects"
    projects_root.mkdir()

    # Create 3 project directories
    for name in ["alpha", "beta", "gamma"]:
        d = projects_root / name
        d.mkdir()

    # We need a salt for opaque IDs — must match the salt the app will use.
    # create_mobile_app() reads the salt from workspace/.server_salt on startup.
    # Writing it here guarantees both the test data and the running app use the
    # same salt, so opaque IDs in state files match those computed at list time.
    salt = uuid.uuid4()
    (workspace / ".server_salt").write_text(str(salt))

    # Create state files that reference project workspace_ids
    alpha_id = make_opaque_id(str((projects_root / "alpha").resolve()), salt)
    beta_id = make_opaque_id(str((projects_root / "beta").resolve()), salt)

    # alpha has 2 runs, beta has 1, gamma has 0
    for i, ws_id in enumerate([alpha_id, alpha_id, beta_id]):
        state = {
            "run_id": f"run{i:04d}",
            "status": "completed",
            "workflow_type": "feature_development",
            "feature_request": f"Feature {i}",
            "start_time": f"2024-01-{15+i}T10:00:00Z",
            "workspace_id": ws_id,
        }
        (workspace / f"state-run{i:04d}.json").write_text(json.dumps(state))

    return workspace, projects_root, salt


@pytest.fixture
def mobile_app_projects(workspace_with_projects):
    workspace, projects_root, salt = workspace_with_projects

    config_path = workspace / "config.yaml"
    config_path.write_text(
        f'workspace_dir: "{workspace}"\n'
        f'projects_root: "{projects_root}"\n'
        'default_model: claude-haiku-4-5\n'
    )

    from orchestrator.mobile_api.app import create_mobile_app
    app = create_mobile_app(workspace_dir=workspace, config_path=config_path)

    tracker = MagicMock()
    tracker.active_run_ids.return_value = []
    tracker.is_active.return_value = False
    tracker.start_run = AsyncMock()
    tracker.cancel_run = AsyncMock()
    tracker.get_error.return_value = None
    app.state.tracker = tracker

    return app, workspace, projects_root, salt


@pytest_asyncio.fixture
async def client_projects(mobile_app_projects) -> AsyncGenerator:
    app, workspace, projects_root, salt = mobile_app_projects
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        headers={"Authorization": f"Bearer {TEST_API_KEY}"},
    ) as c:
        yield c, workspace, projects_root, salt


@pytest.mark.asyncio
async def test_projects_returns_200_with_entries(client_projects):
    """GET /api/v1/projects returns all 3 projects."""
    client, workspace, projects_root, salt = client_projects
    resp = await client.get("/api/v1/projects")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 3
    names = {e["name"] for e in data}
    assert names == {"alpha", "beta", "gamma"}


@pytest.mark.asyncio
async def test_projects_have_required_fields(client_projects):
    """Each entry has id, name, last_modified, run_count."""
    client, workspace, projects_root, salt = client_projects
    resp = await client.get("/api/v1/projects")
    for entry in resp.json():
        assert "id" in entry
        assert "name" in entry
        assert "last_modified" in entry
        assert "run_count" in entry


@pytest.mark.asyncio
async def test_projects_no_path_field(client_projects):
    """No 'path' field anywhere in the response (recursive security check, AC-005)."""
    client, workspace, projects_root, salt = client_projects
    resp = await client.get("/api/v1/projects")
    # Use the recursive helper from test_dynamic_directories_contract — it walks
    # dicts AND lists to ensure raw filesystem paths are never surfaced at any
    # nesting depth, not just at the top level of each entry.
    assert_no_path_key(resp.json())


@pytest.mark.asyncio
async def test_projects_run_counts(client_projects):
    """Run counts match the state files."""
    client, workspace, projects_root, salt = client_projects
    resp = await client.get("/api/v1/projects")
    data = resp.json()
    count_by_name = {e["name"]: e["run_count"] for e in data}
    assert count_by_name["alpha"] == 2
    assert count_by_name["beta"] == 1
    assert count_by_name["gamma"] == 0


@pytest.mark.asyncio
async def test_projects_empty_when_no_root(tmp_path):
    """Returns empty array when projects_root is None."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "artifacts").mkdir()
    (workspace / "logs").mkdir()

    config_path = workspace / "config.yaml"
    config_path.write_text(
        f'workspace_dir: "{workspace}"\n'
        'default_model: claude-haiku-4-5\n'
    )

    from orchestrator.mobile_api.app import create_mobile_app
    app = create_mobile_app(workspace_dir=workspace, config_path=config_path)

    tracker = MagicMock()
    tracker.active_run_ids.return_value = []
    app.state.tracker = tracker

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        headers={"Authorization": f"Bearer {TEST_API_KEY}"},
    ) as client:
        resp = await client.get("/api/v1/projects")
        assert resp.status_code == 200
        assert resp.json() == []


@pytest.mark.asyncio
async def test_projects_401_without_auth(mobile_app_projects):
    """Returns 401 without valid API key."""
    app, *_ = mobile_app_projects
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        resp = await client.get("/api/v1/projects")
        assert resp.status_code == 401
