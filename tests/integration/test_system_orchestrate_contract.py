"""Integration tests: system orchestrate subprocess invocation contract.

TASK-010: Contract tests for SystemOrchestrateRunner and the start_run branch.

Test cases:
  (1) POST /api/v1/runs/start with use_system_orchestrate=true when orchestrate
      binary NOT on PATH → HTTP 422 with error body {\"error\": \"orchestrate binary not found on PATH\"}
  (2) POST /api/v1/runs/start with use_system_orchestrate=false (default) uses
      in-process path and does not invoke shutil.which
  (3) locate_binary() returns None for unknown binary (unit test)
  (4) build_cli_args() correctly maps all RunStartRequest fields to CLI args (unit test)
  (5) Dynamic workspace_id (not in frozen_map) accepted when projects_root configured

Pattern: follows tests/integration/test_runs_contract.py patterns.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from .conftest import TEST_API_KEY


# ── Helpers ────────────────────────────────────────────────────────────────


def _minimal_run_payload(**overrides) -> dict:
    """Minimum valid run-start payload."""
    return {
        "feature_request": "Add user onboarding screen",
        "workflow_type": "feature_development",
        **overrides,
    }


def _build_app(workspace: Path, projects_root: Path | None = None):
    """Build a create_mobile_app() with mocked RunTracker."""
    from orchestrator.mobile_api.app import create_mobile_app

    lines = [
        f'workspace_dir: "{workspace}"',
        f'workspace_root: "{workspace.parent}"',
        f'project_name: "{workspace.name}"',
        "max_budget_usd: 50.0",
        "max_concurrent_agents: 5",
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
    """Return a configured async HTTP client."""
    from httpx import ASGITransport, AsyncClient

    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        headers={"Authorization": f"Bearer {TEST_API_KEY}"},
    )


# ── Unit tests ─────────────────────────────────────────────────────────────


class TestLocateBinary:
    """Unit tests for locate_binary()."""

    def test_returns_none_when_binary_not_on_path(self):
        """(3) locate_binary() returns None when 'orchestrate' is not on PATH."""
        from orchestrator.mobile_api.system_runner import locate_binary

        with patch("shutil.which", return_value=None):
            result = locate_binary()

        assert result is None, (
            f"locate_binary() must return None when binary not on PATH; got {result!r}"
        )

    def test_returns_path_string_when_binary_found(self):
        """locate_binary() returns absolute path string when 'orchestrate' is found."""
        from orchestrator.mobile_api.system_runner import locate_binary

        fake_path = "/usr/local/bin/orchestrate"
        with patch("shutil.which", return_value=fake_path):
            result = locate_binary()

        assert result == fake_path, (
            f"locate_binary() must return the binary path; got {result!r}"
        )


class TestBuildCliArgs:
    """Unit tests for build_cli_args()."""

    def test_returns_valid_args_list(self, tmp_path: Path):
        """(4a) build_cli_args() returns a valid args list with required flags.

        Note: workspace is passed via cwd in start_subprocess_run(), not as
        a CLI arg. build_cli_args() only maps request fields to CLI flags.
        """
        from orchestrator.mobile_api.system_runner import build_cli_args

        workspace = tmp_path / "ws"
        workspace.mkdir()

        from orchestrator.mobile_api.models import RunStartRequest

        request = RunStartRequest(feature_request="Add tests")

        args = build_cli_args(request, "/usr/bin/orchestrate", workspace, "testrunid")

        assert args[0] == "/usr/bin/orchestrate"
        assert "--feature-request" in args
        assert "--run-id" in args
        rid_idx = args.index("--run-id")
        assert args[rid_idx + 1] == "testrunid"

    def test_maps_feature_request(self, tmp_path: Path):
        """(4b) build_cli_args() maps feature_request to --feature-request."""
        from orchestrator.mobile_api.system_runner import build_cli_args

        workspace = tmp_path / "ws"
        workspace.mkdir()

        from orchestrator.mobile_api.models import RunStartRequest

        request = RunStartRequest(feature_request="Implement dark mode")

        args = build_cli_args(request, "/usr/bin/orchestrate", workspace, "runid123")

        assert "--feature-request" in args, f"--feature-request missing; args={args}"
        fr_idx = args.index("--feature-request")
        assert args[fr_idx + 1] == "Implement dark mode", (
            f"feature_request not correctly mapped; got {args[fr_idx+1]!r}"
        )

    def test_maps_workflow_type(self, tmp_path: Path):
        """build_cli_args() maps workflow_type to --workflow."""
        from orchestrator.mobile_api.system_runner import build_cli_args

        workspace = tmp_path / "ws"
        workspace.mkdir()

        from orchestrator.mobile_api.models import RunStartRequest

        request = RunStartRequest(feature_request="Fix bug", workflow_type="bugfix")

        args = build_cli_args(request, "/usr/bin/orchestrate", workspace, "runid")

        assert "--workflow" in args, f"--workflow missing; args={args}"
        w_idx = args.index("--workflow")
        assert args[w_idx + 1] == "bugfix", f"workflow not mapped; got {args[w_idx+1]!r}"

    def test_omits_none_optional_fields(self, tmp_path: Path):
        """(4c) build_cli_args() omits CLI flags for None optional fields."""
        from orchestrator.mobile_api.system_runner import build_cli_args

        workspace = tmp_path / "ws"
        workspace.mkdir()

        from orchestrator.mobile_api.models import RunStartRequest

        request = RunStartRequest(feature_request="Test", workflow_type="bugfix")

        args = build_cli_args(request, "/usr/bin/orchestrate", workspace, "runid")

        assert "--phase" not in args, f"--phase should be omitted when None; args={args}"
        assert "--from-phase" not in args, f"--from-phase should be omitted; args={args}"
        assert "--mode" not in args, f"--mode should be omitted; args={args}"
        assert "--max-budget" not in args, f"--max-budget should be omitted; args={args}"
        assert "--debate" not in args, f"--debate should be omitted when False; args={args}"

    def test_maps_phase_when_set(self, tmp_path: Path):
        """build_cli_args() maps phase to --phase when set."""
        from orchestrator.mobile_api.system_runner import build_cli_args

        workspace = tmp_path / "ws"
        workspace.mkdir()

        from orchestrator.mobile_api.models import RunStartRequest

        request = RunStartRequest(
            feature_request="Test",
            workflow_type="feature_development",
            phase="architect",
            mode="balanced",
            max_budget_usd=25.0,
            debate=True,
            knowledge=True,
        )

        args = build_cli_args(request, "/usr/bin/orchestrate", workspace, "runid")

        assert "--phase" in args
        p_idx = args.index("--phase")
        assert args[p_idx + 1] == "architect"

        assert "--mode" in args
        m_idx = args.index("--mode")
        assert args[m_idx + 1] == "balanced"

        # max_budget_usd is NOT passed as a CLI arg (read from config file)
        assert "--max-budget" not in args

        assert "--debate" in args
        assert "--knowledge" in args

    def test_binary_is_first_arg(self, tmp_path: Path):
        """build_cli_args() always starts the args list with the binary path."""
        from orchestrator.mobile_api.system_runner import build_cli_args

        workspace = tmp_path / "ws"
        workspace.mkdir()

        from orchestrator.mobile_api.models import RunStartRequest

        request = RunStartRequest(feature_request="Test")

        binary = "/usr/local/bin/orchestrate"
        args = build_cli_args(request, binary, workspace, "runid")

        assert args[0] == binary, (
            f"First arg must be the binary path; got {args[0]!r}"
        )


# ── Integration tests ──────────────────────────────────────────────────────


class TestSystemOrchestrateBranch:
    """Integration tests for the use_system_orchestrate=True run start branch."""

    # ── (1) Binary not on PATH → 422 ───────────────────────────────────────

    async def test_returns_422_when_binary_not_on_path(self, tmp_path: Path):
        """(1) POST /api/v1/runs with use_system_orchestrate=true, binary absent → 422."""
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "artifacts").mkdir()
        (workspace / "logs").mkdir()

        app, _tracker = _build_app(workspace)

        with patch("orchestrator.mobile_api.system_runner.locate_binary", return_value=None):
            async with _get_authed_client(app) as client:
                response = await client.post(
                    "/api/v1/runs",
                    json=_minimal_run_payload(use_system_orchestrate=True),
                )

        assert response.status_code == 422, (
            f"Expected 422 when orchestrate binary not on PATH, "
            f"got {response.status_code}: {response.text}"
        )
        body = response.json()
        assert body.get("error") == "orchestrate binary not found on PATH", (
            f"Error body must be exactly "
            f'{{"error": "orchestrate binary not found on PATH"}}; got: {body!r}'
        )

    # ── (2) use_system_orchestrate=false uses in-process path ──────────────

    async def test_false_default_uses_inprocess_path(self, tmp_path: Path):
        """(2) use_system_orchestrate=false (default) uses RunTracker, not subprocess."""
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "artifacts").mkdir()
        (workspace / "logs").mkdir()

        app, mock_tracker = _build_app(workspace)

        with patch("shutil.which") as mock_which:
            async with _get_authed_client(app) as client:
                response = await client.post(
                    "/api/v1/runs",
                    json=_minimal_run_payload(use_system_orchestrate=False),
                )

        assert response.status_code == 202, (
            f"Expected 202 for in-process run, got {response.status_code}: {response.text}"
        )
        # shutil.which should NOT have been called — we're using the in-process path
        mock_which.assert_not_called()

        # RunTracker.start_run SHOULD have been called
        assert mock_tracker.start_run.called, (
            "RunTracker.start_run() must be called for in-process path"
        )

    async def test_default_payload_uses_inprocess_path(self, tmp_path: Path):
        """Payload without use_system_orchestrate field defaults to in-process path."""
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "artifacts").mkdir()
        (workspace / "logs").mkdir()

        app, mock_tracker = _build_app(workspace)

        async with _get_authed_client(app) as client:
            response = await client.post(
                "/api/v1/runs",
                json={
                    "feature_request": "Legacy client payload",
                    "workflow_type": "bugfix",
                    # use_system_orchestrate is absent — should default to False
                },
            )

        assert response.status_code == 202, (
            f"Expected 202 for legacy payload, got {response.status_code}: {response.text}"
        )
        assert mock_tracker.start_run.called, "RunTracker.start_run() must be called"

    # ── (5) Dynamic workspace_id resolution ────────────────────────────────

    async def test_dynamic_workspace_id_accepted_when_projects_root_configured(
        self, tmp_path: Path
    ):
        """(5) Dynamic workspace_id (not in frozen_map) accepted when projects_root configured."""
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "artifacts").mkdir()
        (workspace / "logs").mkdir()

        projects_root = tmp_path / "projects"
        projects_root.mkdir()

        # Create a project subdirectory
        project_dir = projects_root / "my_project"
        project_dir.mkdir()

        app, mock_tracker = _build_app(workspace, projects_root=projects_root)

        async with _get_authed_client(app) as client:
            # Get the dynamic ID for my_project via the directory API
            root_resp = await client.get("/api/v1/directories/root")
            assert root_resp.status_code == 200, (
                f"GET /directories/root failed: {root_resp.text}"
            )
            root_id = root_resp.json()["id"]

            children_resp = await client.get(f"/api/v1/directories/{root_id}/children")
            assert children_resp.status_code == 200
            children = children_resp.json()["entries"]
            assert len(children) == 1
            dynamic_workspace_id = children[0]["id"]

            # Start a run using the dynamic workspace_id
            response = await client.post(
                "/api/v1/runs",
                json=_minimal_run_payload(workspace_id=dynamic_workspace_id),
            )

        assert response.status_code == 202, (
            f"Expected 202 for dynamic workspace_id, "
            f"got {response.status_code}: {response.text}"
        )
        assert mock_tracker.start_run.called, "RunTracker.start_run() must be called"

        # Verify the workspace_dir_override was set to the project directory
        captured = mock_tracker.start_run.call_args.args[0]
        assert captured.workspace_dir_override is not None, (
            "workspace_dir_override must be set for dynamic workspace_id"
        )
        assert "my_project" in captured.workspace_dir_override, (
            f"workspace_dir_override should point to 'my_project'; "
            f"got: {captured.workspace_dir_override!r}"
        )

    async def test_unknown_workspace_id_returns_422_even_with_projects_root(
        self, tmp_path: Path
    ):
        """Dynamic workspace lookup returns 422 when ID unknown in both maps."""
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "artifacts").mkdir()
        (workspace / "logs").mkdir()

        projects_root = tmp_path / "projects"
        projects_root.mkdir()

        app, _ = _build_app(workspace, projects_root=projects_root)

        async with _get_authed_client(app) as client:
            response = await client.post(
                "/api/v1/runs",
                json=_minimal_run_payload(workspace_id="completely-unknown-id-xyz"),
            )

        assert response.status_code == 422, (
            f"Expected 422 for unknown workspace_id, got {response.status_code}: {response.text}"
        )
        assert "workspace_id" in response.text.lower(), (
            f"Response body should reference 'workspace_id'; got: {response.text!r}"
        )


class TestSshConfigEndpoint:
    """Tests for GET /api/v1/ssh/config (no auth required)."""

    async def test_ssh_config_no_auth_required(self, tmp_path: Path):
        """GET /api/v1/ssh/config must be accessible without authentication."""
        from httpx import ASGITransport, AsyncClient

        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "artifacts").mkdir()
        (workspace / "logs").mkdir()

        app, _ = _build_app(workspace)

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
            # No Authorization header
        ) as client:
            response = await client.get("/api/v1/ssh/config")

        assert response.status_code == 200, (
            f"GET /api/v1/ssh/config must return 200 without auth; "
            f"got {response.status_code}: {response.text}"
        )

    async def test_ssh_config_returns_correct_fields(self, tmp_path: Path):
        """GET /api/v1/ssh/config returns configured and host_reachable boolean fields."""
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "artifacts").mkdir()
        (workspace / "logs").mkdir()

        app, _ = _build_app(workspace)

        async with _get_authed_client(app) as client:
            response = await client.get("/api/v1/ssh/config")

        assert response.status_code == 200
        body = response.json()

        assert "configured" in body, f"Response must have 'configured' field; got: {body}"
        assert "host_reachable" in body, f"Response must have 'host_reachable' field; got: {body}"
        assert isinstance(body["configured"], bool), (
            f"'configured' must be a boolean; got: {type(body['configured'])}"
        )
        assert isinstance(body["host_reachable"], bool), (
            f"'host_reachable' must be a boolean; got: {type(body['host_reachable'])}"
        )

    async def test_ssh_config_never_exposes_sensitive_info(self, tmp_path: Path):
        """GET /api/v1/ssh/config never includes credentials or server details."""
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "artifacts").mkdir()
        (workspace / "logs").mkdir()

        app, _ = _build_app(workspace)

        async with _get_authed_client(app) as client:
            response = await client.get("/api/v1/ssh/config")

        body = response.json()
        sensitive_keys = {"password", "key", "token", "secret", "username", "user", "host"}
        for key in body.keys():
            assert key.lower() not in sensitive_keys, (
                f"Response must not include sensitive field '{key}'; got: {body}"
            )

    async def test_ssh_config_responds_fast_when_port_closed(self, tmp_path: Path):
        """GET /api/v1/ssh/config responds quickly when SSH port is unreachable."""
        import asyncio
        import time

        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "artifacts").mkdir()
        (workspace / "logs").mkdir()

        # Configure a port that is definitely not open (use a high ephemeral port)
        lines = [
            f'workspace_dir: "{workspace}"',
            f'workspace_root: "{workspace.parent}"',
            f'project_name: "{workspace.name}"',
            "max_budget_usd: 50.0",
            "max_concurrent_agents: 5",
            "ssh_port: 19999",  # Unlikely to be open
            f'projects_root: "{workspace}"',  # Makes configured=True
        ]
        config_path = workspace / "config.yaml"
        config_path.write_text("\n".join(lines) + "\n")

        from orchestrator.mobile_api.app import create_mobile_app
        app = create_mobile_app(workspace_dir=workspace, config_path=config_path)

        start = time.monotonic()
        async with _get_authed_client(app) as client:
            response = await client.get("/api/v1/ssh/config")
        elapsed = time.monotonic() - start

        assert response.status_code == 200
        body = response.json()
        assert body["host_reachable"] is False, (
            f"Unreachable port must set host_reachable=false; got: {body}"
        )
        # Must respond within 4 seconds (2s probe + overhead)
        assert elapsed < 4.0, (
            f"SSH config endpoint took {elapsed:.1f}s — must respond within 4 seconds"
        )
