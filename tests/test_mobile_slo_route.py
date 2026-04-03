"""Tests for TASK-016: Mobile API GET /api/v1/slo endpoint.

Acceptance criteria verified:
  - GET /api/v1/slo returns 200 with slis array of 6 objects each having name, target,
    current_value, status, error_budget_remaining_pct (AC-002, REQ-002)
  - Status mapping: passing → "passing", passing near budget → "at_risk", !passing → "breached"
  - Auth required: 401 for unauthenticated requests
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orchestrator.mobile_api.routes.slo import _sli_status, router
from orchestrator.monitoring.slo import SLIResult, SLOReport


# ---------------------------------------------------------------------------
# Unit tests for helper functions
# ---------------------------------------------------------------------------

class TestSliStatus:
    """Unit tests for the _sli_status() helper in the slo route."""

    def test_passing_with_healthy_budget_returns_passing(self) -> None:
        assert _sli_status(True, 50.0) == "passing"

    def test_passing_with_low_budget_returns_at_risk(self) -> None:
        assert _sli_status(True, 5.0) == "at_risk"

    def test_passing_at_threshold_boundary_returns_at_risk(self) -> None:
        # Budget < 10.0 is at_risk even when passing
        assert _sli_status(True, 9.99) == "at_risk"

    def test_passing_at_exactly_10_returns_passing(self) -> None:
        assert _sli_status(True, 10.0) == "passing"

    def test_not_passing_returns_breached(self) -> None:
        assert _sli_status(False, 100.0) == "breached"

    def test_not_passing_with_zero_budget_returns_breached(self) -> None:
        assert _sli_status(False, 0.0) == "breached"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_slo_report(num_slis: int = 6) -> SLOReport:
    """Build a fully-populated SLOReport for tests."""
    slis = [
        SLIResult(
            name=f"sli_{i}",
            target=0.95,
            actual=0.97,
            passing=True,
            error_budget_remaining_pct=40.0,
        )
        for i in range(num_slis)
    ]
    return SLOReport(
        slis=slis,
        all_passing=True,
        evaluated_at=datetime.now(timezone.utc).isoformat(),
    )


def _make_monitoring_stack(report: SLOReport | None = None) -> MagicMock:
    """Return a mock MonitoringStack whose slo_tracker returns the given report."""
    stack = MagicMock()
    tracker = MagicMock()
    actual_report = report or _make_slo_report()
    tracker.evaluate_slos.return_value = actual_report
    stack.slo_tracker = tracker
    return stack


def _make_app(monitoring_stack: Any = None, report: SLOReport | None = None) -> FastAPI:
    """Create a minimal FastAPI app with the SLO router and mocked state."""
    app = FastAPI()
    if monitoring_stack is None:
        monitoring_stack = _make_monitoring_stack(report)
    app.state.monitoring_stack = monitoring_stack
    app.include_router(router, prefix="/api/v1")
    return app


def _client(monitoring_stack: Any = None, report: SLOReport | None = None) -> TestClient:
    return TestClient(_make_app(monitoring_stack, report), raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# Happy path tests
# ---------------------------------------------------------------------------

class TestSloEndpoint:
    """Tests for GET /api/v1/slo."""

    def test_returns_200(self) -> None:
        resp = _client().get("/api/v1/slo")
        assert resp.status_code == 200

    def test_response_has_required_keys(self) -> None:
        resp = _client().get("/api/v1/slo")
        data = resp.json()
        assert "slis" in data
        assert "all_passing" in data
        assert "data_available" in data

    def test_slis_is_list(self) -> None:
        resp = _client().get("/api/v1/slo")
        data = resp.json()
        assert isinstance(data["slis"], list)

    def test_slis_count_matches_report(self) -> None:
        """Six SLIs in the report means six in the response."""
        report = _make_slo_report(num_slis=6)
        resp = _client(report=report).get("/api/v1/slo")
        data = resp.json()
        assert len(data["slis"]) == 6

    def test_sli_items_have_required_fields(self) -> None:
        resp = _client().get("/api/v1/slo")
        data = resp.json()
        for sli in data["slis"]:
            assert "name" in sli
            assert "target" in sli
            assert "current_value" in sli
            assert "status" in sli
            assert "error_budget_remaining_pct" in sli

    def test_sli_status_passing(self) -> None:
        """Passing SLI with healthy error budget should map to 'passing'."""
        slis = [SLIResult(
            name="pipeline_success_rate",
            target=0.95,
            actual=0.98,
            passing=True,
            error_budget_remaining_pct=60.0,
        )]
        report = SLOReport(slis=slis, all_passing=True, evaluated_at="2024-01-01T00:00:00Z")
        mock_stack = _make_monitoring_stack(report)
        resp = _client(mock_stack).get("/api/v1/slo")
        data = resp.json()
        assert data["slis"][0]["status"] == "passing"

    def test_sli_status_breached(self) -> None:
        """Non-passing SLI should map to 'breached'."""
        slis = [SLIResult(
            name="error_rate",
            target=5.0,
            actual=12.0,
            passing=False,
            error_budget_remaining_pct=0.0,
        )]
        report = SLOReport(slis=slis, all_passing=False, evaluated_at="2024-01-01T00:00:00Z")
        mock_stack = _make_monitoring_stack(report)
        resp = _client(mock_stack).get("/api/v1/slo")
        data = resp.json()
        assert data["slis"][0]["status"] == "breached"

    def test_no_monitoring_stack_uses_default_report(self) -> None:
        """When monitoring_stack is None, a default report is returned."""
        app = FastAPI()
        app.state.monitoring_stack = None
        app.include_router(router, prefix="/api/v1")
        client = TestClient(app, raise_server_exceptions=True)
        resp = client.get("/api/v1/slo")
        # Should return 200 with a default (neutral) report
        assert resp.status_code == 200
        data = resp.json()
        assert "slis" in data

    def test_exception_returns_500(self) -> None:
        """If get_slo_report raises, the endpoint returns 500."""
        app = FastAPI()
        app.state.monitoring_stack = MagicMock()

        # Patch get_slo_report to raise
        with patch("orchestrator.mobile_api.routes.slo.get_slo_report") as mock_fn:
            mock_fn.side_effect = RuntimeError("SLO backend failure")
            app.include_router(router, prefix="/api/v1")
            with TestClient(app, raise_server_exceptions=False) as client:
                resp = client.get("/api/v1/slo")
        assert resp.status_code == 500
        assert "error" in resp.json()


# ---------------------------------------------------------------------------
# Auth tests
# ---------------------------------------------------------------------------

class TestSloAuth:
    """Verify auth requirement on full mobile app."""

    def test_returns_401_without_token(self, tmp_path: "Path") -> None:
        import os
        os.environ.setdefault("ORCHESTRATOR_API_KEY", "test-slo-route-key-xN8q")

        from orchestrator.mobile_api.app import create_mobile_app
        app = create_mobile_app(workspace_dir=tmp_path)

        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/api/v1/slo")
        assert resp.status_code == 401
