"""Tests for TASK-018: prompt interaction REST endpoints.

Acceptance criteria verified
-----------------------------
1. GET /api/v1/runs/{run_id}/prompt returns {pending: true, agent, question,
   run_id} when a .prompt-{run_id}.json file exists.
2. GET /api/v1/runs/{run_id}/prompt returns {pending: false, run_id} when no
   prompt file exists.
3. POST /api/v1/runs/{run_id}/prompt writes .response-{run_id}.json and
   returns {accepted: true, run_id}.
4. POST returns 404 when no prompt is pending.
5. POST returns 400 for empty (or whitespace-only) response.
6. run_id with path-traversal characters is rejected with 400.
7. Response string is sanitised: control chars stripped, max length enforced.
8. Response file format is compatible with prompt_manager.poll_for_response().
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient

from orchestrator.dashboard.routes.prompt import (
    PromptResponseBody,
    _MAX_RESPONSE_LEN,
    _sanitize_response,
    _read_prompt_file,
    _write_response_file,
    create_prompt_router,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TEMPLATES_DIR = (
    Path(__file__).parent.parent
    / "src"
    / "orchestrator"
    / "dashboard"
    / "templates"
)

RUN_ID = "abc123def456"
PROMPT_ID = "test-prompt-id-abc"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_app(workspace: Path) -> FastAPI:
    """Minimal FastAPI app with only the prompt router."""
    app = FastAPI()
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    router = create_prompt_router(templates, workspace)
    app.include_router(router)
    return app


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Bare workspace directory (no prompt file)."""
    return tmp_path


@pytest.fixture
def workspace_with_prompt(tmp_path: Path) -> tuple[Path, dict]:
    """Workspace with an active .prompt-{run_id}.json file."""
    prompt_data = {
        "prompt_id": PROMPT_ID,
        "question": "Confirm tech stack?",
        "type": "single_choice",
        "options": ["Confirm", "Abort"],
        "agent": "pm",
        "created_at": "2024-01-15T10:00:00Z",
    }
    (tmp_path / f".prompt-{RUN_ID}.json").write_text(
        json.dumps(prompt_data), encoding="utf-8"
    )
    return tmp_path, prompt_data


@pytest.fixture
def client(workspace: Path) -> TestClient:
    return TestClient(_make_app(workspace), raise_server_exceptions=True)


@pytest.fixture
def client_with_prompt(workspace_with_prompt) -> tuple[TestClient, Path, dict]:
    ws, prompt = workspace_with_prompt
    return TestClient(_make_app(ws), raise_server_exceptions=True), ws, prompt


# ===========================================================================
# Unit tests — helpers
# ===========================================================================


class TestSanitizeResponse:
    """_sanitize_response strips dangerous chars and caps length."""

    def test_strips_null_bytes(self):
        assert _sanitize_response("hello\x00world") == "helloworld"

    def test_strips_ascii_control_chars(self):
        # All ASCII 0–31 except printable ones should be stripped.
        raw = "".join(chr(i) for i in range(32)) + "abc"
        result = _sanitize_response(raw)
        assert result == "abc"

    def test_strips_bidi_override_chars(self):
        # U+202A (LEFT-TO-RIGHT EMBEDDING) is category Cf.
        assert _sanitize_response("hello\u202aworld") == "helloworld"
        # U+202E (RIGHT-TO-LEFT OVERRIDE)
        assert _sanitize_response("he\u202ello") == "hello"

    def test_strips_zero_width_joiner(self):
        # U+200D (ZERO WIDTH JOINER) — category Cf.
        assert _sanitize_response("a\u200db") == "ab"

    def test_preserves_normal_text(self):
        text = "Confirm — proceed with the plan."
        assert _sanitize_response(text) == text

    def test_preserves_unicode_letters(self):
        text = "Bestätigen"  # German word with ä, all category Ll/Lu
        assert _sanitize_response(text) == text

    def test_enforces_max_length(self):
        long_text = "a" * (_MAX_RESPONSE_LEN + 500)
        result = _sanitize_response(long_text)
        assert len(result) == _MAX_RESPONSE_LEN

    def test_empty_string_stays_empty(self):
        assert _sanitize_response("") == ""

    def test_only_control_chars_becomes_empty(self):
        assert _sanitize_response("\x00\x01\x02") == ""


class TestReadPromptFile:
    """_read_prompt_file returns dict or None."""

    def test_returns_none_when_absent(self, tmp_path: Path):
        assert _read_prompt_file(tmp_path, "no-such-run") is None

    def test_returns_dict_when_present(self, tmp_path: Path):
        data = {"prompt_id": "x", "question": "Q?", "type": "free_text"}
        (tmp_path / f".prompt-{RUN_ID}.json").write_text(json.dumps(data))
        result = _read_prompt_file(tmp_path, RUN_ID)
        assert result == data

    def test_returns_none_on_corrupt_json(self, tmp_path: Path):
        (tmp_path / f".prompt-{RUN_ID}.json").write_text("not-json")
        assert _read_prompt_file(tmp_path, RUN_ID) is None


class TestWriteResponseFile:
    """_write_response_file creates a response file compatible with poll_for_response."""

    def test_creates_response_file(self, tmp_path: Path):
        _write_response_file(tmp_path, RUN_ID, PROMPT_ID, "Confirm")
        response_file = tmp_path / f".response-{RUN_ID}.json"
        assert response_file.exists()

    def test_response_file_contains_expected_fields(self, tmp_path: Path):
        _write_response_file(tmp_path, RUN_ID, PROMPT_ID, "Confirm")
        data = json.loads(
            (tmp_path / f".response-{RUN_ID}.json").read_text(encoding="utf-8")
        )
        assert data["prompt_id"] == PROMPT_ID
        assert data["response"] == "Confirm"

    def test_overwrites_existing_response_file(self, tmp_path: Path):
        _write_response_file(tmp_path, RUN_ID, PROMPT_ID, "first")
        _write_response_file(tmp_path, RUN_ID, PROMPT_ID, "second")
        data = json.loads(
            (tmp_path / f".response-{RUN_ID}.json").read_text(encoding="utf-8")
        )
        assert data["response"] == "second"

    def test_no_tmp_file_left_on_success(self, tmp_path: Path):
        _write_response_file(tmp_path, RUN_ID, PROMPT_ID, "ok")
        assert not (tmp_path / f".response-{RUN_ID}.json.tmp").exists()


# ===========================================================================
# GET /api/v1/runs/{run_id}/prompt
# ===========================================================================


class TestGetPromptNoPending:
    """When no prompt file exists, returns pending=false."""

    def test_returns_200(self, client: TestClient):
        resp = client.get(f"/api/v1/runs/{RUN_ID}/prompt")
        assert resp.status_code == 200

    def test_pending_is_false(self, client: TestClient):
        resp = client.get(f"/api/v1/runs/{RUN_ID}/prompt")
        assert resp.json()["pending"] is False

    def test_run_id_in_response(self, client: TestClient):
        resp = client.get(f"/api/v1/runs/{RUN_ID}/prompt")
        assert resp.json()["run_id"] == RUN_ID


class TestGetPromptWithPending:
    """When a prompt file exists, returns pending=true with agent and question."""

    def test_returns_200(self, client_with_prompt):
        client, _ws, _prompt = client_with_prompt
        resp = client.get(f"/api/v1/runs/{RUN_ID}/prompt")
        assert resp.status_code == 200

    def test_pending_is_true(self, client_with_prompt):
        client, _ws, _prompt = client_with_prompt
        resp = client.get(f"/api/v1/runs/{RUN_ID}/prompt")
        assert resp.json()["pending"] is True

    def test_returns_question(self, client_with_prompt):
        client, _ws, _prompt = client_with_prompt
        resp = client.get(f"/api/v1/runs/{RUN_ID}/prompt")
        assert resp.json()["question"] == "Confirm tech stack?"

    def test_returns_agent(self, client_with_prompt):
        client, _ws, _prompt = client_with_prompt
        resp = client.get(f"/api/v1/runs/{RUN_ID}/prompt")
        assert resp.json()["agent"] == "pm"

    def test_returns_run_id(self, client_with_prompt):
        client, _ws, _prompt = client_with_prompt
        resp = client.get(f"/api/v1/runs/{RUN_ID}/prompt")
        assert resp.json()["run_id"] == RUN_ID

    def test_missing_agent_field_returns_empty_string(self, tmp_path: Path):
        """Prompt files without 'agent' key still work (backward compat)."""
        prompt_data = {
            "prompt_id": PROMPT_ID,
            "question": "Ready?",
            "type": "free_text",
        }
        (tmp_path / f".prompt-{RUN_ID}.json").write_text(json.dumps(prompt_data))
        resp = TestClient(_make_app(tmp_path)).get(f"/api/v1/runs/{RUN_ID}/prompt")
        assert resp.status_code == 200
        assert resp.json()["agent"] == ""


class TestGetPromptValidation:
    """run_id validation rejects bad values with 400."""

    def test_path_traversal_returns_400(self, client: TestClient):
        resp = client.get("/api/v1/runs/..evilpath../prompt")
        assert resp.status_code == 400

    def test_dots_in_run_id_return_400(self, client: TestClient):
        resp = client.get("/api/v1/runs/foo.bar/prompt")
        assert resp.status_code == 400

    def test_valid_run_id_is_accepted(self, client: TestClient):
        # Should return 200 (pending=false), not 400
        resp = client.get(f"/api/v1/runs/{RUN_ID}/prompt")
        assert resp.status_code == 200

    def test_run_id_with_hyphens_is_accepted(self, client: TestClient):
        resp = client.get("/api/v1/runs/run-abc123/prompt")
        assert resp.status_code == 200

    def test_run_id_with_underscores_is_accepted(self, client: TestClient):
        resp = client.get("/api/v1/runs/run_abc123/prompt")
        assert resp.status_code == 200


# ===========================================================================
# POST /api/v1/runs/{run_id}/prompt
# ===========================================================================


class TestPostPromptSuccess:
    """Happy-path: prompt pending, valid response submitted."""

    def test_returns_200(self, client_with_prompt):
        client, _ws, _prompt = client_with_prompt
        resp = client.post(
            f"/api/v1/runs/{RUN_ID}/prompt",
            json={"response": "Confirm"},
        )
        assert resp.status_code == 200

    def test_accepted_true_in_response(self, client_with_prompt):
        client, _ws, _prompt = client_with_prompt
        resp = client.post(
            f"/api/v1/runs/{RUN_ID}/prompt",
            json={"response": "Confirm"},
        )
        assert resp.json()["accepted"] is True

    def test_run_id_in_response(self, client_with_prompt):
        client, _ws, _prompt = client_with_prompt
        resp = client.post(
            f"/api/v1/runs/{RUN_ID}/prompt",
            json={"response": "Confirm"},
        )
        assert resp.json()["run_id"] == RUN_ID

    def test_response_file_is_written(self, client_with_prompt):
        client, ws, _prompt = client_with_prompt
        client.post(
            f"/api/v1/runs/{RUN_ID}/prompt",
            json={"response": "Confirm"},
        )
        response_file = ws / f".response-{RUN_ID}.json"
        assert response_file.exists()

    def test_response_file_contains_correct_data(self, client_with_prompt):
        """Verify compatibility with prompt_manager.poll_for_response()."""
        client, ws, _prompt = client_with_prompt
        client.post(
            f"/api/v1/runs/{RUN_ID}/prompt",
            json={"response": "Abort"},
        )
        data = json.loads(
            (ws / f".response-{RUN_ID}.json").read_text(encoding="utf-8")
        )
        assert data["prompt_id"] == PROMPT_ID
        assert data["response"] == "Abort"

    def test_response_is_sanitised_before_write(self, client_with_prompt):
        """Control chars in the response should be stripped."""
        client, ws, _prompt = client_with_prompt
        client.post(
            f"/api/v1/runs/{RUN_ID}/prompt",
            json={"response": "Confirm\x00\x01payload"},
        )
        data = json.loads(
            (ws / f".response-{RUN_ID}.json").read_text(encoding="utf-8")
        )
        assert "\x00" not in data["response"]
        assert data["response"] == "Confirmpayload"

    def test_bidi_chars_stripped_from_response(self, client_with_prompt):
        client, ws, _prompt = client_with_prompt
        client.post(
            f"/api/v1/runs/{RUN_ID}/prompt",
            json={"response": "ok\u202e\u202a"},
        )
        data = json.loads(
            (ws / f".response-{RUN_ID}.json").read_text(encoding="utf-8")
        )
        assert "\u202e" not in data["response"]
        assert "\u202a" not in data["response"]


class TestPostPromptNotFound:
    """Returns 404 when no prompt file is pending."""

    def test_returns_404_when_no_prompt_file(self, client: TestClient):
        resp = client.post(
            f"/api/v1/runs/{RUN_ID}/prompt",
            json={"response": "hello"},
        )
        assert resp.status_code == 404

    def test_404_detail_mentions_run_id(self, client: TestClient):
        resp = client.post(
            f"/api/v1/runs/{RUN_ID}/prompt",
            json={"response": "hello"},
        )
        assert RUN_ID in resp.json()["detail"]

    def test_returns_404_after_prompt_file_removed(self, client_with_prompt):
        client, ws, _prompt = client_with_prompt
        # First POST succeeds
        client.post(f"/api/v1/runs/{RUN_ID}/prompt", json={"response": "ok"})
        # Remove prompt file (simulate engine consumed it)
        (ws / f".prompt-{RUN_ID}.json").unlink()
        # Second POST should 404
        resp = client.post(
            f"/api/v1/runs/{RUN_ID}/prompt",
            json={"response": "ok"},
        )
        assert resp.status_code == 404


class TestPostPromptEmptyResponse:
    """Returns 400 for empty or whitespace-only response strings."""

    def test_empty_string_returns_400(self, client_with_prompt):
        client, _ws, _prompt = client_with_prompt
        resp = client.post(
            f"/api/v1/runs/{RUN_ID}/prompt",
            json={"response": ""},
        )
        assert resp.status_code == 400

    def test_whitespace_only_returns_400(self, client_with_prompt):
        client, _ws, _prompt = client_with_prompt
        resp = client.post(
            f"/api/v1/runs/{RUN_ID}/prompt",
            json={"response": "   \t\n  "},
        )
        # After sanitising control chars + stripping, the result is empty
        # (tabs and newlines are Cc and get stripped, spaces are stripped by .strip())
        assert resp.status_code == 400

    def test_only_control_chars_returns_400(self, client_with_prompt):
        """After sanitisation, only-control-char string becomes empty → 400."""
        client, _ws, _prompt = client_with_prompt
        resp = client.post(
            f"/api/v1/runs/{RUN_ID}/prompt",
            json={"response": "\x00\x01\x02\x03"},
        )
        assert resp.status_code == 400


class TestPostPromptValidation:
    """run_id validation rejects bad values with 400."""

    def test_path_traversal_returns_400(self, client_with_prompt):
        client, _ws, _prompt = client_with_prompt
        resp = client.post(
            "/api/v1/runs/..evilpath../prompt",
            json={"response": "test"},
        )
        assert resp.status_code == 400

    def test_dots_in_run_id_return_400(self, client_with_prompt):
        client, _ws, _prompt = client_with_prompt
        resp = client.post(
            "/api/v1/runs/foo.bar/prompt",
            json={"response": "test"},
        )
        assert resp.status_code == 400

    def test_missing_response_field_returns_422(self, client_with_prompt):
        """Pydantic validation: 'response' is required."""
        client, _ws, _prompt = client_with_prompt
        resp = client.post(
            f"/api/v1/runs/{RUN_ID}/prompt",
            json={},
        )
        assert resp.status_code == 422

    def test_response_too_long_returns_422(self, client_with_prompt):
        """Pydantic max_length on PromptResponseBody enforces upper bound."""
        client, _ws, _prompt = client_with_prompt
        resp = client.post(
            f"/api/v1/runs/{RUN_ID}/prompt",
            json={"response": "a" * (_MAX_RESPONSE_LEN + 1)},
        )
        assert resp.status_code == 422


# ===========================================================================
# Router structure
# ===========================================================================


class TestRouterStructure:
    """create_prompt_router returns a router with exactly the 2 expected routes."""

    def test_router_exposes_get_endpoint(self, workspace: Path):
        from fastapi.templating import Jinja2Templates

        app = FastAPI()
        templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
        router = create_prompt_router(templates, workspace)
        app.include_router(router)
        paths = {r.path for r in app.routes if hasattr(r, "path")}
        assert "/api/v1/runs/{run_id}/prompt" in paths

    def test_router_exposes_post_endpoint(self, workspace: Path):
        from fastapi.templating import Jinja2Templates

        app = FastAPI()
        templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
        router = create_prompt_router(templates, workspace)
        app.include_router(router)
        methods_per_path: dict[str, set[str]] = {}
        for route in app.routes:
            if hasattr(route, "path") and hasattr(route, "methods"):
                methods_per_path.setdefault(route.path, set()).update(route.methods or set())
        assert "POST" in methods_per_path.get("/api/v1/runs/{run_id}/prompt", set())
        assert "GET" in methods_per_path.get("/api/v1/runs/{run_id}/prompt", set())


# ===========================================================================
# Integration: create_app wires the prompt router
# ===========================================================================


class TestCreateAppIntegration:
    """Verify the prompt router is accessible via the full dashboard app."""

    def test_get_prompt_reachable_in_full_app(self, tmp_path: Path):
        from orchestrator.dashboard.app import create_app

        workspace_root = tmp_path / "workspaces"
        workspace_root.mkdir()
        project_ws = workspace_root / "test-project"
        project_ws.mkdir()

        app = create_app(workspace_root=workspace_root, project_name="test-project")
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get(f"/api/v1/runs/{RUN_ID}/prompt")
        # Should be 200 (pending=false), not 404 or 500
        assert resp.status_code == 200
        assert resp.json()["pending"] is False

    def test_post_prompt_returns_404_no_pending(self, tmp_path: Path):
        from orchestrator.dashboard.app import create_app

        workspace_root = tmp_path / "workspaces"
        workspace_root.mkdir()
        (workspace_root / "test-project").mkdir()

        app = create_app(workspace_root=workspace_root, project_name="test-project")
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            f"/api/v1/runs/{RUN_ID}/prompt",
            json={"response": "hi"},
        )
        assert resp.status_code == 404
