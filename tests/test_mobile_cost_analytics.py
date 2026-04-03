"""Tests for TASK-016: Mobile API GET /api/v1/cost-analytics endpoint.

Acceptance criteria verified:
  - GET /api/v1/cost-analytics returns 200 with total_spend_usd, burn_rate_usd_per_hour,
    avg_cost_per_run, cost_by_agent, cost_by_model (AC-001, REQ-001)
  - All endpoints require valid bearer token auth and return 401 for unauthenticated requests
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orchestrator.mobile_api.routes.cost_analytics import router


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_reader(raw_data: dict[str, Any] | None = None) -> MagicMock:
    """Return a mock RunDataReader with get_cost_analytics() stubbed."""
    reader = MagicMock()
    if raw_data is None:
        raw_data = {
            "burn_rate": 0.025,
            "by_agent": {
                "pm": {"cost": 0.30, "count": 2},
                "architect": {"cost": 0.45, "count": 2},
            },
            "by_model": {
                "claude-3-haiku-20240307": {"cost": 0.20, "count": 5},
                "claude-3-5-sonnet-20241022": {"cost": 0.55, "count": 3},
            },
            "cost_trend": [
                {"date": "2024-01-15", "cost": 0.75},
            ],
        }
    reader.get_cost_analytics.return_value = raw_data
    return reader


def _make_app(reader: MagicMock | None = None) -> FastAPI:
    """Create a minimal FastAPI app with the cost_analytics router and mocked state."""
    app = FastAPI()
    app.state.reader = reader or _make_reader()
    app.include_router(router, prefix="/api/v1")
    return app


def _client(reader: MagicMock | None = None) -> TestClient:
    return TestClient(_make_app(reader), raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# Happy path tests
# ---------------------------------------------------------------------------

class TestCostAnalyticsEndpoint:
    """Tests for GET /api/v1/cost-analytics."""

    def test_returns_200(self) -> None:
        resp = _client().get("/api/v1/cost-analytics")
        assert resp.status_code == 200

    def test_response_has_required_keys(self) -> None:
        resp = _client().get("/api/v1/cost-analytics")
        data = resp.json()
        required_keys = {
            "total_spend_usd",
            "burn_rate_usd_per_hour",
            "avg_cost_per_run",
            "cost_by_agent",
            "cost_by_model",
        }
        assert required_keys.issubset(data.keys()), (
            f"Missing keys: {required_keys - set(data.keys())}"
        )

    def test_total_spend_usd_is_sum_of_agent_costs(self) -> None:
        reader = _make_reader()
        resp = _client(reader).get("/api/v1/cost-analytics")
        data = resp.json()
        assert data["total_spend_usd"] == pytest.approx(0.75, abs=1e-4)

    def test_burn_rate_matches_reader_value(self) -> None:
        resp = _client().get("/api/v1/cost-analytics")
        data = resp.json()
        assert data["burn_rate_usd_per_hour"] == pytest.approx(0.025, abs=1e-6)

    def test_cost_by_agent_is_list(self) -> None:
        resp = _client().get("/api/v1/cost-analytics")
        data = resp.json()
        assert isinstance(data["cost_by_agent"], list)

    def test_cost_by_agent_items_have_required_fields(self) -> None:
        resp = _client().get("/api/v1/cost-analytics")
        data = resp.json()
        for item in data["cost_by_agent"]:
            assert "agent" in item
            assert "cost_usd" in item
            assert "run_count" in item

    def test_cost_by_model_is_list(self) -> None:
        resp = _client().get("/api/v1/cost-analytics")
        data = resp.json()
        assert isinstance(data["cost_by_model"], list)

    def test_cost_by_model_items_have_required_fields(self) -> None:
        resp = _client().get("/api/v1/cost-analytics")
        data = resp.json()
        for item in data["cost_by_model"]:
            assert "model" in item
            assert "cost_usd" in item
            assert "run_count" in item

    def test_avg_cost_per_run_is_float(self) -> None:
        resp = _client().get("/api/v1/cost-analytics")
        data = resp.json()
        assert isinstance(data["avg_cost_per_run"], float)

    def test_empty_data_returns_zeros(self) -> None:
        reader = _make_reader({
            "burn_rate": 0.0,
            "by_agent": {},
            "by_model": {},
            "cost_trend": [],
        })
        resp = _client(reader).get("/api/v1/cost-analytics")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_spend_usd"] == 0.0
        assert data["cost_by_agent"] == []
        assert data["cost_by_model"] == []

    def test_reader_exception_returns_500(self) -> None:
        reader = MagicMock()
        reader.get_cost_analytics.side_effect = RuntimeError("DB unavailable")
        resp = _client(reader).get("/api/v1/cost-analytics")
        assert resp.status_code == 500
        assert "error" in resp.json()


# ---------------------------------------------------------------------------
# Auth tests (with full mobile app)
# ---------------------------------------------------------------------------

class TestCostAnalyticsAuth:
    """Verify that the auth middleware returns 401 for unauthenticated requests."""

    def test_returns_401_without_token(self, tmp_path: "Path") -> None:
        """Full mobile app requires auth — request without token must return 401."""
        import os
        os.environ.setdefault("ORCHESTRATOR_API_KEY", "test-cost-analytics-key-xN8q")

        from orchestrator.mobile_api.app import create_mobile_app
        app = create_mobile_app(workspace_dir=tmp_path)

        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/api/v1/cost-analytics")
        assert resp.status_code == 401

    def test_returns_200_with_valid_token(self, tmp_path: "Path") -> None:
        """Full mobile app returns 200 when a valid Bearer token is provided."""
        import os
        key = "test-cost-analytics-key-xN8q"
        os.environ["ORCHESTRATOR_API_KEY"] = key

        from orchestrator.mobile_api.app import create_mobile_app
        app = create_mobile_app(workspace_dir=tmp_path)

        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get(
                "/api/v1/cost-analytics",
                headers={"Authorization": f"Bearer {key}"},
            )
        assert resp.status_code == 200
