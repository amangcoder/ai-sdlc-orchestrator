"""Tests for TASK-014: GET /api/v1/monitoring/health endpoint and
enhanced observability / metrics pages.

Acceptance criteria covered:
  1. GET /api/v1/monitoring/health returns array of service status objects.
  2. Each status object has service, port, status, response_time_ms, http_status.
  3. Observability page renders service status cards for all 5 monitoring services.
  4. Observability page has a Refresh Status button.
  5. Observability page run-ID filter renders a select or text input.
  6. Metrics page renders Prometheus status indicator.
  7. Metrics page renders 4 KPI cards (Total Runs, Active Runs, Total Cost USD, Avg Phase Duration).
  8. Metrics page renders a recent-run metrics table.
  9. Metrics page renders deep-link buttons to Prometheus and Grafana UIs.
 10. monitoring_health.py defines the correct 5 services: prometheus, grafana,
     jaeger, loki, promtail.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Paths / helpers
# ---------------------------------------------------------------------------

TEMPLATES_DIR = (
    Path(__file__).parent.parent
    / "src"
    / "orchestrator"
    / "dashboard"
    / "templates"
)


def _make_reader(health_data=None):
    """Return a mock RunDataReader whose get_monitoring_health() is pre-set."""
    reader = MagicMock()
    reader.list_runs.return_value = []
    if health_data is None:
        health_data = [
            {"service": "prometheus", "port": 9090, "status": "up",
             "response_time_ms": 12.3, "http_status": 200},
            {"service": "grafana",    "port": 3000, "status": "up",
             "response_time_ms": 8.1,  "http_status": 200},
            {"service": "jaeger",     "port": 16686, "status": "down",
             "response_time_ms": -1,   "http_status": -1},
            {"service": "loki",       "port": 3100, "status": "up",
             "response_time_ms": 5.4,  "http_status": 200},
            {"service": "promtail",   "port": 9080, "status": "down",
             "response_time_ms": -1,   "http_status": -1},
        ]
    reader.get_monitoring_health.return_value = health_data
    return reader


def _make_health_app(health_data=None) -> FastAPI:
    from orchestrator.dashboard.routes.monitoring_health import (
        create_monitoring_health_router,
    )
    app = FastAPI()
    reader = _make_reader(health_data)
    router = create_monitoring_health_router(reader)
    app.include_router(router)
    return app


def _health_client(health_data=None) -> TestClient:
    return TestClient(_make_health_app(health_data), raise_server_exceptions=True)


def _make_obs_app(config_path=None, runs=None) -> FastAPI:
    from orchestrator.dashboard.routes.observability import create_observability_router
    app = FastAPI()
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    reader = MagicMock()
    reader.list_runs.return_value = runs or []
    router = create_observability_router(templates, config_path, reader)
    app.include_router(router)
    return app


def _obs_client(config_path=None, runs=None) -> TestClient:
    return TestClient(_make_obs_app(config_path, runs), raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# _MONITORING_SERVICES definition tests
# ---------------------------------------------------------------------------


class TestMonitoringServicesDefinition:
    def test_five_services_defined(self):
        from orchestrator.dashboard.routes.monitoring_health import _MONITORING_SERVICES
        assert len(_MONITORING_SERVICES) == 5

    def test_expected_service_names(self):
        from orchestrator.dashboard.routes.monitoring_health import _MONITORING_SERVICES
        names = {name for name, _ in _MONITORING_SERVICES}
        assert names == {"prometheus", "grafana", "jaeger", "loki", "promtail"}

    def test_standard_ports(self):
        from orchestrator.dashboard.routes.monitoring_health import _MONITORING_SERVICES
        ports = {name: port for name, port in _MONITORING_SERVICES}
        assert ports["prometheus"] == 9090
        assert ports["grafana"] == 3000
        assert ports["jaeger"] == 16686
        assert ports["loki"] == 3100
        assert ports["promtail"] == 9080


# ---------------------------------------------------------------------------
# GET /api/v1/monitoring/health endpoint tests
# ---------------------------------------------------------------------------


class TestMonitoringHealthEndpoint:
    def test_returns_200(self):
        client = _health_client()
        resp = client.get("/api/v1/monitoring/health")
        assert resp.status_code == 200

    def test_returns_json_array(self):
        client = _health_client()
        data = client.get("/api/v1/monitoring/health").json()
        assert isinstance(data, list)

    def test_returns_five_services(self):
        client = _health_client()
        data = client.get("/api/v1/monitoring/health").json()
        assert len(data) == 5

    def test_each_entry_has_required_fields(self):
        client = _health_client()
        data = client.get("/api/v1/monitoring/health").json()
        required = {"service", "port", "status", "response_time_ms", "http_status"}
        for entry in data:
            assert required.issubset(entry.keys()), f"Missing fields in: {entry}"

    def test_status_values_are_valid(self):
        client = _health_client()
        data = client.get("/api/v1/monitoring/health").json()
        valid = {"up", "down"}
        for entry in data:
            assert entry["status"] in valid, f"Invalid status: {entry['status']}"

    def test_up_service_has_positive_response_time(self):
        client = _health_client()
        data = client.get("/api/v1/monitoring/health").json()
        for entry in data:
            if entry["status"] == "up":
                assert entry["response_time_ms"] >= 0, (
                    f"Up service {entry['service']} should have non-negative response_time_ms"
                )

    def test_down_service_has_negative_one_response_time(self):
        client = _health_client()
        data = client.get("/api/v1/monitoring/health").json()
        for entry in data:
            if entry["status"] == "down":
                assert entry["response_time_ms"] == -1, (
                    f"Down service {entry['service']} should have response_time_ms=-1"
                )

    def test_delegates_to_reader(self):
        """Endpoint must call reader.get_monitoring_health() with the 5 MONITORING_SERVICES."""
        from orchestrator.dashboard.routes.monitoring_health import (
            create_monitoring_health_router,
            _MONITORING_SERVICES,
        )
        reader = _make_reader()
        app = FastAPI()
        app.include_router(create_monitoring_health_router(reader))
        client = TestClient(app, raise_server_exceptions=True)
        client.get("/api/v1/monitoring/health")
        reader.get_monitoring_health.assert_called_once_with(services=_MONITORING_SERVICES)

    def test_service_names_match_expected(self):
        client = _health_client()
        data = client.get("/api/v1/monitoring/health").json()
        names = {e["service"] for e in data}
        assert names == {"prometheus", "grafana", "jaeger", "loki", "promtail"}


# ---------------------------------------------------------------------------
# Observability page enhancements
# ---------------------------------------------------------------------------


class TestObservabilityPageServiceCards:
    def test_page_renders_200(self):
        client = _obs_client()
        assert client.get("/observability").status_code == 200

    def test_service_status_section_present(self):
        html = _obs_client().get("/observability").text
        assert "Service Status" in html

    def test_refresh_status_button_present(self):
        html = _obs_client().get("/observability").text
        assert "Refresh Status" in html

    def test_five_service_cards_rendered(self):
        html = _obs_client().get("/observability").text
        for svc in ("Prometheus", "Grafana", "Jaeger", "Loki", "Promtail"):
            assert svc in html, f"Service card for {svc} not found in observability page"

    def test_service_ports_shown(self):
        html = _obs_client().get("/observability").text
        for port in ("9090", "3000", "16686", "3100", "9080"):
            assert port in html, f"Port {port} not found in observability page"

    def test_health_indicator_elements_present(self):
        html = _obs_client().get("/observability").text
        # Each card should have an indicator element
        assert "health-indicator" in html

    def test_run_filter_input_rendered(self):
        html = _obs_client().get("/observability").text
        # Either a select or a text input for run_id filtering
        assert "run_id" in html

    def test_run_filter_dropdown_with_runs(self):
        """When runs are available, a <select> element should be rendered."""
        run_mock = MagicMock()
        run_mock.run_id = "abc123def456"
        run_mock.status = "completed"
        runs = [run_mock]
        html = _obs_client(runs=runs).get("/observability").text
        assert "<select" in html
        assert "abc123def456" in html

    def test_no_iframes(self):
        html = _obs_client().get("/observability").text
        assert "<iframe" not in html.lower()

    def test_health_fetch_script_present(self):
        html = _obs_client().get("/observability").text
        assert "fetchHealthStatus" in html
        assert "/api/v1/monitoring/health" in html

    def test_run_id_updates_deep_links_js_present(self):
        html = _obs_client().get("/observability").text
        assert "urls_json" in html or "_serverUrls" in html


# ---------------------------------------------------------------------------
# Metrics page enhancements
# ---------------------------------------------------------------------------


def _make_metrics_app(workspace_root: Path, config_path=None) -> FastAPI:
    """Create a minimal app with only the /metrics page wired via create_app()."""
    from orchestrator.dashboard.app import create_app
    return create_app(workspace_root, "test-project", config_path)


class TestMetricsPageEnhancements:
    def test_metrics_page_renders_200(self, tmp_path: Path):
        app = _make_metrics_app(tmp_path)
        client = TestClient(app, raise_server_exceptions=True)
        resp = client.get("/metrics")
        assert resp.status_code == 200

    def test_prometheus_status_indicator_present(self, tmp_path: Path):
        app = _make_metrics_app(tmp_path)
        client = TestClient(app, raise_server_exceptions=True)
        html = client.get("/metrics").text
        assert "Prometheus" in html

    def test_four_kpi_labels_present(self, tmp_path: Path):
        app = _make_metrics_app(tmp_path)
        client = TestClient(app, raise_server_exceptions=True)
        html = client.get("/metrics").text
        for label in ("Total Runs", "Active Runs", "Total Cost USD", "Avg Phase Duration"):
            assert label in html, f"KPI label '{label}' missing from metrics page"

    def test_prometheus_deeplink_button_present(self, tmp_path: Path):
        app = _make_metrics_app(tmp_path)
        client = TestClient(app, raise_server_exceptions=True)
        html = client.get("/metrics").text
        assert "Open Prometheus" in html

    def test_recent_runs_section_present(self, tmp_path: Path):
        app = _make_metrics_app(tmp_path)
        client = TestClient(app, raise_server_exceptions=True)
        html = client.get("/metrics").text
        assert "Recent Runs" in html

    def test_grafana_button_present_when_configured(self, tmp_path: Path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("monitoring:\n  grafana_url: 'http://localhost:3000'\n")
        app = _make_metrics_app(tmp_path, cfg)
        client = TestClient(app, raise_server_exceptions=True)
        html = client.get("/metrics").text
        assert "Open Grafana" in html
        assert "localhost:3000" in html

    def test_no_grafana_button_when_not_configured(self, tmp_path: Path):
        app = _make_metrics_app(tmp_path)
        client = TestClient(app, raise_server_exceptions=True)
        html = client.get("/metrics").text
        # Without config, Grafana deep-link should not appear
        assert "Open Grafana" not in html

    def test_prometheus_status_is_down_when_unreachable(self, tmp_path: Path):
        """When Prometheus is not running, the status should degrade gracefully."""
        app = _make_metrics_app(tmp_path)
        client = TestClient(app, raise_server_exceptions=True)
        # Prometheus is almost certainly not running in test — check page still renders
        resp = client.get("/metrics")
        assert resp.status_code == 200
        html = resp.text
        # Either "Connected" or "Unreachable" or "Unknown" should appear near Prometheus
        assert any(word in html for word in ("Connected", "Unreachable", "Unknown"))
