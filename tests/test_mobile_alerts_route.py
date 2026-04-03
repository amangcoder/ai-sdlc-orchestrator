"""Tests for TASK-016: Mobile API GET /api/v1/alerts and GET /api/v1/monitoring/health.

Acceptance criteria verified:
  - GET /api/v1/alerts returns 200 with alerts list containing severity, message,
    triggered_at, status (REQ-005)
  - GET /api/v1/monitoring/health returns component health for Prometheus, Grafana,
    Jaeger, Loki (REQ-028)
  - Empty alerts list returns 200 with empty array
  - Auth required: 401 for unauthenticated requests
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orchestrator.mobile_api.routes.alerts import router


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SAMPLE_ALERTS = [
    {
        "id": "alert-001",
        "severity": "critical",
        "message": "Pipeline success rate dropped below SLO threshold",
        "triggered_at": "2024-01-15T10:30:00Z",
        "status": "active",
    },
    {
        "id": "alert-002",
        "severity": "warning",
        "message": "Cost per run exceeds P50 budget",
        "triggered_at": "2024-01-15T09:00:00Z",
        "status": "resolved",
    },
    {
        "id": "alert-003",
        "severity": "info",
        "message": "Model escalation occurred in architect phase",
        "triggered_at": "2024-01-15T08:00:00Z",
        "status": "active",
    },
]

_SAMPLE_HEALTH = [
    {"name": "prometheus", "status": "healthy", "latency_ms": 12.3, "error": None},
    {"name": "grafana", "status": "healthy", "latency_ms": 8.1, "error": None},
    {"name": "jaeger", "status": "unavailable", "latency_ms": None, "error": "Connection refused"},
    {"name": "loki", "status": "healthy", "latency_ms": 5.4, "error": None},
]


def _make_reader(
    alerts: list[dict[str, Any]] | None = None,
    health: list[dict[str, Any]] | None = None,
) -> MagicMock:
    reader = MagicMock()
    reader.get_alert_history.return_value = alerts if alerts is not None else list(_SAMPLE_ALERTS)
    reader.get_monitoring_health.return_value = health if health is not None else list(_SAMPLE_HEALTH)
    return reader


def _make_app(reader: MagicMock | None = None) -> FastAPI:
    app = FastAPI()
    app.state.reader = reader or _make_reader()
    app.include_router(router, prefix="/api/v1")
    return app


def _client(reader: MagicMock | None = None) -> TestClient:
    return TestClient(_make_app(reader), raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# Alerts endpoint tests
# ---------------------------------------------------------------------------

class TestAlertsEndpoint:
    """Tests for GET /api/v1/alerts."""

    def test_returns_200(self) -> None:
        resp = _client().get("/api/v1/alerts")
        assert resp.status_code == 200

    def test_response_has_alerts_and_total(self) -> None:
        resp = _client().get("/api/v1/alerts")
        data = resp.json()
        assert "alerts" in data
        assert "total" in data

    def test_alerts_count_matches_reader(self) -> None:
        resp = _client().get("/api/v1/alerts")
        data = resp.json()
        assert data["total"] == len(_SAMPLE_ALERTS)
        assert len(data["alerts"]) == len(_SAMPLE_ALERTS)

    def test_alert_items_have_required_fields(self) -> None:
        resp = _client().get("/api/v1/alerts")
        data = resp.json()
        for alert in data["alerts"]:
            assert "id" in alert, "Missing 'id' field"
            assert "severity" in alert, "Missing 'severity' field"
            assert "message" in alert, "Missing 'message' field"
            assert "triggered_at" in alert, "Missing 'triggered_at' field"
            assert "status" in alert, "Missing 'status' field"

    def test_severity_values_are_valid(self) -> None:
        resp = _client().get("/api/v1/alerts")
        data = resp.json()
        valid_severities = {"critical", "warning", "info"}
        for alert in data["alerts"]:
            assert alert["severity"] in valid_severities

    def test_empty_alerts_returns_200_with_empty_list(self) -> None:
        reader = _make_reader(alerts=[])
        resp = _client(reader).get("/api/v1/alerts")
        assert resp.status_code == 200
        data = resp.json()
        assert data["alerts"] == []
        assert data["total"] == 0

    def test_limit_query_parameter_accepted(self) -> None:
        resp = _client().get("/api/v1/alerts?limit=10")
        assert resp.status_code == 200

    def test_reader_exception_returns_500(self) -> None:
        reader = MagicMock()
        reader.get_alert_history.side_effect = RuntimeError("Storage backend error")
        resp = _client(reader).get("/api/v1/alerts")
        assert resp.status_code == 500
        assert "error" in resp.json()

    def test_alerts_with_missing_fields_gracefully_filled(self) -> None:
        """Alerts missing optional fields should get sensible defaults."""
        reader = _make_reader(alerts=[{"message": "test alert"}])
        resp = _client(reader).get("/api/v1/alerts")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["alerts"]) == 1
        alert = data["alerts"][0]
        assert alert["severity"] == "info"
        assert alert["status"] == "active"


# ---------------------------------------------------------------------------
# Monitoring health endpoint tests
# ---------------------------------------------------------------------------

class TestMonitoringHealthEndpoint:
    """Tests for GET /api/v1/monitoring/health."""

    def test_returns_200(self) -> None:
        resp = _client().get("/api/v1/monitoring/health")
        assert resp.status_code == 200

    def test_response_has_components_and_counts(self) -> None:
        resp = _client().get("/api/v1/monitoring/health")
        data = resp.json()
        assert "components" in data
        assert "healthy_count" in data
        assert "total_count" in data

    def test_component_count_matches_reader(self) -> None:
        resp = _client().get("/api/v1/monitoring/health")
        data = resp.json()
        assert data["total_count"] == len(_SAMPLE_HEALTH)
        assert len(data["components"]) == len(_SAMPLE_HEALTH)

    def test_component_items_have_required_fields(self) -> None:
        resp = _client().get("/api/v1/monitoring/health")
        data = resp.json()
        for comp in data["components"]:
            assert "name" in comp
            assert "status" in comp
            assert "latency_ms" in comp
            assert "error" in comp

    def test_healthy_count_is_correct(self) -> None:
        resp = _client().get("/api/v1/monitoring/health")
        data = resp.json()
        # 3 healthy out of 4 in _SAMPLE_HEALTH
        assert data["healthy_count"] == 3

    def test_reader_exception_returns_500(self) -> None:
        reader = MagicMock()
        reader.get_monitoring_health.side_effect = RuntimeError("Monitoring unavailable")
        resp = _client(reader).get("/api/v1/monitoring/health")
        assert resp.status_code == 500
        assert "error" in resp.json()


# ---------------------------------------------------------------------------
# Auth tests
# ---------------------------------------------------------------------------

class TestAlertsAuth:
    """Verify auth requirement on full mobile app."""

    def test_alerts_returns_401_without_token(self, tmp_path: "Path") -> None:
        import os
        os.environ.setdefault("ORCHESTRATOR_API_KEY", "test-alerts-key-xN8q")

        from orchestrator.mobile_api.app import create_mobile_app
        app = create_mobile_app(workspace_dir=tmp_path)

        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/api/v1/alerts")
        assert resp.status_code == 401

    def test_monitoring_health_returns_401_without_token(self, tmp_path: "Path") -> None:
        import os
        os.environ.setdefault("ORCHESTRATOR_API_KEY", "test-alerts-key-xN8q")

        from orchestrator.mobile_api.app import create_mobile_app
        app = create_mobile_app(workspace_dir=tmp_path)

        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/api/v1/monitoring/health")
        assert resp.status_code == 401
