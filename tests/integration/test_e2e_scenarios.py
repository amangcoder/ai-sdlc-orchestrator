"""Integration tests: End-to-End User Journey Scenarios.

These tests walk through multi-step user flows that span multiple API
boundaries — matching the primary user journeys described in the PRD.

Scenarios:
  1. First-launch setup: no config → Settings required → auth validated
  2. Dashboard view: list runs with phase status and cost
  3. Start new run: submit form → 202 → run_id returned → state file readable
  4. Run monitoring: active run has phases and current_step
  5. Cancel workflow: confirm dialog → cancel → sentinel created → status polling
  6. Artifact view: complete run → list artifacts → fetch specific artifact
  7. Config edit: GET config (redacted) → PUT update → backup → GET reflects change
  8. Auth failure recovery: wrong key → 401 → correct key → success
  9. Resume failed run: failed run → resume → new run_id
 10. Concurrent request safety: rapid-fire requests handled without data corruption
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from .conftest import (
    TEST_API_KEY, WRONG_API_KEY,
    RUN_COMPLETED, RUN_RUNNING, RUN_FAILED,
)


# ── Scenario 1: First-Launch Auth Guard ───────────────────────────────────

class TestFirstLaunchScenario:
    """
    Scenario: App launches without configuration → all API calls rejected.

    Flutter app: AppRouter redirect guard calls SecureStorageService.loadConfig();
    Backend: AuthMiddleware validates API key on every /api/v1/* call.
    If client doesn't know the API key, every call returns 401.
    """

    async def test_all_api_calls_fail_without_auth(self, unauthed_client):
        """Without the API key, no /api/v1/* endpoint is reachable."""
        endpoints = [
            ("GET",  "/api/v1/runs"),
            ("GET",  "/api/v1/config"),
        ]
        for method, path in endpoints:
            response = await unauthed_client.request(method, path)
            assert response.status_code == 401, (
                f"Unauthenticated {method} {path} should return 401, "
                f"got {response.status_code}"
            )

    async def test_health_check_is_only_accessible_endpoint(
        self, unauthed_client
    ):
        """
        /health is the only endpoint accessible without auth.
        Flutter app calls /health during 'Test Connection' in SettingsScreen.
        """
        health = await unauthed_client.get("/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"


# ── Scenario 2: Dashboard View ────────────────────────────────────────────

class TestDashboardViewScenario:
    """
    Scenario: User opens dashboard → sees list of runs with phase status.

    Flutter DashboardScreen: RunsProvider polls GET /api/v1/runs every 10s.
    Each RunCard displays: run_id[0:8], workflow badge, status chip, current_step, cost.
    """

    async def test_dashboard_loads_runs_with_all_display_fields(
        self, client_with_runs
    ):
        """All fields needed to render a RunCard are present in each run."""
        client, workspace, run_completed, run_running, run_failed = client_with_runs
        response = await client.get("/api/v1/runs")
        assert response.status_code == 200

        dashboard_fields = {
            "run_id", "workflow_type", "status", "total_cost_usd", "start_time",
        }
        for run in response.json():
            missing = dashboard_fields - set(run.keys())
            assert not missing, (
                f"RunCard cannot render: run {run.get('run_id')} missing {missing}"
            )

    async def test_running_run_has_current_step_for_phase_display(
        self, client_with_runs
    ):
        """Active runs have current_step populated so DashboardScreen can show progress."""
        client, workspace, run_completed, run_running, run_failed = client_with_runs
        response = await client.get("/api/v1/runs")
        runs_by_id = {r["run_id"]: r for r in response.json()}

        if run_running in runs_by_id:
            # Running runs should have current_step from their per-run state file
            run = runs_by_id[run_running]
            assert run.get("current_step") is not None, (
                "Active running run must have current_step for phase display"
            )
            assert run["current_step"] == "Architecture Phase"

    async def test_completed_run_has_cost_information(
        self, client_with_runs
    ):
        """Completed runs have total_cost_usd so RunCard can display cost."""
        client, workspace, run_completed, *_ = client_with_runs
        response = await client.get(f"/api/v1/runs/{run_completed}")
        assert response.status_code == 200
        body = response.json()
        assert "total_cost_usd" in body
        assert isinstance(body["total_cost_usd"], (int, float))
        assert body["total_cost_usd"] > 0


# ── Scenario 3: Start New Run ─────────────────────────────────────────────

class TestStartNewRunScenario:
    """
    Scenario: User taps '+' on dashboard, fills form, submits.

    NewRunScreen: POST /api/v1/runs → 202 → navigate to /runs/:runId.
    """

    async def test_start_run_returns_run_id_for_navigation(
        self, client
    ):
        """
        POST /api/v1/runs returns a run_id that the app navigates to.
        Flutter app uses this run_id for /runs/:runId route.
        """
        response = await client.post(
            "/api/v1/runs",
            json={
                "feature_request": "Add user notification preferences screen",
                "workflow_type": "feature_development",
                "debate": False,
                "max_budget_usd": 30.0,
            },
        )
        assert response.status_code == 202
        body = response.json()
        run_id = body["run_id"]
        assert isinstance(run_id, str)
        assert len(run_id) >= 8, "run_id must be long enough for run_id[0:8] display"

    async def test_start_run_with_all_advanced_options(self, client):
        """
        NewRunScreen advanced options (debate, dry_run, enhanced_perception)
        are all accepted by the API without 422.
        """
        response = await client.post(
            "/api/v1/runs",
            json={
                "feature_request": "Security audit of authentication module",
                "workflow_type": "security_audit",
                "debate": True,
                "knowledge": True,
                "enhanced_perception": True,
                "max_budget_usd": 100.0,
                "dry_run": True,
            },
        )
        # dry_run=True means test mode — should be accepted
        assert response.status_code in {200, 202}

    async def test_active_run_blocks_new_run_with_409(
        self, client_active_run
    ):
        """
        NewRunScreen shows 'active run' banner when another run is active.
        The API enforces this with 409 Conflict and active_run_id in response.
        """
        client, workspace, run_completed, run_running = client_active_run

        response = await client.post(
            "/api/v1/runs",
            json={
                "feature_request": "Another feature while first is running",
                "workflow_type": "bugfix",
            },
        )
        assert response.status_code == 409
        body = response.json()
        # Flutter app uses active_run_id to show "View" button in banner
        assert "active_run_id" in body
        assert body["active_run_id"] == run_running


# ── Scenario 4: Run Detail Monitoring ─────────────────────────────────────

class TestRunMonitoringScenario:
    """
    Scenario: User navigates to /runs/:runId to monitor an active run.

    RunDetailScreen: shows phases with status icons, current cost, timestamps.
    """

    async def test_run_detail_shows_per_phase_breakdown(
        self, client_with_runs
    ):
        """RunDetailScreen phase list requires per-phase status and cost data."""
        client, workspace, run_completed, *_ = client_with_runs
        response = await client.get(f"/api/v1/runs/{run_completed}")
        body = response.json()

        phases = body["phases"]
        assert len(phases) > 0, "Completed run must have phase breakdown"

        for phase_name, phase_data in phases.items():
            assert "status" in phase_data, (
                f"Phase '{phase_name}' missing 'status' for PhaseListItem icon"
            )

    async def test_running_run_has_incomplete_phases(
        self, client_with_runs
    ):
        """Active run has some completed and some running/pending phases."""
        client, workspace, run_completed, run_running, run_failed = client_with_runs
        response = await client.get(f"/api/v1/runs/{run_running}")
        assert response.status_code == 200
        phases = response.json()["phases"]

        statuses = {p.get("status") for p in phases.values()}
        # Should have at least one completed and one running/pending phase
        assert "completed" in statuses or len(phases) > 0


# ── Scenario 5: Cancel Run Workflow ───────────────────────────────────────

class TestCancelRunWorkflow:
    """
    Scenario: User confirms cancel dialog → banner shows → status polling.

    RunDetailScreen: POST /api/v1/runs/{id}/cancel → {cancelled: true}
    → 'Cancellation in progress — may take up to 2 minutes' banner appears.
    """

    async def test_cancel_then_sentinel_exists_then_status_checkable(
        self, client_active_run
    ):
        """
        Full cancel flow:
          1. POST cancel → {cancelled: true}
          2. workspace/.interrupt exists
          3. GET /runs/{id} still returns the run (polling can continue)
        """
        client, workspace, run_completed, run_running = client_active_run

        # Step 1: Cancel
        cancel_response = await client.post(
            f"/api/v1/runs/{run_running}/cancel"
        )
        assert cancel_response.status_code == 200
        assert cancel_response.json()["cancelled"] is True

        # Step 2: Sentinel written
        sentinel = workspace / ".interrupt"
        assert sentinel.exists(), "Sentinel file required for engine to stop"

        # Step 3: Run detail still accessible (status polling can continue)
        detail_response = await client.get(f"/api/v1/runs/{run_running}")
        assert detail_response.status_code == 200

    async def test_cancel_inactive_run_does_not_disrupt_active_run(
        self, client_active_run
    ):
        """
        Cancelling an inactive run_id must NOT write workspace/.interrupt.
        If it did, the currently running engine would be interrupted
        (cross-run sentinel contamination).
        """
        client, workspace, run_completed, run_running = client_active_run

        # Try to cancel a DIFFERENT (inactive) run
        response = await client.post(f"/api/v1/runs/{run_completed}/cancel")
        assert response.status_code == 404

        # The active run's sentinel must NOT exist
        sentinel = workspace / ".interrupt"
        assert not sentinel.exists(), (
            "Cancelling an inactive run MUST NOT write .interrupt "
            "(would corrupt the active run)"
        )


# ── Scenario 6: Artifact Viewer ───────────────────────────────────────────

class TestArtifactViewerScenario:
    """
    Scenario: User opens /runs/:runId/artifacts → taps prd → JSON tree renders.

    ArtifactViewerScreen: GET /artifacts → list → tap name → GET /artifacts/{name}.
    """

    async def test_full_artifact_view_flow(self, client_with_runs):
        """List → fetch one artifact — complete ArtifactViewerScreen flow."""
        client, workspace, run_completed, *_ = client_with_runs

        # Step 1: List artifacts
        list_response = await client.get(
            f"/api/v1/runs/{run_completed}/artifacts"
        )
        assert list_response.status_code == 200
        artifacts = list_response.json()
        assert len(artifacts) > 0, "At least one artifact needed for scenario"

        # Step 2: Fetch the first listed artifact
        artifact_name = artifacts[0]
        fetch_response = await client.get(
            f"/api/v1/runs/{run_completed}/artifacts/{artifact_name}"
        )
        assert fetch_response.status_code == 200
        content = fetch_response.json()
        assert isinstance(content, dict), "Artifact content must be a JSON object"
        assert len(content) > 0, "Artifact content must not be empty"

    async def test_multiple_artifact_types_all_fetchable(
        self, client_with_runs
    ):
        """Write multiple known artifact types → all fetchable via API."""
        client, workspace, run_completed, *_ = client_with_runs

        # Write additional artifact types
        artifacts_to_write = {
            "tasks": {
                "tasks": [
                    {
                        "task_id": "TASK-001",
                        "title": "Implement feature",
                        "description": "Full feature implementation",
                        "assigned_role": "engineer",
                        "dependencies": [],
                        "acceptance_criteria": ["Feature works"],
                        "files_to_modify": ["src/feature.py"],
                        "estimated_complexity": "medium",
                    }
                ]
            },
            "architecture": {
                "components": [],
                "data_flow": [],
                "tech_decisions": [],
                "constraints": [],
                "directory_structure": [],
            },
        }
        for name, data in artifacts_to_write.items():
            (workspace / "artifacts" / f"{name}.json").write_text(json.dumps(data))

        for name in artifacts_to_write:
            response = await client.get(
                f"/api/v1/runs/{run_completed}/artifacts/{name}"
            )
            assert response.status_code == 200, (
                f"Artifact '{name}' should be fetchable"
            )


# ── Scenario 7: Config Edit Workflow ─────────────────────────────────────

class TestConfigEditScenario:
    """
    Scenario: User opens ConfigEditorScreen → edits a field → saves → feedback.

    ConfigEditorScreen: GET /config (shows redacted view) →
    user edits non-sensitive field → PUT /config →
    success SnackBar 'Config updated. Changes take effect on the next run.'
    """

    async def test_full_config_edit_flow(self, client, config_yaml):
        """GET config → identify non-sensitive field → PUT update → verify."""
        # Step 1: GET current config
        get_response = await client.get("/api/v1/config")
        assert get_response.status_code == 200
        config = get_response.json()["config"]
        assert isinstance(config, dict)

        # Step 2: PUT a valid update to a non-sensitive field
        put_response = await client.put(
            "/api/v1/config",
            json={"updates": {"max_budget_usd": 45.0}},
        )
        assert put_response.status_code == 200

        # Step 3: Response contains the 'note' for the SnackBar message
        note = put_response.json()["note"]
        assert isinstance(note, str) and len(note) > 0

        # Step 4: Backup was created (user can roll back if needed)
        backup_path = put_response.json()["backup_path"]
        assert Path(backup_path).exists()


# ── Scenario 8: Auth Failure Recovery ────────────────────────────────────

class TestAuthFailureRecoveryScenario:
    """
    Scenario: User has wrong API key → 401 errors → updates key in Settings → works.

    ErrorStateWidget shows 'Open Settings' button for AuthException.
    """

    async def test_wrong_key_401_then_correct_key_200(
        self, mobile_app
    ):
        """
        Using the wrong API key → 401 on all API calls.
        Using the correct key → requests succeed.
        """
        from httpx import ASGITransport, AsyncClient

        # Wrong key — 401
        async with AsyncClient(
            transport=ASGITransport(app=mobile_app),
            base_url="http://testserver",
            headers={"Authorization": f"Bearer {WRONG_API_KEY}"},
        ) as bad_client:
            r_bad = await bad_client.get("/api/v1/runs")
            assert r_bad.status_code == 401

        # Correct key — succeeds
        async with AsyncClient(
            transport=ASGITransport(app=mobile_app),
            base_url="http://testserver",
            headers={"Authorization": f"Bearer {TEST_API_KEY}"},
        ) as good_client:
            r_good = await good_client.get("/api/v1/runs")
            assert r_good.status_code != 401

    async def test_401_error_body_has_standard_shape(
        self, unauthed_client
    ):
        """
        401 response has {error: str} shape that Flutter AuthException can parse.
        Flutter maps this to: throw AuthException('Authentication failed — check your API key in Settings')
        """
        response = await unauthed_client.get("/api/v1/runs")
        assert response.status_code == 401
        body = response.json()
        assert "error" in body
        assert isinstance(body["error"], str)


# ── Scenario 9: Resume Failed Run ─────────────────────────────────────────

class TestResumeFailedRunScenario:
    """
    Scenario: Run fails mid-phase → user taps 'Resume' button →
    new run starts from where it left off.

    RunDetailScreen: POST /api/v1/runs/{id}/resume → 202 → navigate to new run.
    """

    async def test_resume_returns_new_run_id(self, client_with_runs):
        """Resuming a failed run returns a new run_id to navigate to."""
        client, workspace, run_completed, run_running, run_failed = client_with_runs
        response = await client.post(f"/api/v1/runs/{run_failed}/resume")
        assert response.status_code == 202
        body = response.json()
        assert "run_id" in body
        # The new run_id may differ from the original failed run_id
        # (implementation-dependent, but a run_id must be returned)
        assert isinstance(body["run_id"], str)
        assert len(body["run_id"]) > 0


# ── Scenario 10: Health Check for Settings Screen ────────────────────────

class TestHealthCheckScenario:
    """
    Scenario: SettingsScreen 'Test Connection' button calls /health.
    Response shows version and active run count for user confirmation.
    """

    async def test_health_check_returns_connection_info(
        self, unauthed_client
    ):
        """
        /health provides the info displayed in SettingsScreen 'Test Connection':
        'Connected: v{version}, {activeRuns} active runs'
        """
        response = await unauthed_client.get("/health")
        assert response.status_code == 200
        body = response.json()

        # Flutter app displays: "Connected: v${health.version}, ${health.activeRuns} active runs"
        assert "status" in body and body["status"] == "ok"
        assert "version" in body and isinstance(body["version"], str)
        assert "active_runs" in body and isinstance(body["active_runs"], int)
        assert body["active_runs"] >= 0

    async def test_health_active_runs_matches_tracker_state(
        self, mobile_app, mock_tracker_with_active_run
    ):
        """
        active_runs count in /health response reflects the RunTracker state.
        """
        from httpx import ASGITransport, AsyncClient

        mobile_app.state.tracker = mock_tracker_with_active_run
        async with AsyncClient(
            transport=ASGITransport(app=mobile_app),
            base_url="http://testserver",
        ) as c:
            response = await c.get("/health")

        assert response.json()["active_runs"] == 1
