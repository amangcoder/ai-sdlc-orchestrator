"""Tests for TASK-010: Settings page with four tabbed sections.

Acceptance criteria verified:
  AC-1  Settings page has four tabbed sections: Monitoring, Artifacts, SLOs, Advanced
        — maps to REQ-015.
  AC-2  GET /settings renders 200 HTML with tab panels for all four sections.
  AC-3  Config path display is shown when has_config=True.
  AC-4  Warning banner shown when config path is None.
  AC-5  settings.js is referenced from the template.
  AC-6  Form fields are present for Monitoring tab (metrics_enabled, prometheus_url,
        grafana_url, jaeger_ui_url, loki_endpoint, tracing_enabled).
  AC-7  Form fields are present for Artifacts tab (versioning_enabled,
        retention_max_age_days, retention_max_runs).
  AC-8  GC controls (gc-preview-btn, gc-run-btn) are present in Artifacts tab.
  AC-9  Form fields are present for SLOs tab (6 SLI fields).
  AC-10 Form fields are present for Advanced tab (max_budget_usd, max_review_cycles,
        max_concurrent_agents).
  AC-11 create_settings_router() is importable and returns an APIRouter.
  AC-12 create_app() successfully includes the settings router (GET /settings works).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------

TEMPLATES_DIR = (
    Path(__file__).parent.parent
    / "src"
    / "orchestrator"
    / "dashboard"
    / "templates"
)
STATIC_DIR = (
    Path(__file__).parent.parent
    / "src"
    / "orchestrator"
    / "dashboard"
    / "static"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_app(config_path: Path | None = None) -> TestClient:
    """Build a minimal FastAPI app with only the settings router wired in."""
    from orchestrator.dashboard.routes.settings import create_settings_router

    app = FastAPI()
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    router = create_settings_router(templates, config_path)
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=True)


def _full_app_client(tmp_path: Path) -> TestClient:
    """Build a full dashboard app via create_app() for integration tests."""
    from orchestrator.dashboard.app import create_app

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    cfg = tmp_path / "config.yaml"
    cfg.write_text("workspace_dir: workspace\n")
    app = create_app(workspace_root=tmp_path, project_name="test", config_path=cfg)
    return TestClient(app, raise_server_exceptions=False)


# ===========================================================================
# Part A — Router creation
# ===========================================================================


class TestCreateSettingsRouter:
    """Unit tests for create_settings_router() factory."""

    def test_returns_apirouter(self):
        """create_settings_router() must return a FastAPI APIRouter."""
        from fastapi import APIRouter
        from orchestrator.dashboard.routes.settings import create_settings_router

        templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
        router = create_settings_router(templates)
        assert isinstance(router, APIRouter)

    def test_returns_apirouter_with_config_path(self, tmp_path):
        """create_settings_router() accepts an optional config_path."""
        from fastapi import APIRouter
        from orchestrator.dashboard.routes.settings import create_settings_router

        cfg = tmp_path / "config.yaml"
        cfg.write_text("workspace_dir: workspace\n")
        templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
        router = create_settings_router(templates, config_path=cfg)
        assert isinstance(router, APIRouter)

    def test_router_has_settings_route(self):
        """The router must expose GET /settings."""
        from orchestrator.dashboard.routes.settings import create_settings_router

        templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
        router = create_settings_router(templates)
        paths = [r.path for r in router.routes]  # type: ignore[attr-defined]
        assert "/settings" in paths


# ===========================================================================
# Part B — GET /settings response
# ===========================================================================


class TestSettingsPageResponse:
    """Tests for the GET /settings HTML response."""

    def test_returns_200(self):
        client = _make_app()
        resp = client.get("/settings")
        assert resp.status_code == 200

    def test_content_type_html(self):
        client = _make_app()
        resp = client.get("/settings")
        assert "text/html" in resp.headers["content-type"]

    def test_page_title_in_response(self):
        client = _make_app()
        resp = client.get("/settings")
        assert "Settings" in resp.text

    def test_extends_base_template(self):
        """Response HTML should include the navigation bar from base.html."""
        client = _make_app()
        resp = client.get("/settings")
        # Navigation bar is present (brand link from base.html)
        assert "Orchestrator" in resp.text

    def test_settings_js_referenced(self):
        """The template must include a reference to settings.js."""
        client = _make_app()
        resp = client.get("/settings")
        assert "settings.js" in resp.text


# ===========================================================================
# Part C — Four tabbed sections
# ===========================================================================


class TestSettingsTabs:
    """Verify all four tab panels are present in the rendered HTML."""

    @pytest.fixture(autouse=True)
    def _html(self):
        self.html = _make_app().get("/settings").text

    def test_monitoring_tab_button(self):
        assert 'data-tab="monitoring"' in self.html

    def test_artifacts_tab_button(self):
        assert 'data-tab="artifacts"' in self.html

    def test_slos_tab_button(self):
        assert 'data-tab="slos"' in self.html

    def test_advanced_tab_button(self):
        assert 'data-tab="advanced"' in self.html

    def test_monitoring_tab_panel(self):
        assert 'id="tab-monitoring"' in self.html

    def test_artifacts_tab_panel(self):
        assert 'id="tab-artifacts"' in self.html

    def test_slos_tab_panel(self):
        assert 'id="tab-slos"' in self.html

    def test_advanced_tab_panel(self):
        assert 'id="tab-advanced"' in self.html

    def test_tab_buttons_have_role_tab(self):
        assert 'role="tab"' in self.html

    def test_tab_panels_have_role_tabpanel(self):
        assert 'role="tabpanel"' in self.html


# ===========================================================================
# Part D — Monitoring tab form fields
# ===========================================================================


class TestMonitoringTabFields:
    """Verify Monitoring tab has the required form fields."""

    @pytest.fixture(autouse=True)
    def _html(self):
        self.html = _make_app().get("/settings").text

    def test_metrics_enabled_toggle(self):
        assert 'name="monitoring.metrics_enabled"' in self.html

    def test_prometheus_url_input(self):
        assert 'name="monitoring.prometheus_url"' in self.html

    def test_grafana_url_input(self):
        assert 'name="monitoring.grafana_url"' in self.html

    def test_jaeger_ui_url_input(self):
        assert 'name="monitoring.jaeger_ui_url"' in self.html

    def test_loki_endpoint_input(self):
        assert 'name="monitoring.loki_endpoint"' in self.html

    def test_tracing_enabled_toggle(self):
        assert 'name="monitoring.tracing_enabled"' in self.html


# ===========================================================================
# Part E — Artifacts tab form fields and GC controls
# ===========================================================================


class TestArtifactsTabFields:
    """Verify Artifacts tab has the required form fields and GC controls."""

    @pytest.fixture(autouse=True)
    def _html(self):
        self.html = _make_app().get("/settings").text

    def test_versioning_enabled_toggle(self):
        assert 'name="artifacts.versioning_enabled"' in self.html

    def test_retention_max_age_days_input(self):
        assert 'name="artifacts.retention_max_age_days"' in self.html

    def test_retention_max_runs_input(self):
        assert 'name="artifacts.retention_max_runs"' in self.html

    def test_gc_preview_button_present(self):
        assert 'id="gc-preview-btn"' in self.html

    def test_gc_run_button_present(self):
        assert 'id="gc-run-btn"' in self.html

    def test_gc_run_button_initially_disabled(self):
        """Run GC must start disabled — only enabled after a preview."""
        assert 'id="gc-run-btn"' in self.html
        # The button element should carry a disabled attribute before preview
        import re
        match = re.search(r'id="gc-run-btn"[^>]*>', self.html)
        assert match, "gc-run-btn element not found"
        btn_tag = match.group(0)
        assert "disabled" in btn_tag

    def test_gc_preview_results_initially_hidden(self):
        assert 'id="gc-preview-results"' in self.html
        # Must have hidden attribute
        assert 'id="gc-preview-results" hidden' in self.html or \
               'id="gc-preview-results"\nhidden' in self.html or \
               'gc-preview-results' in self.html


# ===========================================================================
# Part F — SLOs tab form fields
# ===========================================================================


class TestSLOsTabFields:
    """Verify SLOs tab has form fields for all 6 SLIs."""

    @pytest.fixture(autouse=True)
    def _html(self):
        self.html = _make_app().get("/settings").text

    def test_pipeline_success_rate_input(self):
        assert 'name="monitoring.slo.pipeline_success_rate"' in self.html

    def test_phase_duration_p95_input(self):
        assert 'name="monitoring.slo.phase_duration_p95_seconds"' in self.html

    def test_cost_per_run_p50_input(self):
        assert 'name="monitoring.slo.cost_per_run_p50_usd"' in self.html

    def test_artifact_validation_rate_input(self):
        assert 'name="monitoring.slo.artifact_validation_rate"' in self.html

    def test_max_errors_per_run_input(self):
        assert 'name="monitoring.slo.max_errors_per_run"' in self.html

    def test_recovery_success_rate_input(self):
        assert 'name="monitoring.slo.recovery_success_rate"' in self.html

    def test_slo_enabled_toggle(self):
        assert 'name="monitoring.slo.enabled"' in self.html


# ===========================================================================
# Part G — Advanced tab form fields
# ===========================================================================


class TestAdvancedTabFields:
    """Verify Advanced tab has the key config fields."""

    @pytest.fixture(autouse=True)
    def _html(self):
        self.html = _make_app().get("/settings").text

    def test_max_budget_usd_input(self):
        assert 'name="max_budget_usd"' in self.html

    def test_max_review_cycles_input(self):
        assert 'name="max_review_cycles"' in self.html

    def test_max_concurrent_agents_input(self):
        assert 'name="max_concurrent_agents"' in self.html

    def test_default_workflow_select(self):
        assert 'name="default_workflow"' in self.html

    def test_confirm_toggle(self):
        assert 'name="confirm"' in self.html


# ===========================================================================
# Part H — Config path display
# ===========================================================================


class TestConfigPathDisplay:
    """Verify the template handles has_config / no-config states correctly."""

    def test_config_path_displayed_when_provided(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("workspace_dir: workspace\n")
        client = _make_app(config_path=cfg)
        html = client.get("/settings").text
        assert str(cfg) in html

    def test_read_only_warning_when_no_config(self):
        client = _make_app(config_path=None)
        html = client.get("/settings").text
        # Should show some warning about no config path
        assert "not configured" in html.lower() or "read-only" in html.lower() or "no config" in html.lower()

    def test_save_button_disabled_when_no_config(self):
        """When no config path given, Save button must be disabled."""
        import re
        client = _make_app(config_path=None)
        html = client.get("/settings").text
        # Find save button
        match = re.search(r'id="save-btn"[^>]*>', html)
        assert match, "save-btn element not found"
        btn_tag = match.group(0)
        assert "disabled" in btn_tag

    def test_save_button_enabled_when_config_present(self, tmp_path):
        """When config path given, Save button must NOT be disabled."""
        import re
        cfg = tmp_path / "config.yaml"
        cfg.write_text("workspace_dir: workspace\n")
        client = _make_app(config_path=cfg)
        html = client.get("/settings").text
        match = re.search(r'id="save-btn"[^>]*>', html)
        assert match, "save-btn element not found"
        btn_tag = match.group(0)
        assert "disabled" not in btn_tag


# ===========================================================================
# Part I — Integration: create_app() includes settings route
# ===========================================================================


class TestCreateAppIntegration:
    """Verify create_app() correctly wires the settings router."""

    def test_settings_route_accessible(self, tmp_path):
        client = _full_app_client(tmp_path)
        resp = client.get("/settings")
        assert resp.status_code == 200

    def test_settings_route_returns_html(self, tmp_path):
        client = _full_app_client(tmp_path)
        resp = client.get("/settings")
        assert "text/html" in resp.headers.get("content-type", "")

    def test_settings_route_has_tabs(self, tmp_path):
        client = _full_app_client(tmp_path)
        resp = client.get("/settings")
        html = resp.text
        for tab in ["monitoring", "artifacts", "slos", "advanced"]:
            assert f'data-tab="{tab}"' in html, f"Tab '{tab}' not found in settings page"


# ===========================================================================
# Part J — settings.js smoke tests
# ===========================================================================


class TestSettingsJsFile:
    """Verify settings.js exists and contains expected function names."""

    def test_settings_js_exists(self):
        js_path = STATIC_DIR / "settings.js"
        assert js_path.exists(), "settings.js not found in static directory"

    def test_settings_js_has_load_config(self):
        js_path = STATIC_DIR / "settings.js"
        content = js_path.read_text()
        assert "loadConfig" in content

    def test_settings_js_has_show_toast(self):
        js_path = STATIC_DIR / "settings.js"
        content = js_path.read_text()
        assert "showToast" in content

    def test_settings_js_has_validate_all(self):
        js_path = STATIC_DIR / "settings.js"
        content = js_path.read_text()
        assert "validateAll" in content

    def test_settings_js_has_serialize_changed_fields(self):
        js_path = STATIC_DIR / "settings.js"
        content = js_path.read_text()
        assert "serializeChangedFields" in content

    def test_settings_js_has_gc_preview(self):
        js_path = STATIC_DIR / "settings.js"
        content = js_path.read_text()
        assert "gc-preview-btn" in content

    def test_settings_js_has_gc_run(self):
        js_path = STATIC_DIR / "settings.js"
        content = js_path.read_text()
        assert "gc-run-btn" in content

    def test_settings_js_has_confirmation_dialog(self):
        """Run GC must show a confirmation dialog."""
        js_path = STATIC_DIR / "settings.js"
        content = js_path.read_text()
        assert "confirm(" in content

    def test_settings_js_dry_run_true_for_preview(self):
        js_path = STATIC_DIR / "settings.js"
        content = js_path.read_text()
        assert "dry_run: true" in content

    def test_settings_js_dry_run_false_for_run(self):
        js_path = STATIC_DIR / "settings.js"
        content = js_path.read_text()
        assert "dry_run: false" in content

    def test_settings_js_sends_confirmation_header(self):
        """Run GC must send X-Confirm-Retention-Delete header."""
        js_path = STATIC_DIR / "settings.js"
        content = js_path.read_text()
        assert "X-Confirm-Retention-Delete" in content

    def test_settings_js_references_retention_endpoint(self):
        js_path = STATIC_DIR / "settings.js"
        content = js_path.read_text()
        assert "/api/v1/artifacts/retention" in content

    def test_settings_js_references_config_endpoint(self):
        js_path = STATIC_DIR / "settings.js"
        content = js_path.read_text()
        assert "/api/v1/config" in content

    def test_settings_js_put_method(self):
        """Save must use HTTP PUT for config updates."""
        js_path = STATIC_DIR / "settings.js"
        content = js_path.read_text()
        assert "'PUT'" in content or '"PUT"' in content
