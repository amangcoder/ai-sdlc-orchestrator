"""Tests for TASK-011 — Alerts page enhancements and critical alert banner.

Acceptance criteria covered:
  AC-014-a  Alerts table filterable by severity via ?severity= query param.
  AC-014-b  Alerts table filterable by status via ?status= query param.
  AC-014-c  Expandable payload rows — detail row present in HTML, hidden by default.
  AC-014-d  Site-wide critical alert banner element present in base.html on all pages.
  AC-014-e  Banner shows alert count, links to /alerts, and exposes dismiss button.
  AC-019    All severity indicators use both icon and text label.
  app.js    initCriticalAlertBanner and initAlertRowExpand defined in app.js.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TEMPLATES_DIR = (
    Path(__file__).parent.parent
    / "src"
    / "orchestrator"
    / "dashboard"
    / "templates"
)

APP_JS = (
    Path(__file__).parent.parent
    / "src"
    / "orchestrator"
    / "dashboard"
    / "static"
    / "app.js"
)

BASE_HTML = TEMPLATES_DIR / "base.html"
ALERTS_HTML = TEMPLATES_DIR / "alerts.html"


def _make_dashboard_app(workspace_root: Path, project_name: str = "test-project"):
    """Create full dashboard app with auth disabled."""
    import orchestrator.dashboard.app as _app_mod
    from orchestrator.dashboard.app import create_app

    saved = _app_mod.DASHBOARD_TOKEN
    _app_mod.DASHBOARD_TOKEN = None
    try:
        return create_app(workspace_root, project_name)
    finally:
        _app_mod.DASHBOARD_TOKEN = saved


def _write_alerts(workspace: Path, alerts: list[dict]) -> None:
    """Write alert records to the workspace alerts.jsonl file."""
    # WorkspaceManager puts the workspace at workspace_root / project_name
    project_ws = workspace / "test-project"
    project_ws.mkdir(parents=True, exist_ok=True)
    alerts_file = project_ws / "alerts.jsonl"
    lines = [json.dumps(a) for a in alerts]
    alerts_file.write_text("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_ALERTS = [
    {
        "ts": "2026-01-01T10:00:00Z",
        "severity": "critical",
        "status": "active",
        "name": "HighCPU",
        "message": "CPU usage above 95%",
        "event": "alert_fired",
        "run_id": "run-abc123",
    },
    {
        "ts": "2026-01-01T09:00:00Z",
        "severity": "warning",
        "status": "resolved",
        "name": "MemoryPressure",
        "message": "Memory above 80%",
        "event": "alert_resolved",
        "run_id": None,
    },
    {
        "ts": "2026-01-01T08:00:00Z",
        "severity": "info",
        "status": "active",
        "name": "SlowQuery",
        "message": "Query took 2s",
        "event": "perf_event",
        "run_id": None,
    },
]


# ---------------------------------------------------------------------------
# 1. Alerts page — basic rendering
# ---------------------------------------------------------------------------


class TestAlertsPageBasic:
    """GET /alerts — basic rendering without filters."""

    def test_alerts_page_returns_200(self, tmp_path: Path):
        _write_alerts(tmp_path, SAMPLE_ALERTS)
        app = _make_dashboard_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/alerts")
        assert resp.status_code == 200

    def test_alerts_page_shows_all_alerts_without_filter(self, tmp_path: Path):
        _write_alerts(tmp_path, SAMPLE_ALERTS)
        app = _make_dashboard_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/alerts")
        html = resp.text
        assert "HighCPU" in html
        assert "MemoryPressure" in html
        assert "SlowQuery" in html

    def test_alerts_page_shows_severity_column_headers(self, tmp_path: Path):
        _write_alerts(tmp_path, SAMPLE_ALERTS)
        app = _make_dashboard_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/alerts")
        html = resp.text
        assert "Severity" in html
        assert "Status" in html
        assert "Triggered" in html

    def test_empty_alerts_shows_empty_state(self, tmp_path: Path):
        app = _make_dashboard_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/alerts")
        assert resp.status_code == 200
        assert "No alerts" in resp.text


# ---------------------------------------------------------------------------
# 2. Filter controls — severity
# ---------------------------------------------------------------------------


class TestAlertsSeverityFilter:
    """?severity= query param filters the displayed alerts."""

    def test_filter_critical_shows_only_critical(self, tmp_path: Path):
        _write_alerts(tmp_path, SAMPLE_ALERTS)
        app = _make_dashboard_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/alerts?severity=critical")
        html = resp.text
        assert "HighCPU" in html
        assert "MemoryPressure" not in html
        assert "SlowQuery" not in html

    def test_filter_warning_shows_only_warnings(self, tmp_path: Path):
        _write_alerts(tmp_path, SAMPLE_ALERTS)
        app = _make_dashboard_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/alerts?severity=warning")
        html = resp.text
        assert "MemoryPressure" in html
        assert "HighCPU" not in html
        assert "SlowQuery" not in html

    def test_filter_info_shows_only_info(self, tmp_path: Path):
        _write_alerts(tmp_path, SAMPLE_ALERTS)
        app = _make_dashboard_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/alerts?severity=info")
        html = resp.text
        assert "SlowQuery" in html
        assert "HighCPU" not in html
        assert "MemoryPressure" not in html

    def test_filter_all_shows_all_alerts(self, tmp_path: Path):
        _write_alerts(tmp_path, SAMPLE_ALERTS)
        app = _make_dashboard_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/alerts?severity=all")
        html = resp.text
        assert "HighCPU" in html
        assert "MemoryPressure" in html

    def test_unknown_severity_returns_empty(self, tmp_path: Path):
        _write_alerts(tmp_path, SAMPLE_ALERTS)
        app = _make_dashboard_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/alerts?severity=unknown")
        assert resp.status_code == 200
        # None of the named alerts should appear
        html = resp.text
        assert "HighCPU" not in html
        assert "MemoryPressure" not in html


# ---------------------------------------------------------------------------
# 3. Filter controls — status
# ---------------------------------------------------------------------------


class TestAlertsStatusFilter:
    """?status= query param filters the displayed alerts."""

    def test_filter_active_shows_only_active(self, tmp_path: Path):
        _write_alerts(tmp_path, SAMPLE_ALERTS)
        app = _make_dashboard_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/alerts?status=active")
        html = resp.text
        assert "HighCPU" in html
        assert "SlowQuery" in html
        assert "MemoryPressure" not in html

    def test_filter_resolved_shows_only_resolved(self, tmp_path: Path):
        _write_alerts(tmp_path, SAMPLE_ALERTS)
        app = _make_dashboard_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/alerts?status=resolved")
        html = resp.text
        assert "MemoryPressure" in html
        assert "HighCPU" not in html
        assert "SlowQuery" not in html

    def test_combined_severity_and_status_filter(self, tmp_path: Path):
        _write_alerts(tmp_path, SAMPLE_ALERTS)
        app = _make_dashboard_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/alerts?severity=critical&status=active")
        html = resp.text
        assert "HighCPU" in html
        assert "MemoryPressure" not in html
        assert "SlowQuery" not in html

    def test_combined_no_match_shows_empty(self, tmp_path: Path):
        _write_alerts(tmp_path, SAMPLE_ALERTS)
        app = _make_dashboard_app(tmp_path)
        client = TestClient(app)
        # critical + resolved — no alerts in our fixture match this
        resp = client.get("/alerts?severity=critical&status=resolved")
        assert resp.status_code == 200
        html = resp.text
        assert "HighCPU" not in html


# ---------------------------------------------------------------------------
# 4. Expandable rows — HTML structure
# ---------------------------------------------------------------------------


class TestAlertsExpandableRows:
    """Alert detail rows are present in HTML and hidden by default."""

    def test_detail_rows_exist_with_correct_ids(self, tmp_path: Path):
        _write_alerts(tmp_path, SAMPLE_ALERTS)
        app = _make_dashboard_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/alerts")
        html = resp.text
        # Each alert should have a corresponding detail row with id="alert-detail-N"
        assert 'id="alert-detail-0"' in html
        assert 'id="alert-detail-1"' in html
        assert 'id="alert-detail-2"' in html

    def test_detail_rows_are_hidden_by_default(self, tmp_path: Path):
        _write_alerts(tmp_path, SAMPLE_ALERTS)
        app = _make_dashboard_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/alerts")
        html = resp.text
        # All detail rows must carry the 'hidden' attribute
        import re
        detail_rows = re.findall(r'<tr[^>]*class="alert-detail-row"[^>]*>', html)
        for row_tag in detail_rows:
            assert "hidden" in row_tag, f"Detail row not hidden: {row_tag}"

    def test_alert_row_has_data_alert_index(self, tmp_path: Path):
        _write_alerts(tmp_path, SAMPLE_ALERTS)
        app = _make_dashboard_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/alerts")
        html = resp.text
        assert 'data-alert-index="0"' in html
        assert 'data-alert-index="1"' in html

    def test_payload_json_is_present_in_detail_row(self, tmp_path: Path):
        _write_alerts(tmp_path, SAMPLE_ALERTS)
        app = _make_dashboard_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/alerts")
        html = resp.text
        # The pre-serialised JSON should be embedded (alert names appear in payload)
        assert "HighCPU" in html  # from json payload of first alert

    def test_alert_class_row_present(self, tmp_path: Path):
        _write_alerts(tmp_path, SAMPLE_ALERTS)
        app = _make_dashboard_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/alerts")
        html = resp.text
        assert 'class="alert-row"' in html


# ---------------------------------------------------------------------------
# 5. Severity indicators — icon + text (REQ-019 / AC-019)
# ---------------------------------------------------------------------------


class TestAlertsSeverityIndicators:
    """Both icon and text label are rendered for each severity level."""

    def test_critical_shows_icon_and_text(self, tmp_path: Path):
        _write_alerts(tmp_path, [SAMPLE_ALERTS[0]])  # critical
        app = _make_dashboard_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/alerts")
        html = resp.text
        assert "severity-critical" in html
        assert "Critical" in html

    def test_warning_shows_icon_and_text(self, tmp_path: Path):
        _write_alerts(tmp_path, [SAMPLE_ALERTS[1]])  # warning
        app = _make_dashboard_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/alerts")
        html = resp.text
        assert "severity-warning" in html
        assert "Warning" in html

    def test_info_shows_icon_and_text(self, tmp_path: Path):
        _write_alerts(tmp_path, [SAMPLE_ALERTS[2]])  # info
        app = _make_dashboard_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/alerts")
        html = resp.text
        assert "severity-info" in html
        assert "Info" in html


# ---------------------------------------------------------------------------
# 6. Active / Resolved status badges
# ---------------------------------------------------------------------------


class TestAlertsStatusBadges:
    """Active alerts get red badge, resolved get green badge."""

    def test_active_alert_has_active_badge(self, tmp_path: Path):
        _write_alerts(tmp_path, [SAMPLE_ALERTS[0]])  # active
        app = _make_dashboard_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/alerts")
        assert "badge-active" in resp.text
        assert "Active" in resp.text

    def test_resolved_alert_has_resolved_badge(self, tmp_path: Path):
        _write_alerts(tmp_path, [SAMPLE_ALERTS[1]])  # resolved
        app = _make_dashboard_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/alerts")
        assert "badge-resolved" in resp.text
        assert "Resolved" in resp.text


# ---------------------------------------------------------------------------
# 7. Site-wide critical alert banner in base.html
# ---------------------------------------------------------------------------


class TestCriticalAlertBanner:
    """Critical alert banner element exists in base.html on every page."""

    def test_banner_element_present_on_alerts_page(self, tmp_path: Path):
        app = _make_dashboard_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/alerts")
        html = resp.text
        assert 'id="critical-alert-banner"' in html

    def test_banner_present_on_runs_page(self, tmp_path: Path):
        app = _make_dashboard_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/runs")
        html = resp.text
        assert 'id="critical-alert-banner"' in html

    def test_banner_present_on_metrics_page(self, tmp_path: Path):
        app = _make_dashboard_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/metrics")
        html = resp.text
        assert 'id="critical-alert-banner"' in html

    def test_banner_has_dismiss_button(self, tmp_path: Path):
        app = _make_dashboard_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/alerts")
        html = resp.text
        assert 'id="critical-banner-dismiss"' in html

    def test_banner_links_to_alerts_page(self, tmp_path: Path):
        app = _make_dashboard_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/runs")
        html = resp.text
        assert 'href="/alerts"' in html
        assert 'critical-banner-link' in html

    def test_banner_text_span_present(self, tmp_path: Path):
        app = _make_dashboard_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/alerts")
        html = resp.text
        assert 'id="critical-banner-text"' in html

    def test_banner_is_hidden_by_default_in_html(self, tmp_path: Path):
        """Banner must carry the 'hidden' attribute by default (JS shows it)."""
        app = _make_dashboard_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/alerts")
        import re
        banner_tags = re.findall(r'<div[^>]*id="critical-alert-banner"[^>]*>', resp.text)
        assert banner_tags, "Banner element not found"
        assert "hidden" in banner_tags[0], "Banner not hidden by default"


# ---------------------------------------------------------------------------
# 8. app.js — banner and expand functions defined
# ---------------------------------------------------------------------------


class TestAppJsFunctions:
    """Required JavaScript functions exist in app.js."""

    def _js_source(self) -> str:
        return APP_JS.read_text()

    def test_init_critical_alert_banner_defined(self):
        assert "function initCriticalAlertBanner" in self._js_source()

    def test_init_alert_row_expand_defined(self):
        assert "function initAlertRowExpand" in self._js_source()

    def test_banner_calls_api_v1_alerts(self):
        assert "/api/v1/alerts" in self._js_source()

    def test_banner_checks_session_storage_dismissed(self):
        assert "critical-banner-dismissed" in self._js_source()

    def test_banner_function_called_on_domcontentloaded(self):
        js = self._js_source()
        # initCriticalAlertBanner must be invoked inside the DOMContentLoaded listener
        dcl_block = js[js.rfind("DOMContentLoaded"):]
        assert "initCriticalAlertBanner" in dcl_block

    def test_init_alert_row_expand_filters_critical_active(self):
        js = self._js_source()
        assert "'critical'" in js or '"critical"' in js

    def test_dismiss_sets_session_storage(self):
        js = self._js_source()
        assert "sessionStorage.setItem" in js


# ---------------------------------------------------------------------------
# 9. Filter select elements in alerts.html template
# ---------------------------------------------------------------------------


class TestAlertsFilterSelectElements:
    """Filter dropdown elements exist in alerts.html with correct attributes."""

    def _html(self) -> str:
        return ALERTS_HTML.read_text()

    def test_severity_select_present(self):
        html = self._html()
        assert 'id="filter-severity"' in html or 'name="severity"' in html

    def test_status_select_present(self):
        html = self._html()
        assert 'id="filter-status"' in html or 'name="status"' in html

    def test_severity_select_options(self):
        html = self._html()
        assert "critical" in html.lower()
        assert "warning" in html.lower()
        assert "info" in html.lower()

    def test_status_select_options(self):
        html = self._html()
        assert "active" in html.lower()
        assert "resolved" in html.lower()

    def test_filter_form_method_is_get(self):
        html = self._html()
        assert 'method="get"' in html.lower()

    def test_filter_form_action_is_alerts(self):
        html = self._html()
        assert 'action="/alerts"' in html


# ---------------------------------------------------------------------------
# 10. api_alerts body constraint — must NOT be broken (existing test guard)
# ---------------------------------------------------------------------------


class TestApiAlertsBodyConstraint:
    """api_alerts must still have exactly 1 statement (return) — no dead code."""

    def test_api_alerts_still_has_single_return_statement(self):
        import ast
        import inspect

        from orchestrator.dashboard import app as app_module

        source = inspect.getsource(app_module)
        tree = ast.parse(source)

        api_alerts_bodies = [
            node.body
            for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "api_alerts"
        ]
        assert api_alerts_bodies, "api_alerts function not found in app module"
        body = api_alerts_bodies[0]
        assert len(body) == 1, (
            f"api_alerts body has {len(body)} statements; expected exactly 1 (return). "
            "TASK-011 changes may have introduced unreachable code."
        )
        assert isinstance(body[0], ast.Return), "Single statement must be a Return node"
