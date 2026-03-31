"""Integration tests for TASK-019 — Settings page and config round-trip.

These are *integration* tests that exercise the full create_app() stack.
They complement the unit-level tests in test_settings_routes.py by testing
the interaction between the settings page, the config API, and the
GC retention API as one end-to-end flow.

Acceptance criteria verified:
  - GET /settings returns 200 HTML with all four tab panels (REQ-015)
  - Config round-trip: GET /api/v1/config → PUT /api/v1/config → GET verifies (AC-016)
  - GC preview: POST /api/v1/artifacts/retention with dry_run=true (REQ-009)
  - GC execution: POST with dry_run=false + X-Confirm-Retention-Delete header (REQ-009)
  - Auth enforcement on /settings and /api/v1/config endpoints (AC-017)
  - Backward compat: /api/v1/config response keys unchanged (AC-021)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config_yaml(tmp_path: Path) -> Path:
    """Write a minimal valid orchestrator config YAML and return its path."""
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


def _make_dashboard_app(
    workspace_root: Path,
    project_name: str = "test-project",
    config_path: Optional[Path] = None,
    *,
    auth_token: Optional[str] = None,
) -> FastAPI:
    """Create the full dashboard app with optional auth token + config path."""
    import orchestrator.dashboard.app as _app_mod
    from orchestrator.dashboard.app import create_app

    saved = _app_mod.DASHBOARD_TOKEN
    _app_mod.DASHBOARD_TOKEN = auth_token
    try:
        return create_app(workspace_root, project_name, config_path=config_path)
    finally:
        _app_mod.DASHBOARD_TOKEN = saved


def _client(
    tmp_path: Path,
    config_path: Optional[Path] = None,
    *,
    auth_token: Optional[str] = None,
) -> TestClient:
    return TestClient(
        _make_dashboard_app(tmp_path, config_path=config_path, auth_token=auth_token),
        raise_server_exceptions=False,
    )


# ---------------------------------------------------------------------------
# GET /settings — HTML page (full-stack)
# ---------------------------------------------------------------------------


class TestSettingsPageFullStack:
    """Verify GET /settings via the complete create_app() pipeline."""

    def test_returns_200(self, tmp_path: Path) -> None:
        resp = _client(tmp_path).get("/settings")
        assert resp.status_code == 200

    def test_content_type_is_html(self, tmp_path: Path) -> None:
        resp = _client(tmp_path).get("/settings")
        assert "text/html" in resp.headers["content-type"]

    def test_page_title_in_response(self, tmp_path: Path) -> None:
        resp = _client(tmp_path).get("/settings")
        assert "Settings" in resp.text

    def test_navigation_bar_present(self, tmp_path: Path) -> None:
        """Base template nav bar renders in the full app."""
        resp = _client(tmp_path).get("/settings")
        assert "Orchestrator" in resp.text

    def test_settings_js_included(self, tmp_path: Path) -> None:
        resp = _client(tmp_path).get("/settings")
        assert "settings.js" in resp.text


# ---------------------------------------------------------------------------
# Settings page — four-tab structure (full-stack)
# ---------------------------------------------------------------------------


class TestSettingsTabsFullStack:
    """Verify all four tab panels are present via the full app."""

    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path: Path) -> None:
        cfg = _make_config_yaml(tmp_path)
        self.html = _client(tmp_path, config_path=cfg).get("/settings").text

    def test_monitoring_tab_button(self) -> None:
        assert 'data-tab="monitoring"' in self.html

    def test_artifacts_tab_button(self) -> None:
        assert 'data-tab="artifacts"' in self.html

    def test_slos_tab_button(self) -> None:
        assert 'data-tab="slos"' in self.html

    def test_advanced_tab_button(self) -> None:
        assert 'data-tab="advanced"' in self.html

    def test_monitoring_tab_panel_id(self) -> None:
        assert 'id="tab-monitoring"' in self.html

    def test_artifacts_tab_panel_id(self) -> None:
        assert 'id="tab-artifacts"' in self.html

    def test_slos_tab_panel_id(self) -> None:
        assert 'id="tab-slos"' in self.html

    def test_advanced_tab_panel_id(self) -> None:
        assert 'id="tab-advanced"' in self.html

    def test_tab_roles_present(self) -> None:
        assert 'role="tab"' in self.html
        assert 'role="tabpanel"' in self.html

    def test_gc_preview_button_present(self) -> None:
        assert 'id="gc-preview-btn"' in self.html

    def test_gc_run_button_present(self) -> None:
        assert 'id="gc-run-btn"' in self.html


# ---------------------------------------------------------------------------
# Config path display (full-stack)
# ---------------------------------------------------------------------------


class TestConfigPathDisplayFullStack:
    """Verify config path display states via full app."""

    def test_config_path_shown_when_provided(self, tmp_path: Path) -> None:
        cfg = _make_config_yaml(tmp_path)
        html = _client(tmp_path, config_path=cfg).get("/settings").text
        assert str(cfg) in html

    def test_warning_when_no_config(self, tmp_path: Path) -> None:
        html = _client(tmp_path, config_path=None).get("/settings").text
        lowered = html.lower()
        assert any(phrase in lowered for phrase in ("not configured", "read-only", "no config"))

    def test_save_button_disabled_without_config(self, tmp_path: Path) -> None:
        import re
        html = _client(tmp_path, config_path=None).get("/settings").text
        match = re.search(r'id="save-btn"[^>]*>', html)
        assert match, "save-btn not found"
        assert "disabled" in match.group(0)

    def test_save_button_enabled_with_config(self, tmp_path: Path) -> None:
        import re
        cfg = _make_config_yaml(tmp_path)
        html = _client(tmp_path, config_path=cfg).get("/settings").text
        match = re.search(r'id="save-btn"[^>]*>', html)
        assert match, "save-btn not found"
        assert "disabled" not in match.group(0)


# ---------------------------------------------------------------------------
# Config round-trip: GET → PUT → GET (integration boundary)
# ---------------------------------------------------------------------------


class TestConfigRoundTrip:
    """Config save round-trip through the full app (AC-016)."""

    def test_get_config_returns_200(self, tmp_path: Path) -> None:
        cfg = _make_config_yaml(tmp_path)
        resp = _client(tmp_path, config_path=cfg).get("/api/v1/config")
        assert resp.status_code == 200

    def test_get_config_returns_json(self, tmp_path: Path) -> None:
        cfg = _make_config_yaml(tmp_path)
        resp = _client(tmp_path, config_path=cfg).get("/api/v1/config")
        assert "application/json" in resp.headers["content-type"]

    def test_get_config_has_max_budget_usd(self, tmp_path: Path) -> None:
        cfg = _make_config_yaml(tmp_path)
        data = _client(tmp_path, config_path=cfg).get("/api/v1/config").json()
        assert "max_budget_usd" in data

    def test_put_config_returns_200(self, tmp_path: Path) -> None:
        cfg = _make_config_yaml(tmp_path)
        resp = _client(tmp_path, config_path=cfg).put(
            "/api/v1/config", json={"max_concurrent_agents": 6}
        )
        assert resp.status_code == 200

    def test_put_config_returns_updated_true(self, tmp_path: Path) -> None:
        cfg = _make_config_yaml(tmp_path)
        resp = _client(tmp_path, config_path=cfg).put(
            "/api/v1/config", json={"max_concurrent_agents": 6}
        )
        assert resp.json().get("updated") is True

    def test_put_config_persists_to_yaml(self, tmp_path: Path) -> None:
        """After PUT, the YAML file on disk must reflect the new value."""
        cfg = _make_config_yaml(tmp_path)
        _client(tmp_path, config_path=cfg).put(
            "/api/v1/config", json={"max_concurrent_agents": 8}
        )
        updated = yaml.safe_load(cfg.read_text())
        assert updated["max_concurrent_agents"] == 8

    def test_get_after_put_reflects_change(self, tmp_path: Path) -> None:
        """Full round-trip: PUT a value, then GET and verify it appears."""
        cfg = _make_config_yaml(tmp_path)
        c = _client(tmp_path, config_path=cfg)
        # PUT
        put_resp = c.put("/api/v1/config", json={"max_concurrent_agents": 12})
        assert put_resp.status_code == 200
        # GET and verify
        get_resp = c.get("/api/v1/config")
        data = get_resp.json()
        assert data.get("max_concurrent_agents") == 12, (
            f"Config GET did not reflect PUT change: max_concurrent_agents={data.get('max_concurrent_agents')}"
        )

    def test_partial_put_preserves_other_fields(self, tmp_path: Path) -> None:
        """PUT with one field must not overwrite unmentioned fields (merge, not replace)."""
        cfg = _make_config_yaml(tmp_path)
        c = _client(tmp_path, config_path=cfg)
        c.put("/api/v1/config", json={"max_concurrent_agents": 3})
        data = c.get("/api/v1/config").json()
        assert "max_budget_usd" in data
        assert data["max_budget_usd"] == pytest.approx(50.0)

    def test_backup_file_created_after_put(self, tmp_path: Path) -> None:
        """PUT must create a .bak file before overwriting the config."""
        cfg = _make_config_yaml(tmp_path)
        _client(tmp_path, config_path=cfg).put(
            "/api/v1/config", json={"max_concurrent_agents": 5}
        )
        bak_path = cfg.with_suffix(".yaml.bak")
        assert bak_path.exists(), f".bak file must exist at {bak_path}"

    def test_invalid_put_returns_400(self, tmp_path: Path) -> None:
        """PUT with invalid type value must return 400."""
        cfg = _make_config_yaml(tmp_path)
        resp = _client(tmp_path, config_path=cfg).put(
            "/api/v1/config", json={"max_budget_usd": "not-a-number"}
        )
        assert resp.status_code == 400

    def test_invalid_put_does_not_modify_file(self, tmp_path: Path) -> None:
        """Failed PUT must leave the config file unchanged."""
        cfg = _make_config_yaml(tmp_path)
        original = cfg.read_bytes()
        _client(tmp_path, config_path=cfg).put(
            "/api/v1/config", json={"max_budget_usd": "invalid"}
        )
        assert cfg.read_bytes() == original

    def test_put_without_config_path_returns_400(self, tmp_path: Path) -> None:
        """PUT /api/v1/config without a configured config_path → 400."""
        resp = _client(tmp_path, config_path=None).put(
            "/api/v1/config", json={"max_concurrent_agents": 2}
        )
        assert resp.status_code == 400

    def test_put_without_config_path_error_in_body(self, tmp_path: Path) -> None:
        resp = _client(tmp_path, config_path=None).put(
            "/api/v1/config", json={"max_concurrent_agents": 2}
        )
        assert "error" in resp.json()


# ---------------------------------------------------------------------------
# GC preview — POST /api/v1/artifacts/retention (dry_run=true)
# ---------------------------------------------------------------------------


class TestGarbageCollectionPreview:
    """Verify GC preview endpoint via full app (REQ-009)."""

    def test_retention_endpoint_exists(self, tmp_path: Path) -> None:
        """POST /api/v1/artifacts/retention must be registered."""
        app = _make_dashboard_app(tmp_path)
        paths = [r.path for r in app.routes]
        assert any("retention" in p for p in paths), (
            f"Retention route not found. Available paths: {paths}"
        )

    def test_dry_run_preview_returns_200(self, tmp_path: Path) -> None:
        """POST with dry_run=true (preview mode) must return 200."""
        resp = _client(tmp_path).post(
            "/api/v1/artifacts/retention",
            json={"dry_run": True, "max_age_days": 90, "max_runs": 100},
        )
        assert resp.status_code == 200

    def test_dry_run_response_has_candidates(self, tmp_path: Path) -> None:
        """Dry-run response must include a list of candidate runs."""
        resp = _client(tmp_path).post(
            "/api/v1/artifacts/retention",
            json={"dry_run": True, "max_age_days": 1, "max_runs": 1},
        )
        assert resp.status_code == 200
        data = resp.json()
        # Response must include deleted_paths or candidates field
        assert any(k in data for k in ("deleted_paths", "candidates", "runs_eligible")), (
            f"GC preview response missing expected field. Got: {list(data.keys())}"
        )

    def test_dry_run_does_not_delete_files(self, tmp_path: Path) -> None:
        """dry_run=True must NOT remove any files from the workspace."""
        ws = tmp_path / "workspace" / "artifacts"
        ws.mkdir(parents=True, exist_ok=True)
        sentinel = ws / "sentinel.txt"
        sentinel.write_text("keep me")

        _client(tmp_path).post(
            "/api/v1/artifacts/retention",
            json={"dry_run": True, "max_age_days": 1, "max_runs": 1},
        )
        assert sentinel.exists(), "dry_run=True must not delete files"

    def test_dry_run_response_is_json(self, tmp_path: Path) -> None:
        resp = _client(tmp_path).post(
            "/api/v1/artifacts/retention",
            json={"dry_run": True, "max_age_days": 90, "max_runs": 100},
        )
        assert "application/json" in resp.headers["content-type"]

    def test_keep_failed_default_is_true(self) -> None:
        """RetentionRequest default for keep_failed must be True."""
        from orchestrator.dashboard.routes.artifacts import RetentionRequest
        req = RetentionRequest()
        assert req.keep_failed is True

    def test_dry_run_default_is_true(self) -> None:
        """RetentionRequest default for dry_run must be True (safe by default)."""
        from orchestrator.dashboard.routes.artifacts import RetentionRequest
        req = RetentionRequest()
        assert req.dry_run is True


# ---------------------------------------------------------------------------
# GC execution — POST /api/v1/artifacts/retention (dry_run=false + confirm header)
# ---------------------------------------------------------------------------


class TestGarbageCollectionExecution:
    """Verify GC execution with confirmation header (REQ-009)."""

    def test_execution_without_confirm_header_rejected(self, tmp_path: Path) -> None:
        """dry_run=False without X-Confirm-Retention-Delete must be rejected."""
        resp = _client(tmp_path).post(
            "/api/v1/artifacts/retention",
            json={"dry_run": False, "max_age_days": 90, "max_runs": 100},
        )
        # Must require the confirmation header — 400 expected without it
        assert resp.status_code in (400, 403, 422), (
            f"Expected rejection without confirm header, got {resp.status_code}"
        )

    def test_execution_with_confirm_header_accepted(self, tmp_path: Path) -> None:
        """dry_run=False + X-Confirm-Retention-Delete: yes → 200."""
        resp = _client(tmp_path).post(
            "/api/v1/artifacts/retention",
            json={"dry_run": False, "max_age_days": 90, "max_runs": 100},
            headers={"X-Confirm-Retention-Delete": "yes"},
        )
        assert resp.status_code == 200

    def test_execution_response_has_deleted_paths(self, tmp_path: Path) -> None:
        """Execution response must include deleted_paths list."""
        resp = _client(tmp_path).post(
            "/api/v1/artifacts/retention",
            json={"dry_run": False, "max_age_days": 90, "max_runs": 100},
            headers={"X-Confirm-Retention-Delete": "yes"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "deleted_paths" in data


# ---------------------------------------------------------------------------
# Auth enforcement — settings and config endpoints
# ---------------------------------------------------------------------------


class TestSettingsAuth:
    """Verify auth on settings and config endpoints (AC-017)."""

    _TOKEN = "settings-test-token-xyz"

    def _authed_client(self, tmp_path: Path) -> TestClient:
        cfg = _make_config_yaml(tmp_path)
        return TestClient(
            _make_dashboard_app(tmp_path, config_path=cfg, auth_token=self._TOKEN),
            raise_server_exceptions=False,
        )

    def test_settings_page_401_without_token(self, tmp_path: Path) -> None:
        client = self._authed_client(tmp_path)
        assert client.get("/settings").status_code == 401

    def test_settings_page_200_with_valid_token(self, tmp_path: Path) -> None:
        client = self._authed_client(tmp_path)
        resp = client.get("/settings", headers={"Authorization": f"Bearer {self._TOKEN}"})
        assert resp.status_code == 200

    def test_config_get_401_without_token(self, tmp_path: Path) -> None:
        client = self._authed_client(tmp_path)
        assert client.get("/api/v1/config").status_code == 401

    def test_config_get_200_with_valid_token(self, tmp_path: Path) -> None:
        client = self._authed_client(tmp_path)
        resp = client.get(
            "/api/v1/config",
            headers={"Authorization": f"Bearer {self._TOKEN}"},
        )
        assert resp.status_code == 200

    def test_config_put_401_without_token(self, tmp_path: Path) -> None:
        client = self._authed_client(tmp_path)
        resp = client.put("/api/v1/config", json={"max_concurrent_agents": 2})
        assert resp.status_code == 401

    def test_config_put_200_with_valid_token(self, tmp_path: Path) -> None:
        client = self._authed_client(tmp_path)
        resp = client.put(
            "/api/v1/config",
            json={"max_concurrent_agents": 4},
            headers={"Authorization": f"Bearer {self._TOKEN}"},
        )
        assert resp.status_code == 200

    def test_retention_endpoint_401_without_token(self, tmp_path: Path) -> None:
        client = self._authed_client(tmp_path)
        resp = client.post(
            "/api/v1/artifacts/retention",
            json={"dry_run": True},
        )
        assert resp.status_code == 401

    def test_retention_endpoint_200_with_valid_token(self, tmp_path: Path) -> None:
        client = self._authed_client(tmp_path)
        resp = client.post(
            "/api/v1/artifacts/retention",
            json={"dry_run": True, "max_age_days": 90, "max_runs": 100},
            headers={"Authorization": f"Bearer {self._TOKEN}"},
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Backward compatibility — config API shape unchanged
# ---------------------------------------------------------------------------


class TestConfigApiBackwardCompat:
    """Verify GET /api/v1/config response keys are unchanged."""

    def test_config_api_response_has_workspace_dir(self, tmp_path: Path) -> None:
        cfg = _make_config_yaml(tmp_path)
        data = _client(tmp_path, config_path=cfg).get("/api/v1/config").json()
        assert "workspace_dir" in data

    def test_config_api_response_has_max_budget_usd(self, tmp_path: Path) -> None:
        cfg = _make_config_yaml(tmp_path)
        data = _client(tmp_path, config_path=cfg).get("/api/v1/config").json()
        assert "max_budget_usd" in data

    def test_config_api_response_has_default_workflow(self, tmp_path: Path) -> None:
        cfg = _make_config_yaml(tmp_path)
        data = _client(tmp_path, config_path=cfg).get("/api/v1/config").json()
        assert "default_workflow" in data

    def test_config_api_response_has_max_concurrent_agents(self, tmp_path: Path) -> None:
        cfg = _make_config_yaml(tmp_path)
        data = _client(tmp_path, config_path=cfg).get("/api/v1/config").json()
        assert "max_concurrent_agents" in data
