"""Integration tests: GET /api/v1/directories and extended POST /api/v1/runs.

TASK-017: Contract tests for the new directory-selection endpoint and the
extended run-start request that forwards workspace_id and orchestrate flags.

Test cases:
  (1) GET /api/v1/directories without auth header → 401.
  (2) GET /api/v1/directories with valid auth + empty allowed_directories →
      200, exactly one entry, name == workspace_dir.name.
  (3) GET /api/v1/directories with 2 configured allowed_directories (real
      tmp dirs, one with pyproject.toml, one with pubspec.yaml) → 200, 2
      entries with correct tech_stack ('Python', 'Flutter').
  (4) No 'path' key in any directory API response (recursive check).
  (5) POST /api/v1/runs with workspace_id='nonexistent-id-xyz' → 422,
      response body contains the string 'workspace_id'.
  (6) POST /api/v1/runs with valid workspace_id (one of the configured
      dirs) → 202.
  (7) POST /api/v1/runs with mode='balanced' and confirm=True → 202;
      captured RunRequest.mode=='balanced' and RunRequest.confirm==True.
  (8) POST /api/v1/runs with confirm=False → captured RunRequest.confirm
      ==False (not silently dropped as falsy).
  (9) POST /api/v1/runs with all new fields absent (original 2-field
      payload) → 202, backward compat.
 (10) POST /api/v1/runs with checklist_verify=False → captured
      RunRequest.checklist_verify==False.

Pattern: follows tests/integration/conftest.py (TEST_API_KEY, workspace
fixture, httpx AsyncClient with ASGITransport, MagicMock RunTracker).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from .conftest import TEST_API_KEY


# ── Helpers ────────────────────────────────────────────────────────────────


def assert_no_path_key(obj) -> None:
    """Recursively assert that no key named 'path' exists anywhere in *obj*.

    Walks dicts and lists to ensure raw filesystem paths are never surfaced
    in API responses, regardless of nesting depth.

    Args:
        obj: Parsed JSON value (dict, list, or scalar).

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


def _minimal_run_payload(**overrides) -> dict:
    """Minimum valid run-start payload (original 2-field baseline)."""
    return {
        "feature_request": "Add user onboarding screen to the app",
        "workflow_type": "feature_development",
        **overrides,
    }


def _build_app_with_dirs(workspace: Path, *extra_dirs: Path, config_path: Path | None = None):
    """Build a create_mobile_app() instance with a pre-configured MagicMock tracker.

    When *extra_dirs* is non-empty, a config YAML is written that lists those
    directories under ``allowed_directories``.  When empty, no
    ``allowed_directories`` key is written so the factory falls back to
    *workspace* as the single allowed directory.

    Returns ``(app, tracker)`` so callers can inspect tracker call history.
    """
    from orchestrator.mobile_api.app import create_mobile_app

    if config_path is None:
        # Build config YAML content
        lines = [
            f'workspace_dir: "{workspace}"',
            f'workspace_root: "{workspace.parent}"',
            f'project_name: "{workspace.name}"',
            "default_model: claude-haiku-4-5",
            "max_budget_usd: 50.0",
            "max_concurrent_agents: 5",
        ]
        if extra_dirs:
            lines.append("allowed_directories:")
            for d in extra_dirs:
                lines.append(f'  - path: "{d}"')
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


# ── Test class: GET /api/v1/directories ────────────────────────────────────


class TestDirectoriesContract:
    """Contract tests for GET /api/v1/directories."""

    # ── (1) Auth guard ─────────────────────────────────────────────────────

    async def test_no_auth_header_returns_401(self, unauthed_client):
        """(1) GET /api/v1/directories without Authorization header → 401."""
        response = await unauthed_client.get("/api/v1/directories")
        assert response.status_code == 401, (
            f"Expected 401 Unauthorized for unauthenticated request, "
            f"got {response.status_code}"
        )

    # ── (2) Empty allowed_directories → workspace fallback ─────────────────

    async def test_empty_config_returns_single_workspace_entry(
        self, client, workspace: Path
    ):
        """(2) Empty allowed_directories → 200, exactly one entry, name == workspace.name."""
        response = await client.get("/api/v1/directories")
        assert response.status_code == 200

        directories = response.json()["directories"]
        assert len(directories) == 1, (
            f"Expected exactly 1 directory (workspace fallback), "
            f"got {len(directories)}: {directories}"
        )
        assert directories[0]["name"] == workspace.name, (
            f"Expected entry name '{workspace.name}', got '{directories[0]['name']}'"
        )

    # ── (3) Two configured allowed_directories ─────────────────────────────

    async def test_configured_dirs_returns_two_entries_with_correct_tech_stack(
        self, tmp_path: Path
    ):
        """(3) 2 configured allowed_directories → 200, 2 entries, correct tech_stack."""
        from httpx import ASGITransport, AsyncClient

        # Create two real directories with tech-stack indicator files
        py_dir = tmp_path / "my_python_project"
        py_dir.mkdir()
        (py_dir / "pyproject.toml").write_text("[project]\nname = 'test'\n")

        flutter_dir = tmp_path / "my_flutter_app"
        flutter_dir.mkdir()
        (flutter_dir / "pubspec.yaml").write_text("name: test_app\n")

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "artifacts").mkdir()
        (workspace / "logs").mkdir()

        app, _tracker = _build_app_with_dirs(workspace, py_dir, flutter_dir)

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
            headers={"Authorization": f"Bearer {TEST_API_KEY}"},
        ) as c:
            response = await c.get("/api/v1/directories")

        assert response.status_code == 200
        directories = response.json()["directories"]

        assert len(directories) == 2, (
            f"Expected 2 directory entries, got {len(directories)}: {directories}"
        )

        by_name = {d["name"]: d for d in directories}
        assert "my_python_project" in by_name, (
            f"'my_python_project' not found among entries: {list(by_name.keys())}"
        )
        assert "my_flutter_app" in by_name, (
            f"'my_flutter_app' not found among entries: {list(by_name.keys())}"
        )
        assert by_name["my_python_project"]["tech_stack"] == "Python", (
            f"Expected tech_stack='Python' for pyproject.toml directory, "
            f"got '{by_name['my_python_project']['tech_stack']}'"
        )
        assert by_name["my_flutter_app"]["tech_stack"] == "Flutter", (
            f"Expected tech_stack='Flutter' for pubspec.yaml directory, "
            f"got '{by_name['my_flutter_app']['tech_stack']}'"
        )

    # ── (4) No 'path' key in any directory response ────────────────────────

    async def test_no_path_key_in_workspace_fallback_response(self, client):
        """(4a) Workspace-fallback response never includes a raw 'path' key."""
        response = await client.get("/api/v1/directories")
        assert response.status_code == 200
        assert_no_path_key(response.json())

    async def test_no_path_key_in_configured_dirs_response(self, tmp_path: Path):
        """(4b) Configured-allow-list response never includes a raw 'path' key."""
        from httpx import ASGITransport, AsyncClient

        py_dir = tmp_path / "py_proj"
        py_dir.mkdir()
        (py_dir / "pyproject.toml").write_text("[project]\nname = 'x'\n")

        flutter_dir = tmp_path / "flutter_proj"
        flutter_dir.mkdir()
        (flutter_dir / "pubspec.yaml").write_text("name: x\n")

        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "artifacts").mkdir()
        (workspace / "logs").mkdir()

        app, _tracker = _build_app_with_dirs(workspace, py_dir, flutter_dir)

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
            headers={"Authorization": f"Bearer {TEST_API_KEY}"},
        ) as c:
            response = await c.get("/api/v1/directories")

        assert response.status_code == 200
        assert_no_path_key(response.json())


# ── Test class: POST /api/v1/runs extended contract ────────────────────────


class TestRunStartExtendedContract:
    """POST /api/v1/runs — workspace_id validation and flag forwarding."""

    # ── (5) Invalid workspace_id → 422 ────────────────────────────────────

    async def test_unknown_workspace_id_returns_422_with_workspace_id_in_body(
        self, client
    ):
        """(5) workspace_id='nonexistent-id-xyz' → 422, body contains 'workspace_id'."""
        response = await client.post(
            "/api/v1/runs",
            json=_minimal_run_payload(workspace_id="nonexistent-id-xyz"),
        )
        assert response.status_code == 422, (
            f"Expected 422 for unknown workspace_id, got {response.status_code}"
        )
        # The response body must reference 'workspace_id' so the client
        # knows which field caused the rejection.
        assert "workspace_id" in response.text, (
            f"Response body must contain the string 'workspace_id'; "
            f"got: {response.text!r}"
        )

    # ── (6) Valid workspace_id → 202 ──────────────────────────────────────

    async def test_valid_workspace_id_returns_202(self, tmp_path: Path):
        """(6) POST /api/v1/runs with a valid workspace_id → 202."""
        from httpx import ASGITransport, AsyncClient

        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "artifacts").mkdir()
        (workspace / "logs").mkdir()

        app, _tracker = _build_app_with_dirs(workspace)

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
            headers={"Authorization": f"Bearer {TEST_API_KEY}"},
        ) as c:
            # Fetch a valid workspace_id from the directories endpoint
            dir_resp = await c.get("/api/v1/directories")
            assert dir_resp.status_code == 200
            directories = dir_resp.json()["directories"]
            assert len(directories) >= 1, (
                "Need at least one directory entry to obtain a valid workspace_id"
            )
            valid_workspace_id = directories[0]["id"]

            run_resp = await c.post(
                "/api/v1/runs",
                json=_minimal_run_payload(workspace_id=valid_workspace_id),
            )

        assert run_resp.status_code == 202, (
            f"Expected 202 for valid workspace_id, got {run_resp.status_code}: "
            f"{run_resp.text}"
        )

    # ── (7) mode + confirm forwarded to RunRequest ────────────────────────

    async def test_mode_and_confirm_true_forwarded_to_run_request(
        self, client, mock_tracker
    ):
        """(7) mode='balanced', confirm=True → RunRequest.mode=='balanced', confirm==True."""
        response = await client.post(
            "/api/v1/runs",
            json=_minimal_run_payload(mode="balanced", confirm=True),
        )
        assert response.status_code == 202, (
            f"Expected 202, got {response.status_code}: {response.text}"
        )

        assert mock_tracker.start_run.called, (
            "tracker.start_run() must be called when POST /api/v1/runs succeeds"
        )
        captured: object = mock_tracker.start_run.call_args.args[0]

        assert captured.mode == "balanced", (
            f"Expected RunRequest.mode='balanced', got {captured.mode!r}"
        )
        assert captured.confirm is True, (
            f"Expected RunRequest.confirm=True, got {captured.confirm!r}"
        )

    # ── (8) confirm=False not dropped as falsy ────────────────────────────

    async def test_confirm_false_is_not_silently_dropped(
        self, client, mock_tracker
    ):
        """(8) confirm=False → RunRequest.confirm is False (not coerced to None).

        A naive ``if run_request.confirm:`` guard would silently drop False;
        this test guards against that regression.
        """
        response = await client.post(
            "/api/v1/runs",
            json=_minimal_run_payload(confirm=False),
        )
        assert response.status_code == 202, (
            f"Expected 202, got {response.status_code}: {response.text}"
        )

        assert mock_tracker.start_run.called
        captured = mock_tracker.start_run.call_args.args[0]

        assert captured.confirm is False, (
            f"confirm=False must be preserved in RunRequest; "
            f"got {captured.confirm!r} — likely dropped by falsy check"
        )

    # ── (9) Original 2-field payload still works (backward compat) ────────

    async def test_original_two_field_payload_backward_compatible(self, client):
        """(9) Payload with only feature_request + workflow_type → 202 (legacy compat)."""
        response = await client.post(
            "/api/v1/runs",
            json={
                "feature_request": "Legacy client: no new fields present",
                "workflow_type": "bugfix",
            },
        )
        assert response.status_code == 202, (
            f"Legacy 2-field payload must still return 202; "
            f"got {response.status_code}: {response.text}"
        )

    # ── (10) checklist_verify=False not dropped as falsy ─────────────────

    async def test_checklist_verify_false_is_not_silently_dropped(
        self, client, mock_tracker
    ):
        """(10) checklist_verify=False → RunRequest.checklist_verify is False.

        Mirrors test (8): confirms that falsy booleans are never coerced to
        None before reaching RunTracker.
        """
        response = await client.post(
            "/api/v1/runs",
            json=_minimal_run_payload(checklist_verify=False),
        )
        assert response.status_code == 202, (
            f"Expected 202, got {response.status_code}: {response.text}"
        )

        assert mock_tracker.start_run.called
        captured = mock_tracker.start_run.call_args.args[0]

        assert captured.checklist_verify is False, (
            f"checklist_verify=False must be preserved in RunRequest; "
            f"got {captured.checklist_verify!r} — likely dropped by falsy check"
        )
