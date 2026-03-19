"""Integration tests: Runs Router ↔ RunTracker/RunDataReader boundary.

Boundaries tested:
  - HTTP client → RunsRouter → RunDataReader (list/detail read from filesystem)
  - HTTP client → RunsRouter → RunTracker (start/cancel/resume lifecycle)
  - State file augmentation: current_step from per-run state-{id}.json, NOT state.json
  - Rate limiting: same IP within 5 seconds → 429
  - Conflict detection: 409 when a run is already active
  - Cancel safety: inactive run_id → 404 with NO sentinel written

Contract assertions match the API contract defined in architecture.json.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from .conftest import TEST_API_KEY, RUN_COMPLETED, RUN_RUNNING, RUN_FAILED


# ── Helpers ────────────────────────────────────────────────────────────────

def _valid_start_payload(**overrides) -> dict:
    return {
        "feature_request": "Add real-time notifications for task completions",
        "workflow_type": "feature_development",
        "debate": False,
        "knowledge": None,
        "max_budget_usd": 50.0,
        "dry_run": False,
        "resume_run_id": None,
        **overrides,
    }


# ── GET /api/v1/runs ───────────────────────────────────────────────────────

class TestListRunsContract:
    """GET /api/v1/runs — shape, sorting, state augmentation."""

    async def test_returns_200_with_list(self, client_with_runs):
        client, workspace, run_completed, run_running, run_failed = client_with_runs
        response = await client.get("/api/v1/runs")
        assert response.status_code == 200
        body = response.json()
        assert isinstance(body, list)

    async def test_each_run_has_required_fields(self, client_with_runs):
        """Every RunSummaryResponse must have all contract-specified fields."""
        client, *_ = client_with_runs
        response = await client.get("/api/v1/runs")
        required_fields = {
            "run_id", "feature_request", "workflow_type", "status",
            "total_cost_usd", "start_time", "steps_completed", "steps_total",
        }
        for run in response.json():
            missing = required_fields - set(run.keys())
            assert not missing, f"Run {run.get('run_id')} missing fields: {missing}"

    async def test_current_step_is_read_from_per_run_state_file(
        self, client_with_runs
    ):
        """
        current_step must be read from workspace/state-{run_id}.json,
        NOT from the global workspace/state.json.

        Proof: we modify state-{RUN_COMPLETED}.json to a unique current_step
        string and verify it appears in the response — while state.json still
        contains the running run's state.
        """
        client, workspace, run_completed, run_running, run_failed = client_with_runs

        # Overwrite the completed run's per-run state file with a unique marker
        state_path = workspace / f"state-{run_completed}.json"
        state_data = json.loads(state_path.read_text())
        UNIQUE_STEP = "UniqueStepMarker_NotInGlobalState"
        state_data["current_step"] = UNIQUE_STEP
        state_path.write_text(json.dumps(state_data))

        # Ensure global state.json does NOT contain this marker
        global_state = json.loads((workspace / "state.json").read_text())
        assert global_state.get("current_step") != UNIQUE_STEP

        response = await client.get("/api/v1/runs")
        runs_by_id = {r["run_id"]: r for r in response.json()}

        assert run_completed in runs_by_id, "Completed run not in response"
        assert runs_by_id[run_completed]["current_step"] == UNIQUE_STEP, (
            "current_step was not read from per-run state-{id}.json"
        )

    async def test_runs_sorted_newest_first(self, client_with_runs):
        """Runs are returned newest-first by start_time."""
        client, *_ = client_with_runs
        response = await client.get("/api/v1/runs")
        runs = response.json()
        start_times = [r["start_time"] for r in runs]
        assert start_times == sorted(start_times, reverse=True), (
            "Runs must be sorted newest-first"
        )

    async def test_empty_workspace_returns_empty_list(self, client):
        """Empty workspace (no JSONL logs) → [] response."""
        response = await client.get("/api/v1/runs")
        assert response.status_code == 200
        assert response.json() == []

    async def test_at_most_20_runs_returned(self, workspace_with_runs, config_yaml):
        """List endpoint caps at 20 most recent runs."""
        workspace, *_ = workspace_with_runs
        # Create 25 additional run logs
        for i in range(25):
            run_id = f"extra{i:012d}"
            (workspace / "logs" / f"run-{run_id}.jsonl").write_text(
                json.dumps({"event": "run_start", "run_id": run_id,
                            "workflow_type": "bugfix"})
            )
        from orchestrator.mobile_api.app import create_mobile_app
        from httpx import ASGITransport, AsyncClient
        from unittest.mock import MagicMock
        tracker = MagicMock()
        tracker.active_run_ids.return_value = []
        app = create_mobile_app(workspace_dir=workspace, config_path=config_yaml)
        app.state.tracker = tracker
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
            headers={"Authorization": f"Bearer {TEST_API_KEY}"},
        ) as c:
            response = await c.get("/api/v1/runs")
        assert len(response.json()) <= 20


# ── GET /api/v1/runs/{run_id} ──────────────────────────────────────────────

class TestGetRunDetailContract:
    """GET /api/v1/runs/{run_id} — detail response shape and error handling."""

    async def test_existing_run_returns_200(self, client_with_runs):
        client, workspace, run_completed, *_ = client_with_runs
        response = await client.get(f"/api/v1/runs/{run_completed}")
        assert response.status_code == 200

    async def test_detail_response_has_required_fields(self, client_with_runs):
        """RunDetailResponse must include all summary fields plus phases."""
        client, workspace, run_completed, *_ = client_with_runs
        response = await client.get(f"/api/v1/runs/{run_completed}")
        body = response.json()
        required = {
            "run_id", "feature_request", "workflow_type", "status",
            "total_cost_usd", "start_time", "phases",
        }
        missing = required - set(body.keys())
        assert not missing, f"RunDetailResponse missing fields: {missing}"

    async def test_phases_field_is_dict(self, client_with_runs):
        """phases field in RunDetailResponse must be a dict (not a list)."""
        client, workspace, run_completed, *_ = client_with_runs
        response = await client.get(f"/api/v1/runs/{run_completed}")
        assert isinstance(response.json()["phases"], dict)

    async def test_unknown_run_id_returns_404(self, client_with_runs):
        """GET /api/v1/runs/nonexistent → 404 with {error: 'Run not found'}."""
        client, *_ = client_with_runs
        response = await client.get("/api/v1/runs/nonexistentrunid")
        assert response.status_code == 404
        body = response.json()
        assert "error" in body
        assert "not found" in body["error"].lower()

    async def test_detail_reads_from_per_run_state_file(self, client_with_runs):
        """
        GET /api/v1/runs/{id} reads workspace/state-{id}.json directly,
        never the global state.json (which is overwritten by each new run).
        """
        client, workspace, run_completed, run_running, *_ = client_with_runs

        # Corrupt global state.json so it refers to a different run_id
        (workspace / "state.json").write_text(
            json.dumps({"run_id": "completely-different-id", "status": "failed"})
        )

        # Per-run state file for completed run should still be readable
        response = await client.get(f"/api/v1/runs/{run_completed}")
        assert response.status_code == 200
        assert response.json()["run_id"] == run_completed


# ── POST /api/v1/runs ──────────────────────────────────────────────────────

class TestStartRunContract:
    """POST /api/v1/runs — start, conflict, rate limit."""

    async def test_start_run_returns_202(self, client):
        """Valid StartRunRequest → 202 Accepted with run_id and status."""
        response = await client.post("/api/v1/runs", json=_valid_start_payload())
        assert response.status_code == 202
        body = response.json()
        assert "run_id" in body
        assert "status" in body

    async def test_start_run_response_shape(self, client):
        """StartRunResponse contains run_id (str) and status (str)."""
        response = await client.post("/api/v1/runs", json=_valid_start_payload())
        body = response.json()
        assert isinstance(body["run_id"], str)
        assert len(body["run_id"]) > 0
        assert isinstance(body["status"], str)

    async def test_start_run_while_active_returns_409(self, client_active_run):
        """
        POST /api/v1/runs while another run is active → 409 Conflict.
        Response body must include active_run_id so Flutter can navigate to it.
        """
        client, workspace, run_completed, run_running = client_active_run
        response = await client.post("/api/v1/runs", json=_valid_start_payload())
        assert response.status_code == 409
        body = response.json()
        assert "error" in body
        assert "active_run_id" in body
        assert isinstance(body["active_run_id"], str)

    async def test_start_run_rate_limit_429(self, client):
        """
        Two POST /api/v1/runs calls from the same source IP within 5 seconds
        → second call returns 429 Too Many Requests.
        """
        # First call should succeed
        r1 = await client.post("/api/v1/runs", json=_valid_start_payload())
        assert r1.status_code == 202

        # Second call immediately after → rate limited
        r2 = await client.post("/api/v1/runs", json=_valid_start_payload())
        assert r2.status_code == 429

    async def test_missing_feature_request_returns_422(self, client):
        """StartRunRequest without required feature_request → 422 Unprocessable Entity."""
        response = await client.post("/api/v1/runs", json={"workflow_type": "bugfix"})
        assert response.status_code == 422

    async def test_invalid_workflow_type_is_validated(self, client):
        """workflow_type value outside the allowed enum → 422."""
        response = await client.post(
            "/api/v1/runs",
            json=_valid_start_payload(workflow_type="invalid_workflow_xyz"),
        )
        # Should return 422 (Pydantic validation) or 400 — never 500
        assert response.status_code in {400, 422}


# ── POST /api/v1/runs/{id}/cancel ─────────────────────────────────────────

class TestCancelRunContract:
    """Cancel endpoint — sentinel file safety and response shapes."""

    async def test_cancel_active_run_returns_cancelled_true(
        self, client_active_run
    ):
        """POST /api/v1/runs/{active_id}/cancel → {cancelled: true}."""
        client, workspace, run_completed, run_running = client_active_run
        response = await client.post(f"/api/v1/runs/{run_running}/cancel")
        assert response.status_code == 200
        assert response.json() == {"cancelled": True}

    async def test_cancel_active_run_creates_interrupt_sentinel(
        self, client_active_run
    ):
        """
        Cancelling an active run writes workspace/.interrupt within the request.
        The sentinel file is the mechanism used by OrchestratorEngine to stop
        at the next phase boundary.
        """
        client, workspace, run_completed, run_running = client_active_run
        sentinel = workspace / ".interrupt"
        assert not sentinel.exists(), "Sentinel should not pre-exist"

        await client.post(f"/api/v1/runs/{run_running}/cancel")
        assert sentinel.exists(), "workspace/.interrupt must be created on cancel"

    async def test_cancel_inactive_run_returns_404(self, client_with_runs):
        """
        POST /api/v1/runs/{inactive_id}/cancel → 404.
        CRITICAL: workspace/.interrupt must NOT be created because writing the
        sentinel would stop a DIFFERENT currently-running engine process.
        """
        client, workspace, run_completed, run_running, run_failed = client_with_runs
        sentinel = workspace / ".interrupt"

        # Cancel a run that is NOT tracked as active
        response = await client.post(f"/api/v1/runs/{run_completed}/cancel")
        assert response.status_code == 404

        # Sentinel must NOT exist
        assert not sentinel.exists(), (
            "workspace/.interrupt MUST NOT be written when run_id is not active "
            "(cross-run sentinel contamination)"
        )

    async def test_cancel_unknown_run_id_returns_404(self, client_with_runs):
        """Cancelling a completely unknown run_id → 404."""
        client, workspace, *_ = client_with_runs
        response = await client.post("/api/v1/runs/completelyunknownrunid/cancel")
        assert response.status_code == 404


# ── POST /api/v1/runs/{id}/resume ─────────────────────────────────────────

class TestResumeRunContract:
    """Resume endpoint contract."""

    async def test_resume_failed_run_returns_202(self, client_with_runs):
        """POST /api/v1/runs/{failed_id}/resume → 202 with new run_id."""
        client, workspace, run_completed, run_running, run_failed = client_with_runs
        response = await client.post(f"/api/v1/runs/{run_failed}/resume")
        # 202 Accepted when no other run is active
        assert response.status_code == 202
        body = response.json()
        assert "run_id" in body

    async def test_resume_while_run_active_returns_409(
        self, client_active_run
    ):
        """Resuming when another run is already active → 409 Conflict."""
        client, workspace, run_completed, run_running = client_active_run
        response = await client.post(f"/api/v1/runs/{run_completed}/resume")
        assert response.status_code == 409


# ── GET /api/v1/runs?workspace_id={id} ────────────────────────────────────

class TestWorkspaceIdFilterContract:
    """GET /api/v1/runs with workspace_id query parameter — TASK-003 / REQ-021."""

    async def test_workspace_id_filter_returns_only_matching_runs(
        self, workspace_with_runs, config_yaml
    ):
        """
        GET /api/v1/runs?workspace_id=X returns only runs where state.workspace_id == X.
        Runs with a different or missing workspace_id are excluded.
        """
        import uuid
        from httpx import ASGITransport, AsyncClient
        from unittest.mock import MagicMock

        workspace, run_completed, run_running, run_failed = workspace_with_runs

        # Tag RUN_COMPLETED with a workspace_id; leave others untagged
        target_workspace_id = uuid.uuid4().hex
        state_path = workspace / f"state-{run_completed}.json"
        state = json.loads(state_path.read_text())
        state["workspace_id"] = target_workspace_id
        state_path.write_text(json.dumps(state))

        from orchestrator.mobile_api.app import create_mobile_app
        tracker = MagicMock()
        tracker.active_run_ids.return_value = []
        app = create_mobile_app(workspace_dir=workspace, config_path=config_yaml)
        app.state.tracker = tracker

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
            headers={"Authorization": f"Bearer {TEST_API_KEY}"},
        ) as client:
            response = await client.get(
                f"/api/v1/runs?workspace_id={target_workspace_id}"
            )

        assert response.status_code == 200
        runs = response.json()
        # Only the tagged run should appear
        assert len(runs) == 1
        assert runs[0]["run_id"] == run_completed

    async def test_workspace_id_filter_empty_when_no_match(
        self, workspace_with_runs, config_yaml
    ):
        """
        GET /api/v1/runs?workspace_id=nonexistent returns [] (not 404).
        """
        import uuid
        from httpx import ASGITransport, AsyncClient
        from unittest.mock import MagicMock

        workspace, *_ = workspace_with_runs
        nonexistent_id = "ffffffffffffffffffffffffffffffff"

        from orchestrator.mobile_api.app import create_mobile_app
        tracker = MagicMock()
        tracker.active_run_ids.return_value = []
        app = create_mobile_app(workspace_dir=workspace, config_path=config_yaml)
        app.state.tracker = tracker

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
            headers={"Authorization": f"Bearer {TEST_API_KEY}"},
        ) as client:
            response = await client.get(
                f"/api/v1/runs?workspace_id={nonexistent_id}"
            )

        assert response.status_code == 200
        assert response.json() == []

    async def test_workspace_id_filter_cap_is_100_not_20(
        self, workspace_with_runs, config_yaml
    ):
        """
        GET /api/v1/runs?workspace_id=X allows up to 100 results (not the default 20 cap).
        Verify the cap is applied correctly when workspace_id is provided.
        """
        import uuid
        from httpx import ASGITransport, AsyncClient
        from unittest.mock import MagicMock

        workspace, run_completed, run_running, run_failed = workspace_with_runs
        target_workspace_id = uuid.uuid4().hex

        # Tag all 3 existing runs with the workspace_id
        for run_id in [run_completed, run_running, run_failed]:
            state_path = workspace / f"state-{run_id}.json"
            state = json.loads(state_path.read_text())
            state["workspace_id"] = target_workspace_id
            state_path.write_text(json.dumps(state))
            # Create JSONL entries for each tagged run if missing
            log_path = workspace / "logs" / f"run-{run_id}.jsonl"
            if not log_path.exists():
                log_path.write_text(
                    json.dumps({"event": "run_start", "run_id": run_id, "workflow_type": "bugfix"})
                )

        from orchestrator.mobile_api.app import create_mobile_app
        tracker = MagicMock()
        tracker.active_run_ids.return_value = []
        app = create_mobile_app(workspace_dir=workspace, config_path=config_yaml)
        app.state.tracker = tracker

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
            headers={"Authorization": f"Bearer {TEST_API_KEY}"},
        ) as client:
            # Without filter — capped at 20
            no_filter = await client.get("/api/v1/runs")
            assert len(no_filter.json()) <= 20

            # With workspace_id filter — should return all 3 (well within 100 cap)
            filtered = await client.get(
                f"/api/v1/runs?workspace_id={target_workspace_id}"
            )
            assert filtered.status_code == 200
            # All 3 runs should be returned (under the 100 cap)
            assert len(filtered.json()) == 3

    async def test_no_workspace_id_still_caps_at_20(self, workspace_with_runs, config_yaml):
        """
        GET /api/v1/runs without workspace_id still returns at most 20 runs.
        Backward-compat check — REQ-021.
        """
        from httpx import ASGITransport, AsyncClient
        from unittest.mock import MagicMock

        workspace, *_ = workspace_with_runs

        # Add 25 extra runs
        for i in range(25):
            extra_id = f"extra{i:012d}"
            (workspace / "logs" / f"run-{extra_id}.jsonl").write_text(
                json.dumps({"event": "run_start", "run_id": extra_id,
                            "workflow_type": "bugfix"})
            )

        from orchestrator.mobile_api.app import create_mobile_app
        tracker = MagicMock()
        tracker.active_run_ids.return_value = []
        app = create_mobile_app(workspace_dir=workspace, config_path=config_yaml)
        app.state.tracker = tracker

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
            headers={"Authorization": f"Bearer {TEST_API_KEY}"},
        ) as client:
            response = await client.get("/api/v1/runs")

        assert response.status_code == 200
        assert len(response.json()) <= 20
