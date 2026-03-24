"""Integration tests: dynamic directory browsing API contract.

TASK-009: Contract tests for the new dynamic directory endpoints:
  GET  /api/v1/directories/root
  GET  /api/v1/directories/{dir_id}/children
  POST /api/v1/directories/{dir_id}/children

Test cases:
  (1) GET /api/v1/directories/root returns 404 when projects_root not configured
  (2) GET /api/v1/directories/root returns root entry with opaque ID when configured
  (3) GET /api/v1/directories/{dir_id}/children returns children list with opaque IDs
  (4) GET /api/v1/directories/{dir_id}/children returns 403 for a dir_id outside root
  (5) GET /api/v1/directories/{dir_id}/children returns 400 at max depth
  (6) POST /api/v1/directories/{dir_id}/children creates directory with valid name
  (7) POST returns 409 for duplicate name
  (8) POST returns 400 for invalid name with path traversal attempt
  (9) POST returns 403 for parent outside projects_root

Pattern: follows tests/integration/conftest.py (TEST_API_KEY, workspace
fixture, httpx AsyncClient with ASGITransport, MagicMock RunTracker).
"""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from .conftest import TEST_API_KEY


# ── Helpers ────────────────────────────────────────────────────────────────


def assert_no_path_key(obj) -> None:
    """Recursively assert that no key named 'path' exists anywhere in *obj*.

    Walks dicts and lists to ensure raw filesystem paths are never surfaced
    in API responses, regardless of nesting depth.

    Raises:
        AssertionError: if any dict in the structure contains a 'path' key.
    """
    if isinstance(obj, dict):
        assert "path" not in obj, (
            f"API response contains forbidden 'path' key which would disclose "
            f"filesystem paths to mobile clients. Offending object: {obj!r}"
        )
        for value in obj.values():
            assert_no_path_key(value)
    elif isinstance(obj, list):
        for item in obj:
            assert_no_path_key(item)


def _build_app_with_projects_root(
    workspace: Path,
    projects_root: Path | None,
    max_browse_depth: int = 10,
):
    """Build a create_mobile_app() instance configured with projects_root.

    Returns (app, tracker) so callers can inspect tracker call history.
    """
    from orchestrator.mobile_api.app import create_mobile_app

    # Build minimal config YAML
    lines = [
        f'workspace_dir: "{workspace}"',
        f'workspace_root: "{workspace.parent}"',
        f'project_name: "{workspace.name}"',
        "max_budget_usd: 50.0",
        "max_concurrent_agents: 5",
        f"max_browse_depth: {max_browse_depth}",
    ]
    if projects_root is not None:
        lines.append(f'projects_root: "{projects_root}"')

    config_path = workspace / "config.yaml"
    config_path.write_text("\n".join(lines) + "\n")

    tracker = MagicMock()
    tracker.active_run_ids.return_value = []
    tracker.is_active.return_value = False
    tracker.start_run = AsyncMock(return_value="newrunid12345678")
    tracker.cancel_run = AsyncMock(return_value=True)
    tracker.get_error.return_value = None

    app = create_mobile_app(workspace_dir=workspace, config_path=config_path)
    app.state.tracker = tracker
    return app, tracker


def _get_authed_client(app):
    """Return a configured async HTTP client for the given app."""
    from httpx import ASGITransport, AsyncClient

    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        headers={"Authorization": f"Bearer {TEST_API_KEY}"},
    )


# ── Test class ─────────────────────────────────────────────────────────────


class TestDynamicDirectoriesContract:
    """Contract tests for the dynamic directory browsing API."""

    # ── (1) GET /root returns 404 when projects_root not configured ─────────

    async def test_root_returns_404_when_projects_root_not_configured(
        self, tmp_path: Path
    ):
        """(1) GET /api/v1/directories/root returns 404 when projects_root is not set."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "artifacts").mkdir()
        (workspace / "logs").mkdir()

        app, _ = _build_app_with_projects_root(workspace, projects_root=None)

        async with _get_authed_client(app) as client:
            response = await client.get("/api/v1/directories/root")

        assert response.status_code == 404, (
            f"Expected 404 when projects_root is not configured, "
            f"got {response.status_code}: {response.text}"
        )
        assert "projects_root" in response.text.lower(), (
            f"Response body should mention 'projects_root'; got: {response.text!r}"
        )

    # ── (2) GET /root returns entry with opaque ID when configured ──────────

    async def test_root_returns_entry_with_opaque_id(self, tmp_path: Path):
        """(2) GET /api/v1/directories/root returns root entry with opaque ID."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "artifacts").mkdir()
        (workspace / "logs").mkdir()

        projects_root = tmp_path / "projects"
        projects_root.mkdir()

        app, _ = _build_app_with_projects_root(workspace, projects_root)

        async with _get_authed_client(app) as client:
            response = await client.get("/api/v1/directories/root")

        assert response.status_code == 200, (
            f"Expected 200 for configured projects_root, "
            f"got {response.status_code}: {response.text}"
        )
        data = response.json()

        # Must have id and name fields
        assert "id" in data, f"Response must contain 'id' field; got: {data}"
        assert "name" in data, f"Response must contain 'name' field; got: {data}"
        assert data["name"] == "projects", (
            f"Expected name='projects', got '{data['name']}'"
        )

        # ID must be a 32-char hex string (UUID v5 hex)
        assert len(data["id"]) == 32, (
            f"Opaque ID must be 32 chars (UUID v5 hex); got: {data['id']!r}"
        )

        # No raw path in response
        assert_no_path_key(data)

    # ── (3) GET /{dir_id}/children returns children with opaque IDs ─────────

    async def test_children_returns_entries_with_opaque_ids_and_no_paths(
        self, tmp_path: Path
    ):
        """(3) GET /{dir_id}/children returns children list with opaque IDs and no raw paths."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "artifacts").mkdir()
        (workspace / "logs").mkdir()

        projects_root = tmp_path / "projects"
        projects_root.mkdir()

        # Create subdirectories
        (projects_root / "project_a").mkdir()
        (projects_root / "project_b").mkdir()
        (projects_root / "project_b" / "pubspec.yaml").write_text("name: test\n")

        app, _ = _build_app_with_projects_root(workspace, projects_root)

        async with _get_authed_client(app) as client:
            # Get the root ID
            root_resp = await client.get("/api/v1/directories/root")
            assert root_resp.status_code == 200
            root_id = root_resp.json()["id"]

            # List children of root
            response = await client.get(f"/api/v1/directories/{root_id}/children")

        assert response.status_code == 200, (
            f"Expected 200 for listing children, "
            f"got {response.status_code}: {response.text}"
        )
        data = response.json()

        # Response must have required fields
        assert "entries" in data, f"Response must have 'entries'; got keys: {list(data.keys())}"
        assert "parent_id" in data, f"Response must have 'parent_id'; got keys: {list(data.keys())}"
        assert "depth" in data, f"Response must have 'depth'; got keys: {list(data.keys())}"
        assert "at_depth_limit" in data, f"Response must have 'at_depth_limit'"

        assert data["parent_id"] == root_id
        assert data["depth"] == 0  # root is depth 0

        entries = data["entries"]
        assert len(entries) == 2, f"Expected 2 children, got {len(entries)}: {entries}"

        by_name = {e["name"]: e for e in entries}
        assert "project_a" in by_name, f"'project_a' not found; got: {list(by_name.keys())}"
        assert "project_b" in by_name, f"'project_b' not found; got: {list(by_name.keys())}"

        # Each entry must have a 32-char hex opaque ID
        for entry in entries:
            assert len(entry["id"]) == 32, (
                f"Opaque ID must be 32 chars; got: {entry['id']!r}"
            )

        # Flutter tech stack detected for project_b
        assert by_name["project_b"]["tech_stack"] == "Flutter", (
            f"Expected tech_stack='Flutter' for project with pubspec.yaml, "
            f"got: {by_name['project_b']['tech_stack']!r}"
        )

        # No raw paths anywhere in the response
        assert_no_path_key(data)

    # ── (4) GET /{dir_id}/children returns 403 for outside-root dir_id ──────

    async def test_children_returns_403_for_id_outside_projects_root(
        self, tmp_path: Path
    ):
        """(4) GET /{dir_id}/children returns 403 when dir_id resolves outside projects_root."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "artifacts").mkdir()
        (workspace / "logs").mkdir()

        projects_root = tmp_path / "projects"
        projects_root.mkdir()

        app, _ = _build_app_with_projects_root(workspace, projects_root)

        # Use an ID that was generated for a path outside projects_root
        # (e.g. the workspace directory, which is NOT inside projects_root)
        import uuid as _uuid
        salt = app.state.directory_salt
        outside_path = str(workspace.resolve())
        fake_id = _uuid.uuid5(salt, outside_path).hex

        async with _get_authed_client(app) as client:
            response = await client.get(f"/api/v1/directories/{fake_id}/children")

        # Should be 403 — the ID either doesn't exist in projects_root scan,
        # or if somehow it did, would fail the containment check
        assert response.status_code in (403, 404), (
            f"Expected 403/404 for outside-root dir_id, "
            f"got {response.status_code}: {response.text}"
        )

    # ── (5) GET /{dir_id}/children returns 400 at max depth ─────────────────

    async def test_children_returns_400_at_max_depth(self, tmp_path: Path):
        """(5) GET /{dir_id}/children returns 400 with depth-limit message at max depth."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "artifacts").mkdir()
        (workspace / "logs").mkdir()

        projects_root = tmp_path / "projects"
        projects_root.mkdir()

        # Create a nested subdirectory
        level1 = projects_root / "level1"
        level1.mkdir()

        # Set max_browse_depth=1 so level1 is at the limit
        app, _ = _build_app_with_projects_root(
            workspace, projects_root, max_browse_depth=1
        )

        async with _get_authed_client(app) as client:
            # Get the root ID
            root_resp = await client.get("/api/v1/directories/root")
            assert root_resp.status_code == 200
            root_id = root_resp.json()["id"]

            # Get level1's ID from root's children
            children_resp = await client.get(f"/api/v1/directories/{root_id}/children")
            assert children_resp.status_code == 200
            children = children_resp.json()["entries"]
            assert len(children) == 1
            level1_id = children[0]["id"]

            # Now try to list children of level1 — depth=1 equals max_depth=1
            response = await client.get(f"/api/v1/directories/{level1_id}/children")

        assert response.status_code == 400, (
            f"Expected 400 at max depth, got {response.status_code}: {response.text}"
        )
        body = response.json()
        assert body.get("at_depth_limit") is True or "depth" in response.text.lower(), (
            f"Response should indicate depth limit was reached; got: {body!r}"
        )

    # ── (6) POST creates directory with valid name ───────────────────────────

    async def test_create_directory_with_valid_name_returns_201(
        self, tmp_path: Path
    ):
        """(6) POST /api/v1/directories/{dir_id}/children creates directory with valid name."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "artifacts").mkdir()
        (workspace / "logs").mkdir()

        projects_root = tmp_path / "projects"
        projects_root.mkdir()

        app, _ = _build_app_with_projects_root(workspace, projects_root)

        async with _get_authed_client(app) as client:
            # Get root ID
            root_resp = await client.get("/api/v1/directories/root")
            assert root_resp.status_code == 200
            root_id = root_resp.json()["id"]

            # Create a new directory
            response = await client.post(
                f"/api/v1/directories/{root_id}/children",
                json={"name": "my-new-project"},
            )

        assert response.status_code == 201, (
            f"Expected 201 for valid name, got {response.status_code}: {response.text}"
        )
        data = response.json()

        assert "id" in data, f"Response must have 'id'; got: {data}"
        assert data["name"] == "my-new-project", (
            f"Expected name='my-new-project', got: {data['name']!r}"
        )
        assert len(data["id"]) == 32, f"Opaque ID must be 32 chars; got: {data['id']!r}"

        # Verify the directory was actually created
        assert (projects_root / "my-new-project").is_dir(), (
            "Directory 'my-new-project' was not created on the filesystem"
        )

        # No raw paths in response
        assert_no_path_key(data)

    # ── (7) POST returns 409 for duplicate name ──────────────────────────────

    async def test_create_directory_returns_409_for_duplicate(self, tmp_path: Path):
        """(7) POST /api/v1/directories/{dir_id}/children returns 409 for duplicate name."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "artifacts").mkdir()
        (workspace / "logs").mkdir()

        projects_root = tmp_path / "projects"
        projects_root.mkdir()

        # Pre-create the directory so the second create will conflict
        (projects_root / "existing-project").mkdir()

        app, _ = _build_app_with_projects_root(workspace, projects_root)

        async with _get_authed_client(app) as client:
            root_resp = await client.get("/api/v1/directories/root")
            root_id = root_resp.json()["id"]

            response = await client.post(
                f"/api/v1/directories/{root_id}/children",
                json={"name": "existing-project"},
            )

        assert response.status_code == 409, (
            f"Expected 409 Conflict for duplicate name, "
            f"got {response.status_code}: {response.text}"
        )

    # ── (8) POST returns 400 for invalid name (path traversal) ──────────────

    async def test_create_directory_returns_400_for_path_traversal_name(
        self, tmp_path: Path
    ):
        """(8) POST returns 400 for name containing path traversal (../)."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "artifacts").mkdir()
        (workspace / "logs").mkdir()

        projects_root = tmp_path / "projects"
        projects_root.mkdir()

        app, _ = _build_app_with_projects_root(workspace, projects_root)

        async with _get_authed_client(app) as client:
            root_resp = await client.get("/api/v1/directories/root")
            root_id = root_resp.json()["id"]

            # Try path traversal names — should be rejected
            for invalid_name in ["../escape", "/absolute", "name\x00null"]:
                response = await client.post(
                    f"/api/v1/directories/{root_id}/children",
                    json={"name": invalid_name},
                )
                assert response.status_code in (400, 422), (
                    f"Expected 400/422 for invalid name {invalid_name!r}, "
                    f"got {response.status_code}: {response.text}"
                )

    # ── (9) POST returns 403 for parent outside projects_root ───────────────

    async def test_create_directory_returns_403_for_parent_outside_root(
        self, tmp_path: Path
    ):
        """(9) POST returns 403 when parent dir_id resolves outside projects_root."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "artifacts").mkdir()
        (workspace / "logs").mkdir()

        projects_root = tmp_path / "projects"
        projects_root.mkdir()

        app, _ = _build_app_with_projects_root(workspace, projects_root)

        # Generate an ID that corresponds to a path outside projects_root
        import uuid as _uuid
        salt = app.state.directory_salt
        outside_path = str(workspace.resolve())
        fake_id = _uuid.uuid5(salt, outside_path).hex

        async with _get_authed_client(app) as client:
            response = await client.post(
                f"/api/v1/directories/{fake_id}/children",
                json={"name": "should-fail"},
            )

        assert response.status_code in (403, 404), (
            f"Expected 403/404 for parent outside projects_root, "
            f"got {response.status_code}: {response.text}"
        )

    # ── Auth guard ──────────────────────────────────────────────────────────

    async def test_all_endpoints_require_auth(self, tmp_path: Path):
        """Dynamic directory endpoints require authentication (no free pass)."""
        from httpx import ASGITransport, AsyncClient

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "artifacts").mkdir()
        (workspace / "logs").mkdir()

        projects_root = tmp_path / "projects"
        projects_root.mkdir()

        app, _ = _build_app_with_projects_root(workspace, projects_root)

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
            # No Authorization header
        ) as client:
            root_resp = await client.get("/api/v1/directories/root")
            children_resp = await client.get("/api/v1/directories/fakeid/children")
            create_resp = await client.post(
                "/api/v1/directories/fakeid/children",
                json={"name": "test"},
            )

        assert root_resp.status_code == 401, (
            f"GET /directories/root must require auth; got {root_resp.status_code}"
        )
        assert children_resp.status_code == 401, (
            f"GET /directories/{{id}}/children must require auth; got {children_resp.status_code}"
        )
        assert create_resp.status_code == 401, (
            f"POST /directories/{{id}}/children must require auth; got {create_resp.status_code}"
        )

    # ── No raw paths anywhere ───────────────────────────────────────────────

    async def test_no_raw_paths_in_any_response(self, tmp_path: Path):
        """No raw filesystem paths appear in any dynamic directory API response."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "artifacts").mkdir()
        (workspace / "logs").mkdir()

        projects_root = tmp_path / "projects"
        projects_root.mkdir()
        (projects_root / "sub1").mkdir()
        (projects_root / "sub1" / "deep").mkdir()

        app, _ = _build_app_with_projects_root(workspace, projects_root)

        async with _get_authed_client(app) as client:
            root_resp = await client.get("/api/v1/directories/root")
            assert root_resp.status_code == 200
            assert_no_path_key(root_resp.json())

            root_id = root_resp.json()["id"]
            children_resp = await client.get(f"/api/v1/directories/{root_id}/children")
            assert children_resp.status_code == 200
            assert_no_path_key(children_resp.json())

            children = children_resp.json()["entries"]
            if children:
                sub_id = children[0]["id"]
                deep_resp = await client.get(f"/api/v1/directories/{sub_id}/children")
                assert deep_resp.status_code == 200
                assert_no_path_key(deep_resp.json())
