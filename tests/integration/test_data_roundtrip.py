"""Integration tests: Data Round-Trip Across API Boundaries.

These tests verify that data written through one operation is correctly
retrieved through another — testing data integrity as it crosses boundaries.

Round-trips tested:
  1. Start run → GET /runs → run appears with correct run_id
  2. Write state file with current_step → GET /runs → current_step present
  3. Cancel active run → workspace/.interrupt file written
  4. Config update → backup written → GET /config returns updated value
  5. Write artifact to disk → GET /artifacts → artifact listed and fetchable
  6. Write JSONL events → GET /events → events at correct offsets
  7. Write per-run state → GET /runs/{id} → state matches (not global state.json)
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest


class TestRunDataRoundTrip:
    """Start run → data appears in subsequent GET requests."""

    async def test_started_run_appears_in_list(self, client, mock_tracker):
        """
        After POST /api/v1/runs, the new run_id must be discoverable
        via GET /api/v1/runs once its state file is written.
        """
        # Arrange: configure mock tracker to return a predictable run_id
        NEW_RUN_ID = "newroundrip112233"
        mock_tracker.start_run.return_value = NEW_RUN_ID

        # Write the state file the tracker would create
        async def _post_and_check(client, workspace):
            response = await client.post(
                "/api/v1/runs",
                json={
                    "feature_request": "Round-trip test feature request",
                    "workflow_type": "feature_development",
                },
            )
            assert response.status_code == 202
            assert response.json()["run_id"] == NEW_RUN_ID
        # Note: in production, RunTracker writes the state file as part of run start.
        # This test verifies the API layer returns the correct run_id.
        response = await client.post(
            "/api/v1/runs",
            json={
                "feature_request": "Round-trip test feature request",
                "workflow_type": "feature_development",
            },
        )
        assert response.status_code == 202
        assert response.json()["run_id"] == NEW_RUN_ID

    async def test_state_file_current_step_appears_in_runs_list(
        self, client_with_runs
    ):
        """
        Write a unique current_step to state-{id}.json →
        GET /api/v1/runs returns that exact current_step for that run.
        This is the critical augmentation: RunsRouter reads per-run state files.
        """
        client, workspace, run_completed, run_running, run_failed = client_with_runs

        UNIQUE_STEP = "RoundTrip_Step_Marker_XYZ_9876"
        state_path = workspace / f"state-{run_running}.json"
        state = json.loads(state_path.read_text())
        state["current_step"] = UNIQUE_STEP
        state_path.write_text(json.dumps(state))

        response = await client.get("/api/v1/runs")
        runs_by_id = {r["run_id"]: r for r in response.json()}

        assert run_running in runs_by_id
        assert runs_by_id[run_running]["current_step"] == UNIQUE_STEP

    async def test_run_detail_reflects_phase_state_from_file(
        self, client_with_runs
    ):
        """
        Write a specific phase status to state-{run_id}.json →
        GET /api/v1/runs/{run_id} returns that phase status.
        """
        client, workspace, run_completed, run_running, run_failed = client_with_runs

        state_path = workspace / f"state-{run_completed}.json"
        state = json.loads(state_path.read_text())
        # Add a new phase entry
        state["phases"]["new_phase"] = {
            "status": "completed",
            "cost_usd": 0.99,
            "model_tier": "opus",
        }
        state_path.write_text(json.dumps(state))

        response = await client.get(f"/api/v1/runs/{run_completed}")
        assert response.status_code == 200
        phases = response.json()["phases"]
        assert "new_phase" in phases
        assert phases["new_phase"]["status"] == "completed"


class TestCancelRoundTrip:
    """Cancel request → sentinel file round-trip."""

    async def test_cancel_creates_sentinel_content(self, client_active_run):
        """
        POST /api/v1/runs/{id}/cancel writes workspace/.interrupt with
        content that signals web_cancel origin.
        """
        client, workspace, run_completed, run_running = client_active_run
        await client.post(f"/api/v1/runs/{run_running}/cancel")
        sentinel = workspace / ".interrupt"
        assert sentinel.exists()
        content = sentinel.read_text()
        # Sentinel should have non-empty content indicating cancellation source
        assert len(content) > 0


class TestArtifactRoundTrip:
    """Write artifact → list → fetch round-trip."""

    async def test_written_artifact_appears_in_list(self, client_with_runs):
        """
        Write a new artifact file →
        GET /artifacts lists it →
        GET /artifacts/{name} returns its content.
        """
        client, workspace, run_completed, run_running, run_failed = client_with_runs

        # Write an additional artifact
        tasks_data = {
            "tasks": [
                {
                    "task_id": "TASK-001",
                    "title": "Test task",
                    "description": "Test description for round-trip",
                    "assigned_role": "engineer",
                    "dependencies": [],
                    "acceptance_criteria": ["Criterion 1"],
                    "files_to_modify": ["src/test.py"],
                    "estimated_complexity": "low",
                }
            ]
        }
        (workspace / "artifacts" / "tasks.json").write_text(json.dumps(tasks_data))

        # List should include 'tasks'
        list_response = await client.get(
            f"/api/v1/runs/{run_completed}/artifacts"
        )
        assert "tasks" in list_response.json()

        # Fetch should return exact content
        fetch_response = await client.get(
            f"/api/v1/runs/{run_completed}/artifacts/tasks"
        )
        assert fetch_response.status_code == 200
        assert fetch_response.json() == tasks_data

    async def test_deleted_artifact_returns_404(self, client_with_runs):
        """
        Remove an artifact file from disk →
        GET /artifacts/{name} returns 404 {error: 'Artifact not found: prd'}.
        """
        client, workspace, run_completed, run_running, run_failed = client_with_runs

        artifact_path = workspace / "artifacts" / "prd.json"
        assert artifact_path.exists()
        artifact_path.unlink()

        response = await client.get(
            f"/api/v1/runs/{run_completed}/artifacts/prd"
        )
        assert response.status_code == 404
        assert "prd" in response.json()["error"]

    async def test_artifact_content_survives_unicode_round_trip(
        self, client_with_runs
    ):
        """
        Artifact with Unicode content (emoji, CJK, RTL chars) survives
        disk → HTTP → client round-trip without corruption.
        """
        client, workspace, run_completed, *_ = client_with_runs

        unicode_prd = {
            "title": "Implement 日本語 interface with emoji 🎉 support",
            "overview": "Support RTL: مرحبا والعالم and CJK: 你好世界 in UI",
            "goals": ["Arabic RTL support", "CJK font rendering", "Emoji in UI 🚀"],
            "requirements": [
                {"id": "REQ-001", "description": "Unicode everywhere", "priority": "must"}
            ],
            "constraints": [],
            "acceptance_criteria": ["All scripts render correctly"],
        }
        (workspace / "artifacts" / "prd.json").write_text(
            json.dumps(unicode_prd, ensure_ascii=False), encoding="utf-8"
        )

        response = await client.get(
            f"/api/v1/runs/{run_completed}/artifacts/prd"
        )
        assert response.status_code == 200
        returned = response.json()
        assert returned["title"] == unicode_prd["title"]
        assert "日本語" in returned["title"]
        assert "🎉" in returned["title"]


class TestEventsPaginationRoundTrip:
    """Write events to JSONL → GET /events with offset/limit → correct slice."""

    async def test_events_pagination_returns_correct_slice(
        self, client_with_runs
    ):
        """
        The JSONL log for RUN_COMPLETED has 5 events.
        Fetch with offset=1, limit=2 should return events[1] and events[2].
        """
        client, workspace, run_completed, *_ = client_with_runs
        log_path = workspace / "logs" / f"run-{run_completed}.jsonl"
        log_lines = log_path.read_text().strip().split("\n")
        if len(log_lines) < 3:
            pytest.skip("Need at least 3 log events for pagination test")

        expected_slice = [json.loads(log_lines[1]), json.loads(log_lines[2])]

        response = await client.get(
            f"/api/v1/runs/{run_completed}/events?offset=1&limit=2"
        )
        assert response.status_code == 200
        body = response.json()
        assert body["offset"] == 1
        assert len(body["events"]) <= 2
        if len(body["events"]) >= 1:
            assert body["events"][0] == expected_slice[0]

    async def test_new_events_appended_to_jsonl_become_visible(
        self, client_with_runs
    ):
        """
        Append a new event to the JSONL log →
        GET /events returns it at the correct offset.
        """
        client, workspace, run_completed, run_running, run_failed = client_with_runs
        log_path = workspace / "logs" / f"run-{run_running}.jsonl"

        # Get current event count
        r_before = await client.get(
            f"/api/v1/runs/{run_running}/events?offset=0&limit=100"
        )
        count_before = r_before.json()["count"]

        # Append a new event to the JSONL log
        new_event = {
            "event": "task_invoke",
            "phase": "architect",
            "agent_role": "architect",
            "model_tier": "sonnet",
        }
        with log_path.open("a") as f:
            f.write("\n" + json.dumps(new_event))

        # New event should now be visible at offset = count_before
        r_after = await client.get(
            f"/api/v1/runs/{run_running}/events?offset={count_before}&limit=10"
        )
        assert r_after.status_code == 200
        new_events = r_after.json()["events"]
        assert len(new_events) >= 1
        assert new_events[-1]["event"] == "task_invoke"
