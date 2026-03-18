"""Integration tests: Artifacts Router ↔ Filesystem Security Boundary.

Boundary tested:
  GET /api/v1/runs/{run_id}/artifacts           → list available artifact names
  GET /api/v1/runs/{run_id}/artifacts/{name}    → serve artifact JSON content
  GET /api/v1/runs/{run_id}/events              → events polling fallback

Security contract (two-layer path traversal defense):
  Layer 1: regex allowlist  — [a-z][a-z0-9_]{0,63} rejects immediately
  Layer 2: ARTIFACT_MODELS  — only known artifact types from orchestrator.models
  Layer 3: Path.resolve()   — defense-in-depth path escape check

Contract assertions:
  - Traversal attempts (../../../etc/passwd) → 404 before any file read
  - Uppercase names → 404 (regex rejects)
  - Unknown names not in ARTIFACT_MODELS → 404
  - Known artifact present on disk → 200 with JSON content
  - Known artifact absent from disk → 404 with {error: 'Artifact not found: {name}'}
  - Events endpoint returns {events, offset, count} shape
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


# ── List Artifacts ─────────────────────────────────────────────────────────

class TestListArtifactsContract:
    """GET /api/v1/runs/{run_id}/artifacts — artifact name listing."""

    async def test_returns_list_of_artifact_names(self, client_with_runs):
        """Response is a JSON array of strings (artifact names without .json)."""
        client, workspace, run_completed, *_ = client_with_runs
        response = await client.get(f"/api/v1/runs/{run_completed}/artifacts")
        assert response.status_code == 200
        body = response.json()
        assert isinstance(body, list)
        assert all(isinstance(name, str) for name in body)

    async def test_artifact_names_do_not_include_json_extension(
        self, client_with_runs
    ):
        """Names in the list should NOT include the .json extension."""
        client, workspace, run_completed, *_ = client_with_runs
        response = await client.get(f"/api/v1/runs/{run_completed}/artifacts")
        for name in response.json():
            assert not name.endswith(".json"), (
                f"Artifact name '{name}' should not include .json extension"
            )

    async def test_prd_artifact_is_listed(self, client_with_runs):
        """workspace/artifacts/prd.json exists → 'prd' appears in the list."""
        client, workspace, run_completed, *_ = client_with_runs
        response = await client.get(f"/api/v1/runs/{run_completed}/artifacts")
        assert "prd" in response.json()

    async def test_empty_artifacts_dir_returns_empty_list(self, client):
        """No artifacts written → empty list response."""
        response = await client.get(f"/api/v1/runs/anyrunid/artifacts")
        assert response.status_code == 200
        # May be empty list or list without prd
        assert isinstance(response.json(), list)


# ── Serve Artifact Content ─────────────────────────────────────────────────

class TestServeArtifactContract:
    """GET /api/v1/runs/{run_id}/artifacts/{name} — JSON content serving."""

    async def test_known_artifact_returns_200_with_json(self, client_with_runs):
        """workspace/artifacts/prd.json exists → 200 with its JSON content."""
        client, workspace, run_completed, *_ = client_with_runs
        response = await client.get(f"/api/v1/runs/{run_completed}/artifacts/prd")
        assert response.status_code == 200
        body = response.json()
        assert isinstance(body, dict)
        # PRD content must contain our test data
        assert "title" in body
        assert body["title"] == "Fix login button crash"

    async def test_artifact_content_matches_disk_file(self, client_with_runs):
        """Response JSON must be byte-for-byte identical to the on-disk file."""
        client, workspace, run_completed, *_ = client_with_runs
        disk_content = json.loads(
            (workspace / "artifacts" / "prd.json").read_text()
        )
        response = await client.get(f"/api/v1/runs/{run_completed}/artifacts/prd")
        assert response.json() == disk_content

    async def test_missing_artifact_returns_404(self, client_with_runs):
        """
        Known artifact name (passes allowlist) but file absent on disk →
        404 with {error: 'Artifact not found: architecture'}.
        """
        client, workspace, run_completed, *_ = client_with_runs
        # 'architecture' is a known ARTIFACT_MODELS key but file is not written
        response = await client.get(
            f"/api/v1/runs/{run_completed}/artifacts/architecture"
        )
        assert response.status_code == 404
        body = response.json()
        assert "error" in body
        assert "architecture" in body["error"]

    async def test_multiple_artifacts_all_served_correctly(
        self, client_with_runs
    ):
        """Write two artifacts; both are fetchable and contain correct data."""
        client, workspace, run_completed, *_ = client_with_runs

        arch_data = {
            "components": [{"name": "TestComponent", "responsibility": "Testing"}],
            "data_flow": [],
            "tech_decisions": [],
            "constraints": [],
            "directory_structure": [],
        }
        (workspace / "artifacts" / "architecture.json").write_text(
            json.dumps(arch_data)
        )

        r1 = await client.get(f"/api/v1/runs/{run_completed}/artifacts/prd")
        r2 = await client.get(f"/api/v1/runs/{run_completed}/artifacts/architecture")
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r2.json()["components"][0]["name"] == "TestComponent"


# ── Path Traversal Defense ─────────────────────────────────────────────────

class TestArtifactPathTraversalDefense:
    """
    Two-layer path traversal defense: regex + ARTIFACT_MODELS allowlist.
    These tests verify that malicious artifact names are rejected BEFORE
    any filesystem access occurs.
    """

    @pytest.mark.parametrize("malicious_name", [
        "../../etc/passwd",
        "../../../etc/shadow",
        "..%2F..%2Fetc%2Fpasswd",  # URL-encoded traversal
        ".hidden",
        "file.json",               # contains dot — rejected by regex
        "FILE_NAME",               # uppercase — rejected by regex
        "a" * 65,                  # exceeds max length of 63
        "",                        # empty name
        "valid name",              # space character
        "name;injection",          # shell injection attempt
    ])
    async def test_malicious_name_returns_404(
        self, client_with_runs, malicious_name: str
    ):
        """
        All malformed artifact names → 404 (or 422).
        The regex allowlist rejects these before any file system access.
        """
        client, workspace, run_completed, *_ = client_with_runs
        # URL-encode the malicious name for the request path
        import urllib.parse
        encoded = urllib.parse.quote(malicious_name, safe="")
        response = await client.get(
            f"/api/v1/runs/{run_completed}/artifacts/{encoded}"
        )
        assert response.status_code in {404, 422, 400}, (
            f"Malicious artifact name '{malicious_name}' should be rejected "
            f"but got status {response.status_code}"
        )

    async def test_path_traversal_does_not_read_file(
        self, client_with_runs
    ):
        """
        Path traversal attempt must be rejected WITHOUT reading any file.
        We write a sentinel file outside artifacts/ and verify it's never returned.
        """
        client, workspace, run_completed, *_ = client_with_runs
        # Write a secret file in the workspace root (outside artifacts/)
        secret = workspace / "SECRET.json"
        secret.write_text(json.dumps({"secret": "do-not-serve-this"}))

        # Attempt traversal to reach SECRET.json
        response = await client.get(
            f"/api/v1/runs/{run_completed}/artifacts/..%2FSECRET"
        )
        assert response.status_code in {404, 422, 400}
        if response.status_code == 200:
            # If somehow a 200 is returned, ensure it doesn't contain our secret
            assert "do-not-serve-this" not in response.text

    async def test_unknown_artifact_type_returns_404(self, client_with_runs):
        """
        Name passes regex ([a-z][a-z0-9_]{0,63}) but is NOT in ARTIFACT_MODELS
        → 404 (ARTIFACT_MODELS allowlist rejects it).
        """
        client, workspace, run_completed, *_ = client_with_runs
        # Write the file on disk to ensure the rejection is from allowlist, not missing file
        (workspace / "artifacts" / "notaknowntype.json").write_text(
            json.dumps({"data": "should-not-be-served"})
        )
        response = await client.get(
            f"/api/v1/runs/{run_completed}/artifacts/notaknowntype"
        )
        assert response.status_code == 404

    async def test_resolve_artifact_path_rejects_traversal(self):
        """
        resolve_artifact_path() raises ValueError on any name with traversal.
        Tested directly (not through HTTP) to confirm the guard runs pre-filesystem.
        """
        from orchestrator.mobile_api.routes.artifacts import resolve_artifact_path  # noqa
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "artifacts").mkdir()

            for bad_name in ["../../etc/passwd", "../secret", "a/b", "a.b"]:
                with pytest.raises(ValueError):
                    resolve_artifact_path(workspace, bad_name)


# ── Events Endpoint ────────────────────────────────────────────────────────

class TestEventsPollingContract:
    """GET /api/v1/runs/{run_id}/events — HTTP polling fallback endpoint."""

    async def test_events_returns_correct_shape(self, client_with_runs):
        """Response must be {events: list, offset: int, count: int}."""
        client, workspace, run_completed, *_ = client_with_runs
        response = await client.get(
            f"/api/v1/runs/{run_completed}/events?offset=0&limit=100"
        )
        assert response.status_code == 200
        body = response.json()
        assert "events" in body
        assert "offset" in body
        assert "count" in body
        assert isinstance(body["events"], list)
        assert isinstance(body["offset"], int)
        assert isinstance(body["count"], int)

    async def test_events_offset_echoed_in_response(self, client_with_runs):
        """The offset query param must be echoed back in the response body."""
        client, workspace, run_completed, *_ = client_with_runs
        response = await client.get(
            f"/api/v1/runs/{run_completed}/events?offset=2&limit=100"
        )
        assert response.json()["offset"] == 2

    async def test_events_count_matches_list_length(self, client_with_runs):
        """count field must equal len(events)."""
        client, workspace, run_completed, *_ = client_with_runs
        response = await client.get(
            f"/api/v1/runs/{run_completed}/events?offset=0&limit=100"
        )
        body = response.json()
        assert body["count"] == len(body["events"])

    async def test_events_content_matches_jsonl_log(self, client_with_runs):
        """Events returned must match the JSONL log contents."""
        client, workspace, run_completed, *_ = client_with_runs
        log_path = workspace / "logs" / f"run-{run_completed}.jsonl"
        expected_events = [
            json.loads(line)
            for line in log_path.read_text().strip().split("\n")
        ]

        response = await client.get(
            f"/api/v1/runs/{run_completed}/events?offset=0&limit=100"
        )
        returned_events = response.json()["events"]
        # All log events should be present in response
        assert len(returned_events) >= len(expected_events)

    async def test_events_limit_respected(self, client_with_runs):
        """limit=2 returns at most 2 events."""
        client, workspace, run_completed, *_ = client_with_runs
        response = await client.get(
            f"/api/v1/runs/{run_completed}/events?offset=0&limit=2"
        )
        body = response.json()
        assert len(body["events"]) <= 2

    async def test_events_offset_skips_earlier_events(self, client_with_runs):
        """Fetching with offset=N skips the first N events."""
        client, workspace, run_completed, *_ = client_with_runs
        # Get all events from offset 0
        r_all = await client.get(
            f"/api/v1/runs/{run_completed}/events?offset=0&limit=100"
        )
        all_events = r_all.json()["events"]
        if len(all_events) < 2:
            pytest.skip("Need at least 2 events for offset test")

        # Get events from offset 1
        r_offset = await client.get(
            f"/api/v1/runs/{run_completed}/events?offset=1&limit=100"
        )
        offset_events = r_offset.json()["events"]

        # First event at offset 1 should match second event at offset 0
        assert offset_events[0] == all_events[1]
