"""Tests for TASK-000 security and foundation fixes in app.py.

Covers all five acceptance criteria:
  1. Auth middleware uses hmac.compare_digest (timing-attack fix).
  2. api_run_artifact validates run_id/name and confines path to artifacts dir.
  3. api_resume_run uses manager.project_workspace (NameError fix).
  4. PUT /api/v1/config persists changes with .bak backup and validates via Pydantic.
  5. GET / redirects to /dashboard (not /runs).
  6. Shared _RUN_ID_RE in routes/validators.py (no duplication).
"""

from __future__ import annotations

import hmac
import json
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_dashboard_app(
    workspace_root: Path,
    project_name: str = "test-project",
    config_path: Optional[Path] = None,
    *,
    auth_token: Optional[str] = None,
) -> FastAPI:
    """Create the dashboard app with explicit token control.

    Patches the module-level DASHBOARD_TOKEN so create_app() captures the
    desired value into _auth_token at construction time.
    """
    import orchestrator.dashboard.app as _app_mod
    from orchestrator.dashboard.app import create_app

    saved = _app_mod.DASHBOARD_TOKEN
    _app_mod.DASHBOARD_TOKEN = auth_token
    try:
        return create_app(workspace_root, project_name, config_path=config_path)
    finally:
        _app_mod.DASHBOARD_TOKEN = saved


def _make_artifact(tmp_path: Path, run_id: str, name: str, data: dict) -> Path:
    """Write a JSON artifact to the expected workspace location."""
    from orchestrator.workspace_manager import WorkspaceManager

    manager = WorkspaceManager(tmp_path, "test-project")
    art_dir = manager.artifacts_dir(run_id)
    art_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = art_dir / name
    artifact_path.write_text(json.dumps(data))
    return artifact_path


# ---------------------------------------------------------------------------
# 1. Timing-safe auth: hmac.compare_digest
# ---------------------------------------------------------------------------


class TestAuthTimingSafe:
    """Auth middleware must use hmac.compare_digest — not string equality."""

    def test_correct_token_grants_access(self, tmp_path: Path):
        """Valid Bearer token should pass through to the handler."""
        app = _make_dashboard_app(tmp_path, auth_token="secret-token")
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get(
            "/api/v1/runs",
            headers={"Authorization": "Bearer secret-token"},
        )
        assert response.status_code != 401, (
            "Correct Bearer token must not be rejected"
        )

    def test_wrong_token_returns_401(self, tmp_path: Path):
        """Wrong Bearer token must return 401."""
        app = _make_dashboard_app(tmp_path, auth_token="secret-token")
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get(
            "/api/v1/runs",
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert response.status_code == 401

    def test_missing_auth_header_returns_401(self, tmp_path: Path):
        """Missing Authorization header must return 401 when token is set."""
        app = _make_dashboard_app(tmp_path, auth_token="secret-token")
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/v1/runs")
        assert response.status_code == 401

    def test_401_response_is_json_with_error_field(self, tmp_path: Path):
        """401 response body must be JSON with an 'error' field."""
        app = _make_dashboard_app(tmp_path, auth_token="secret-token")
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/v1/runs")
        assert response.headers["content-type"].startswith("application/json")
        assert "error" in response.json()

    def test_healthz_bypasses_auth(self, tmp_path: Path):
        """/healthz must be reachable without Authorization (auth-exempt path)."""
        app = _make_dashboard_app(tmp_path, auth_token="secret-token")
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/healthz")
        assert response.status_code == 200

    def test_hmac_compare_digest_is_called_in_middleware(self, tmp_path: Path):
        """Verify hmac.compare_digest is invoked during token comparison.

        This test patches hmac.compare_digest and asserts it was called,
        guarding against future regressions back to timing-unsafe == comparison.
        """
        app = _make_dashboard_app(tmp_path, auth_token="secret-token")
        client = TestClient(app, raise_server_exceptions=False)

        with patch(
            "orchestrator.dashboard.app.hmac.compare_digest",
            wraps=hmac.compare_digest,
        ) as mock_cd:
            client.get(
                "/api/v1/runs",
                headers={"Authorization": "Bearer secret-token"},
            )
            mock_cd.assert_called()

    def test_no_auth_middleware_when_token_unset(self, tmp_path: Path):
        """When DASHBOARD_TOKEN is None, all endpoints are publicly accessible."""
        app = _make_dashboard_app(tmp_path, auth_token=None)
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/v1/runs")
        assert response.status_code != 401


# ---------------------------------------------------------------------------
# 2. Path traversal fix in api_run_artifact
# ---------------------------------------------------------------------------


class TestArtifactPathTraversal:
    """api_run_artifact must validate inputs and confine paths."""

    def test_valid_artifact_returned(self, tmp_path: Path):
        """Correctly-named artifact with alphanumeric run_id returns 200."""
        _make_artifact(tmp_path, "abc123", "prd", {"title": "PRD"})
        app = _make_dashboard_app(tmp_path)
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/v1/runs/abc123/artifacts/prd")
        assert response.status_code == 200
        assert response.json()["title"] == "PRD"

    def test_traversal_attempt_in_name_rejected(self, tmp_path: Path):
        """../../etc/passwd style name must be blocked (400 from _validate_name)."""
        app = _make_dashboard_app(tmp_path)
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get(
            "/api/v1/runs/abc123/artifacts/..%2F..%2Fetc%2Fpasswd"
        )
        # FastAPI decodes path params, so the regex check fires first.
        assert response.status_code in {400, 403, 404, 422}, (
            f"Path traversal name must not return 200, got {response.status_code}"
        )

    def test_traversal_attempt_in_run_id_rejected(self, tmp_path: Path):
        """run_id with path separators must return 400."""
        app = _make_dashboard_app(tmp_path)
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get(
            "/api/v1/runs/../secret/artifacts/prd"
        )
        assert response.status_code in {400, 403, 404, 422}, (
            f"Traversal run_id must not succeed, got {response.status_code}"
        )

    def test_hidden_files_rejected(self, tmp_path: Path):
        """Hidden files like .env (leading dot) must be rejected by _validate_filename."""
        app = _make_dashboard_app(tmp_path)
        client = TestClient(app, raise_server_exceptions=False)
        # _validate_filename requires starting with alphanumeric, so .env is rejected
        response = client.get("/api/v1/runs/abc123/artifacts/.env")
        # Leading dot fails the regex; FastAPI may return 404 from routing first.
        assert response.status_code != 200

    def test_json_extension_artifact_accepted(self, tmp_path: Path):
        """Names like prd.json (with dot-extension) must be accepted by _validate_filename."""
        _make_artifact(tmp_path, "abc123", "prd.json", {"title": "PRD"})
        app = _make_dashboard_app(tmp_path)
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/v1/runs/abc123/artifacts/prd.json")
        assert response.status_code == 200

    def test_run_id_with_special_chars_rejected(self, tmp_path: Path):
        """run_id with characters outside the allowlist returns 400."""
        app = _make_dashboard_app(tmp_path)
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/v1/runs/run%3B%3Binjected/artifacts/prd")
        assert response.status_code in {400, 403, 404, 422}

    def test_missing_artifact_returns_404(self, tmp_path: Path):
        """Valid name + run_id but no file → 404."""
        app = _make_dashboard_app(tmp_path)
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/v1/runs/abc123/artifacts/nonexistent")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# 3. NameError fix: api_resume_run uses manager.project_workspace
# ---------------------------------------------------------------------------


class TestResumeRunNoNameError:
    """api_resume_run must NOT raise NameError — workspace_dir was undefined."""

    def test_resume_run_with_no_state_returns_404(self, tmp_path: Path):
        """Resume for unknown run_id → 404 (not 500 / NameError)."""
        app = _make_dashboard_app(tmp_path)
        client = TestClient(app, raise_server_exceptions=False)
        response = client.post("/api/v1/runs/no-such-run/resume")
        assert response.status_code == 404, (
            "Expected 404 for unknown run, not 500 (which would indicate NameError)"
        )

    def test_resume_run_with_state_file_reaches_runner(self, tmp_path: Path):
        """When a valid state file exists, the runner is invoked (not NameError)."""
        from orchestrator.workspace_manager import WorkspaceManager

        manager = WorkspaceManager(tmp_path, "test-project")
        ws = manager.project_workspace
        ws.mkdir(parents=True, exist_ok=True)

        run_id = "run-cafe1234"
        state = {
            "run_id": run_id,
            "feature_request": "test feature",
            "workflow_type": "feature_development",
        }
        (ws / f"state-{run_id}.json").write_text(json.dumps(state))

        app = _make_dashboard_app(tmp_path)

        # Patch runner.start_run so we don't actually launch an orchestration
        with patch(
            "orchestrator.dashboard.runner.RunTracker.start_run",
            side_effect=ValueError("max concurrent runs reached"),
        ):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.post(f"/api/v1/runs/{run_id}/resume")

        # 409 means the runner was reached (ValueError → 409),
        # NOT 500/NameError which would mean workspace_dir was still undefined.
        assert response.status_code == 409, (
            f"Expected 409 (runner reached) but got {response.status_code}. "
            "If 500, workspace_dir NameError is still present."
        )

    def test_resume_run_v1_and_no_v1_routes_both_work(self, tmp_path: Path):
        """Both /api/runs/{id}/resume and /api/v1/runs/{id}/resume must not NameError."""
        app = _make_dashboard_app(tmp_path)
        client = TestClient(app, raise_server_exceptions=False)

        for path in ["/api/runs/no-such/resume", "/api/v1/runs/no-such/resume"]:
            response = client.post(path)
            assert response.status_code != 500, (
                f"POST {path} returned 500 — likely NameError from undefined workspace_dir"
            )


# ---------------------------------------------------------------------------
# 4. PUT /api/v1/config persists changes and creates .bak backup
# ---------------------------------------------------------------------------


class TestConfigPersistence:
    """PUT /api/v1/config must persist, backup, and validate config."""

    def _make_config_yaml(self, tmp_path: Path) -> Path:
        """Write a minimal valid config YAML and return its path."""
        import yaml

        config = {
            "workspace_dir": "workspace",
            "max_review_cycles": 3,
            "max_budget_usd": 50.0,
            "default_workflow": "feature_development",
            "confirm": False,
            "checklist_verify": False,
            "tech_stack_confirmation": False,
            "max_concurrent_agents": 4,
            "projects_root": str(tmp_path),
        }
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(config))
        return config_path

    def test_valid_update_persists_to_yaml(self, tmp_path: Path):
        """Valid PUT body must be written to the config file."""
        import yaml

        config_path = self._make_config_yaml(tmp_path)
        app = _make_dashboard_app(tmp_path, config_path=config_path)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.put(
            "/api/v1/config",
            json={"max_concurrent_agents": 8},
        )
        assert response.status_code == 200, (
            f"Expected 200 for valid update, got {response.status_code}: {response.text}"
        )

        updated = yaml.safe_load(config_path.read_text())
        assert updated["max_concurrent_agents"] == 8, (
            "Config file must be updated on disk after successful PUT"
        )

    def test_backup_file_created_before_overwrite(self, tmp_path: Path):
        """A .bak file must be created containing the original config."""
        import yaml

        config_path = self._make_config_yaml(tmp_path)
        original_content = config_path.read_bytes()

        app = _make_dashboard_app(tmp_path, config_path=config_path)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.put(
            "/api/v1/config",
            json={"max_concurrent_agents": 6},
        )
        assert response.status_code == 200

        bak_path = config_path.with_suffix(".yaml.bak")
        assert bak_path.exists(), f"Backup file must exist at {bak_path}"
        assert bak_path.read_bytes() == original_content, (
            "Backup must contain original config content"
        )

    def test_backup_not_overwritten_on_repeated_updates(self, tmp_path: Path):
        """The .bak always reflects the state before the LATEST write."""
        import yaml

        config_path = self._make_config_yaml(tmp_path)
        original_bytes = config_path.read_bytes()

        app = _make_dashboard_app(tmp_path, config_path=config_path)
        client = TestClient(app, raise_server_exceptions=False)

        # First update
        client.put("/api/v1/config", json={"max_concurrent_agents": 5})
        bak_path = config_path.with_suffix(".yaml.bak")

        # Second update — backup should reflect state after first write
        client.put("/api/v1/config", json={"max_concurrent_agents": 7})

        second_bak = yaml.safe_load(bak_path.read_text())
        assert second_bak["max_concurrent_agents"] == 5, (
            "After second PUT the .bak should contain the intermediate config"
        )

    def test_invalid_config_returns_400_and_file_unchanged(self, tmp_path: Path):
        """Invalid config value must return 400 and leave the file untouched."""
        config_path = self._make_config_yaml(tmp_path)
        original_bytes = config_path.read_bytes()

        app = _make_dashboard_app(tmp_path, config_path=config_path)
        client = TestClient(app, raise_server_exceptions=False)

        # max_budget_usd must be positive — send a clearly invalid value
        response = client.put(
            "/api/v1/config",
            json={"max_budget_usd": "not-a-number"},
        )
        assert response.status_code == 400, (
            f"Invalid value must return 400, got {response.status_code}"
        )
        assert config_path.read_bytes() == original_bytes, (
            "Config file must be UNCHANGED after a validation failure"
        )

    def test_no_config_path_returns_400(self, tmp_path: Path):
        """PUT /config without a config_path configured must return 400."""
        app = _make_dashboard_app(tmp_path, config_path=None)
        client = TestClient(app, raise_server_exceptions=False)
        response = client.put("/api/v1/config", json={"max_concurrent_agents": 2})
        assert response.status_code == 400
        assert "error" in response.json()

    def test_put_response_includes_updated_true(self, tmp_path: Path):
        """Successful PUT must include {updated: true} in the response body."""
        config_path = self._make_config_yaml(tmp_path)
        app = _make_dashboard_app(tmp_path, config_path=config_path)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.put("/api/v1/config", json={"confirm": False})
        assert response.status_code == 200
        assert response.json().get("updated") is True

    def test_merged_with_existing_not_replaced(self, tmp_path: Path):
        """PUT must MERGE the body into existing config, not replace it entirely."""
        import yaml

        config_path = self._make_config_yaml(tmp_path)
        app = _make_dashboard_app(tmp_path, config_path=config_path)
        client = TestClient(app, raise_server_exceptions=False)

        # Only send one field — all others must be preserved
        client.put("/api/v1/config", json={"max_concurrent_agents": 3})

        updated = yaml.safe_load(config_path.read_text())
        assert "max_budget_usd" in updated, (
            "Existing config keys must be preserved after a partial PUT"
        )
        assert updated["max_budget_usd"] == 50.0


# ---------------------------------------------------------------------------
# 5. GET / redirects to /dashboard (not /runs)
# ---------------------------------------------------------------------------


class TestIndexRedirect:
    """GET / must redirect to /dashboard."""

    def test_root_redirects_to_dashboard(self, tmp_path: Path):
        """/ must issue a redirect (3xx) to /dashboard."""
        app = _make_dashboard_app(tmp_path)
        client = TestClient(app, follow_redirects=False, raise_server_exceptions=False)
        response = client.get("/")
        assert response.status_code in {301, 302, 307, 308}, (
            f"Expected redirect from /, got {response.status_code}"
        )
        location = response.headers.get("location", "")
        assert "/dashboard" in location, (
            f"/ must redirect to /dashboard, not {location!r}"
        )

    def test_root_does_not_redirect_to_runs(self, tmp_path: Path):
        """/ must NOT redirect to /runs (old behaviour — fixed by TASK-000)."""
        app = _make_dashboard_app(tmp_path)
        client = TestClient(app, follow_redirects=False, raise_server_exceptions=False)
        response = client.get("/")
        location = response.headers.get("location", "")
        assert "/runs" not in location or "/dashboard" in location, (
            "/ must redirect to /dashboard, not /runs (regression check)"
        )


# ---------------------------------------------------------------------------
# 6. Shared _RUN_ID_RE in routes/validators.py
# ---------------------------------------------------------------------------


class TestSharedValidators:
    """_RUN_ID_RE must live in routes/validators.py — not duplicated per module."""

    def test_validators_module_exports_run_id_re(self):
        """routes/validators.py must export _RUN_ID_RE."""
        from orchestrator.dashboard.routes.validators import _RUN_ID_RE
        assert _RUN_ID_RE is not None

    def test_artifacts_module_uses_shared_run_id_re(self):
        """routes/artifacts.py must not define its own _RUN_ID_RE — it imports it."""
        import orchestrator.dashboard.routes.artifacts as artifacts_mod
        import orchestrator.dashboard.routes.validators as validators_mod

        # The regex object in artifacts must be the same object as in validators
        assert artifacts_mod._RUN_ID_RE is validators_mod._RUN_ID_RE, (
            "routes/artifacts.py must import _RUN_ID_RE from routes/validators.py"
        )

    def test_observability_module_uses_shared_run_id_re(self):
        """routes/observability.py must not define its own _RUN_ID_RE — it imports it."""
        import orchestrator.dashboard.routes.observability as obs_mod
        import orchestrator.dashboard.routes.validators as validators_mod

        assert obs_mod._RUN_ID_RE is validators_mod._RUN_ID_RE, (
            "routes/observability.py must import _RUN_ID_RE from routes/validators.py"
        )

    def test_valid_run_ids_match(self):
        """_RUN_ID_RE must accept valid hex + dash + underscore run IDs."""
        from orchestrator.dashboard.routes.validators import _RUN_ID_RE

        valid = [
            "abc123",
            "run-abc123",
            "run_abc123",
            "a" * 64,          # max length
            "0",               # single char
            "ABC123-def_456",
        ]
        for rid in valid:
            assert _RUN_ID_RE.match(rid), f"_RUN_ID_RE should accept {rid!r}"

    def test_invalid_run_ids_do_not_match(self):
        """_RUN_ID_RE must reject IDs with path separators and special chars."""
        from orchestrator.dashboard.routes.validators import _RUN_ID_RE

        invalid = [
            "",                       # empty
            "../etc/passwd",          # traversal
            "a" * 65,                 # too long
            "run id with spaces",
            "run\x00null",            # null byte
        ]
        for rid in invalid:
            assert not _RUN_ID_RE.match(rid), f"_RUN_ID_RE should reject {rid!r}"

    def test_validate_run_id_raises_http_exception_for_invalid(self):
        """_validate_run_id must raise HTTPException(400) for bad run_ids."""
        from fastapi import HTTPException
        from orchestrator.dashboard.routes.validators import _validate_run_id

        with pytest.raises(HTTPException) as exc_info:
            _validate_run_id("../../../etc/passwd")
        assert exc_info.value.status_code == 400

    def test_validate_name_raises_http_exception_for_invalid(self):
        """_validate_name must raise HTTPException(400) for bad artifact names."""
        from fastapi import HTTPException
        from orchestrator.dashboard.routes.validators import _validate_name

        with pytest.raises(HTTPException) as exc_info:
            _validate_name("../evil")
        assert exc_info.value.status_code == 400

    def test_validate_name_rejects_name_exceeding_max_length(self):
        """Names longer than 128 chars must be rejected."""
        from fastapi import HTTPException
        from orchestrator.dashboard.routes.validators import _validate_name

        with pytest.raises(HTTPException) as exc_info:
            _validate_name("a" * 129)
        assert exc_info.value.status_code == 400

    def test_validate_filename_accepts_dot_extensions(self):
        """_validate_filename allows names like prd.json (dot-extension permitted)."""
        from orchestrator.dashboard.routes.validators import _validate_filename

        # Should not raise
        _validate_filename("prd.json")
        _validate_filename("architecture.json")
        _validate_filename("tasks.json")
        _validate_filename("report")

    def test_validate_filename_rejects_traversal(self):
        """_validate_filename rejects path traversal patterns."""
        from fastapi import HTTPException
        from orchestrator.dashboard.routes.validators import _validate_filename

        for bad in ["../etc/passwd", ".env", "foo/bar", "a.b.c"]:
            with pytest.raises(HTTPException) as exc_info:
                _validate_filename(bad)
            assert exc_info.value.status_code == 400

    def test_validate_filename_rejects_leading_dot(self):
        """_validate_filename rejects hidden files (leading dot)."""
        from fastapi import HTTPException
        from orchestrator.dashboard.routes.validators import _validate_filename

        with pytest.raises(HTTPException):
            _validate_filename(".htaccess")
