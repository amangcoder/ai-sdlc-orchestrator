"""Integration tests for prompt/response REST endpoints (TASK-008).

Tests GET /api/v1/runs/{run_id}/pending-prompt
  and POST /api/v1/runs/{run_id}/respond.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import AsyncGenerator
from unittest.mock import MagicMock, AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from .conftest import (
    TEST_API_KEY,
    RUN_RUNNING,
)


@pytest.fixture
def workspace_with_prompt(tmp_path: Path) -> tuple[Path, str]:
    """Workspace with a .prompt-{run_id}.json file and active run."""
    workspace = tmp_path
    (workspace / "artifacts").mkdir()
    (workspace / "logs").mkdir()

    run_id = RUN_RUNNING

    # Create a state file for the run
    state = {
        "run_id": run_id,
        "status": "running",
        "workflow_type": "feature_development",
        "feature_request": "Test feature",
        "start_time": "2024-01-15T10:00:00Z",
        "end_time": None,
        "total_cost_usd": 0.0,
        "steps_completed": 1,
        "steps_total": 5,
    }
    (workspace / f"state-{run_id}.json").write_text(json.dumps(state))

    # Create JSONL log file
    events = [
        {"event": "run_start", "run_id": run_id, "workflow_type": "feature_development"},
    ]
    (workspace / "logs" / f"run-{run_id}.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events)
    )

    # Create prompt file
    prompt_data = {
        "prompt_id": "test-prompt-id-abc",
        "question": "Confirm tech stack?",
        "type": "single_choice",
        "options": ["Confirm", "Abort"],
        "created_at": "2024-01-15T10:00:00Z",
    }
    (workspace / f".prompt-{run_id}.json").write_text(json.dumps(prompt_data))

    return workspace, run_id


@pytest.fixture
def mobile_app_with_prompt(workspace_with_prompt):
    workspace, run_id = workspace_with_prompt

    config_path = workspace / "config.yaml"
    config_path.write_text(
        f'workspace_dir: "{workspace}"\n'
        'default_model: claude-haiku-4-5\n'
    )

    from orchestrator.mobile_api.app import create_mobile_app
    app = create_mobile_app(workspace_dir=workspace, config_path=config_path)

    # Mock tracker where run is active
    tracker = MagicMock()
    tracker.active_run_ids.return_value = [run_id]
    tracker.is_active.side_effect = lambda rid: rid == run_id
    tracker.start_run = AsyncMock()
    tracker.cancel_run = AsyncMock()
    tracker.get_error.return_value = None
    app.state.tracker = tracker

    return app, workspace, run_id


@pytest_asyncio.fixture
async def client_with_prompt(mobile_app_with_prompt) -> AsyncGenerator:
    app, workspace, run_id = mobile_app_with_prompt
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        headers={"Authorization": f"Bearer {TEST_API_KEY}"},
    ) as c:
        yield c, workspace, run_id


# ── GET /pending-prompt tests ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pending_prompt_returns_200(client_with_prompt):
    client, workspace, run_id = client_with_prompt
    resp = await client.get(f"/api/v1/runs/{run_id}/pending-prompt")
    assert resp.status_code == 200
    data = resp.json()
    assert data["prompt_id"] == "test-prompt-id-abc"
    assert data["question"] == "Confirm tech stack?"
    assert data["type"] == "single_choice"
    assert data["options"] == ["Confirm", "Abort"]


@pytest.mark.asyncio
async def test_pending_prompt_returns_204_when_no_prompt(client_with_prompt):
    client, workspace, run_id = client_with_prompt
    # Remove prompt file
    (workspace / f".prompt-{run_id}.json").unlink()

    resp = await client.get(f"/api/v1/runs/{run_id}/pending-prompt")
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_pending_prompt_returns_404_for_unknown_run(client_with_prompt):
    client, workspace, run_id = client_with_prompt
    resp = await client.get("/api/v1/runs/unknownrunid/pending-prompt")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_pending_prompt_returns_400_for_path_traversal(client_with_prompt):
    """run_id containing path-traversal chars (dots/slashes) is rejected with 400.

    We use '..evilpath..' rather than '../etc/passwd' because a literal '/'
    in the URL path gets normalised by the router *before* the handler runs,
    which would produce a 404 (route not matched) instead of the 400 we expect.
    A run_id like '..evilpath..' successfully routes to the handler and is then
    rejected by _validate_run_id_format (dots are not in [a-zA-Z0-9_-]).
    """
    client, workspace, run_id = client_with_prompt
    resp = await client.get("/api/v1/runs/..evilpath../pending-prompt")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_pending_prompt_truncates_question(client_with_prompt):
    """Question should be truncated to 500 chars."""
    client, workspace, run_id = client_with_prompt
    # Write a prompt with a very long question
    prompt_data = {
        "prompt_id": "long-prompt",
        "question": "A" * 1000,
        "type": "free_text",
        "options": None,
        "created_at": "2024-01-15T10:00:00Z",
    }
    (workspace / f".prompt-{run_id}.json").write_text(json.dumps(prompt_data))

    resp = await client.get(f"/api/v1/runs/{run_id}/pending-prompt")
    assert resp.status_code == 200
    assert len(resp.json()["question"]) == 500


@pytest.mark.asyncio
async def test_pending_prompt_truncates_options(client_with_prompt):
    """Each option should be truncated to 100 chars."""
    client, workspace, run_id = client_with_prompt
    prompt_data = {
        "prompt_id": "opt-prompt",
        "question": "Pick one",
        "type": "single_choice",
        "options": ["B" * 200],
        "created_at": "2024-01-15T10:00:00Z",
    }
    (workspace / f".prompt-{run_id}.json").write_text(json.dumps(prompt_data))

    resp = await client.get(f"/api/v1/runs/{run_id}/pending-prompt")
    assert resp.status_code == 200
    assert len(resp.json()["options"][0]) == 100


# ── POST /respond tests ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_respond_success(client_with_prompt):
    client, workspace, run_id = client_with_prompt
    resp = await client.post(
        f"/api/v1/runs/{run_id}/respond",
        json={"prompt_id": "test-prompt-id-abc", "response": "Confirm"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "submitted"
    assert data["prompt_id"] == "test-prompt-id-abc"

    # Verify response file was written
    response_file = workspace / f".response-{run_id}.json"
    assert response_file.exists()
    rdata = json.loads(response_file.read_text())
    assert rdata["prompt_id"] == "test-prompt-id-abc"
    assert rdata["response"] == "Confirm"


@pytest.mark.asyncio
async def test_respond_409_prompt_id_mismatch(client_with_prompt):
    client, workspace, run_id = client_with_prompt
    resp = await client.post(
        f"/api/v1/runs/{run_id}/respond",
        json={"prompt_id": "wrong-prompt-id", "response": "Confirm"},
    )
    assert resp.status_code == 409
    assert "Prompt ID mismatch" in resp.json()["error"]


@pytest.mark.asyncio
async def test_respond_410_inactive_run(client_with_prompt):
    client, workspace, run_id = client_with_prompt
    # Make tracker report run as inactive.
    # IMPORTANT: the fixture sets is_active.side_effect which takes precedence
    # over return_value.  We must clear side_effect first so that the mock
    # falls through to return_value = False.
    app = client._transport.app  # type: ignore[attr-defined]
    app.state.tracker.is_active.side_effect = None
    app.state.tracker.is_active.return_value = False

    resp = await client.post(
        f"/api/v1/runs/{run_id}/respond",
        json={"prompt_id": "test-prompt-id-abc", "response": "Confirm"},
    )
    assert resp.status_code == 410


@pytest.mark.asyncio
async def test_respond_idempotent_resubmit(client_with_prompt):
    """POST /respond with same prompt_id returns 200 idempotently."""
    client, workspace, run_id = client_with_prompt

    # First submit
    resp1 = await client.post(
        f"/api/v1/runs/{run_id}/respond",
        json={"prompt_id": "test-prompt-id-abc", "response": "Confirm"},
    )
    assert resp1.status_code == 200

    # Second submit (same prompt_id)
    resp2 = await client.post(
        f"/api/v1/runs/{run_id}/respond",
        json={"prompt_id": "test-prompt-id-abc", "response": "Confirm"},
    )
    assert resp2.status_code == 200


@pytest.mark.asyncio
async def test_respond_400_malformed_run_id(client_with_prompt):
    """POST /respond with a path-traversal run_id returns 400.

    Uses '..evilpath..' as the run_id for the same routing reason described in
    test_pending_prompt_returns_400_for_path_traversal: literal '/' in the URL
    would be normalised away by the router.  Dots are not in [a-zA-Z0-9_-] so
    _validate_run_id_format rejects the value and returns HTTP 400.
    """
    client, workspace, run_id = client_with_prompt
    resp = await client.post(
        "/api/v1/runs/..evilpath../respond",
        json={"prompt_id": "abc", "response": "test"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_respond_404_no_prompt(client_with_prompt):
    client, workspace, run_id = client_with_prompt
    # Remove prompt file
    (workspace / f".prompt-{run_id}.json").unlink()

    # Ensure tracker reports run as active (fixture side_effect already does
    # this, but we make it explicit for readability).
    app = client._transport.app  # type: ignore[attr-defined]
    app.state.tracker.is_active.side_effect = None
    app.state.tracker.is_active.return_value = True

    resp = await client.post(
        f"/api/v1/runs/{run_id}/respond",
        json={"prompt_id": "abc", "response": "test"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_respond_strips_bidi_chars(client_with_prompt):
    """Unicode bidi override characters should be stripped from prompt content."""
    client, workspace, run_id = client_with_prompt

    # Write a prompt with bidi chars
    prompt_data = {
        "prompt_id": "bidi-prompt",
        "question": "Hello\u202aWorld\u202e!",
        "type": "single_choice",
        "options": ["Option\u2066A\u2069"],
        "created_at": "2024-01-15T10:00:00Z",
    }
    (workspace / f".prompt-{run_id}.json").write_text(json.dumps(prompt_data))

    resp = await client.get(f"/api/v1/runs/{run_id}/pending-prompt")
    assert resp.status_code == 200
    data = resp.json()
    # Bidi chars should be stripped
    assert "\u202a" not in data["question"]
    assert "\u202e" not in data["question"]
    assert "\u2066" not in data["options"][0]
    assert "\u2069" not in data["options"][0]
