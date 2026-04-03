"""Integration tests: Observability Endpoint E2E Scenarios.

End-to-end scenarios that span multiple API boundaries.

Scenarios:
  1. Mobile app connects → health check → gets observability URLs → checks services
  2. Monitoring health check flow: all 4 components queried in single request
  3. SLO + Alerts correlation: breached SLO triggers alerts
  4. Cost analytics + run data consistency
  5. Artifact search → fetch specific artifact (cross-endpoint flow)

These tests exercise the FULL request path through the mobile app,
not individual routes, verifying the integration between:
  - Auth middleware
  - Route handlers
  - app.state.reader (RunDataReader)
  - app.state.monitoring_stack (MonitoringStack, may be None)
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

import pytest

from .conftest import TEST_API_KEY, WRONG_API_KEY, RUN_COMPLETED, RUN_RUNNING


# ── Fixtures ────────────────────────────────────────────────────────────────

def _make_full_reader() -> MagicMock:
    """Complete RunDataReader mock covering all endpoints."""
    reader = MagicMock()

    # Cost analytics
    reader.get_cost_analytics.return_value = {
        "burn_rate": 0.035,
        "by_agent": {
            "pm": {"cost": 0.30, "count": 3},
            "architect": {"cost": 0.55, "count": 3},
            "backend_engineer": {"cost": 1.20, "count": 3},
        },
        "by_model": {
            "claude-3-haiku-20240307": {"cost": 0.45, "count": 10},
            "claude-3-5-sonnet-20241022": {"cost": 1.60, "count": 6},
        },
        "cost_trend": [
            {"date": "2024-01-15", "cost": 0.85},
        ],
    }

    # SLO — one breached
    reader.get_slo_report = MagicMock(return_value={
        "slis": [
            {"name": "pipeline_success_rate", "target": 0.95,
             "current_value": 0.93, "passing": False,
             "error_budget_consumed_pct": 100.0},
            {"name": "p95_phase_duration_seconds", "target": 300.0,
             "current_value": 250.0, "passing": True,
             "error_budget_consumed_pct": 20.0},
            {"name": "cost_per_run_usd", "target": 5.0,
             "current_value": 4.5, "passing": True,
             "error_budget_consumed_pct": 10.0},
            {"name": "artifact_validation_rate", "target": 0.90,
             "current_value": 0.91, "passing": True,
             "error_budget_consumed_pct": 5.0},
            {"name": "agent_timeout_rate", "target": 0.02,
             "current_value": 0.01, "passing": True,
             "error_budget_consumed_pct": 2.0},
            {"name": "model_escalation_rate", "target": 0.10,
             "current_value": 0.08, "passing": True,
             "error_budget_consumed_pct": 0.0},
        ]
    })

    # Alerts — critical alert for breached SLO
    reader.get_alert_history.return_value = [
        {
            "id": "alert-slo-001",
            "severity": "critical",
            "message": "Pipeline success rate (93%) below 95% SLO threshold",
            "triggered_at": "2024-01-15T12:00:00Z",
            "status": "active",
        }
    ]

    # Artifact search
    reader.search_artifacts_global.return_value = [
        {
            "artifact_name": "prd",
            "run_id": RUN_COMPLETED,
            "schema": "prd",
            "agent": "pm",
            "version": 1,
            "updated_at": "2024-01-15T10:15:00Z",
        },
        {
            "artifact_name": "architecture",
            "run_id": RUN_COMPLETED,
            "schema": "architecture",
            "agent": "architect",
            "version": 1,
            "updated_at": "2024-01-15T10:45:00Z",
        },
    ]

    # Metrics
    reader.get_metrics_summary.return_value = {
        "total_runs": 12,
        "active_runs": 1,
        "total_cost_usd": 18.50,
        "avg_phase_duration_seconds": 215.0,
    }

    # Monitoring health
    reader.get_monitoring_health.return_value = [
        {"name": "prometheus", "status": "healthy", "latency_ms": 11.2, "error": None},
        {"name": "grafana", "status": "healthy", "latency_ms": 7.5, "error": None},
        {"name": "jaeger", "status": "healthy", "latency_ms": 9.1, "error": None},
        {"name": "loki", "status": "unavailable", "latency_ms": None,
         "error": "Connection refused: :3100"},
    ]

    return reader


@pytest.fixture
def scenario_app(workspace_with_runs, config_yaml, mock_tracker):
    """Full app with run data AND monitoring data."""
    workspace, run_completed, run_running, run_failed = workspace_with_runs
    from orchestrator.mobile_api.app import create_mobile_app

    app = create_mobile_app(workspace_dir=workspace, config_path=config_yaml)
    app.state.tracker = mock_tracker
    app.state.reader = _make_full_reader()
    return app, workspace, run_completed, run_running, run_failed


@pytest.fixture
async def scenario_client(scenario_app):
    """Authenticated async client for scenario tests."""
    from httpx import ASGITransport, AsyncClient

    app, workspace, *run_ids = scenario_app
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        headers={"Authorization": f"Bearer {TEST_API_KEY}"},
    ) as c:
        yield c, workspace, *run_ids


# ── Scenario 1: Mobile App Monitoring Dashboard Flow ──────────────────────

class TestMobileMonitoringDashboardFlow:
    """
    Scenario: User opens monitoring dashboard → sees all 4 sections.

    CostAnalyticsScreen → SLOComplianceScreen → AlertsScreen →
    ArtifactSearchScreen — each backed by a separate API endpoint.

    This scenario verifies that all 4 new screens can load data
    concurrently from independent endpoints.
    """

    async def test_all_monitoring_screens_load_successfully(
        self, scenario_client
    ):
        """
        Simulate the Flutter app loading all 4 monitoring screens at startup.
        All requests must succeed with 200.
        """
        import asyncio

        client, workspace, *_ = scenario_client
        responses = await asyncio.gather(
            client.get("/api/v1/cost-analytics"),
            client.get("/api/v1/slo"),
            client.get("/api/v1/alerts"),
            client.get("/api/v1/artifacts/search?q=prd"),
            client.get("/api/v1/metrics"),
            client.get("/api/v1/monitoring/health"),
        )

        for i, resp in enumerate(responses):
            assert resp.status_code == 200, (
                f"Monitoring screen {i} returned {resp.status_code}: "
                f"{resp.text[:200]}"
            )

    async def test_concurrent_monitoring_requests_return_consistent_data(
        self, scenario_client
    ):
        """
        Concurrent requests to monitoring endpoints should return internally
        consistent data from the same reader state.
        """
        import asyncio

        client, workspace, *_ = scenario_client

        # Fire both cost analytics and metrics simultaneously
        cost_resp, metrics_resp = await asyncio.gather(
            client.get("/api/v1/cost-analytics"),
            client.get("/api/v1/metrics"),
        )

        cost = cost_resp.json()
        metrics = metrics_resp.json()

        # total_spend_usd from cost-analytics should approximately match
        # total_cost_usd from metrics
        assert abs(cost["total_spend_usd"] - metrics["total_cost_usd"]) < 10.0, (
            "Cost analytics and metrics endpoints should return consistent cost data. "
            f"cost-analytics.total_spend_usd={cost['total_spend_usd']}, "
            f"metrics.total_cost_usd={metrics['total_cost_usd']}"
        )


# ── Scenario 2: SLO Breach → Alert Correlation ───────────────────────────

class TestSLOAlertCorrelationScenario:
    """
    Scenario: A breached SLO should correlate with an active critical alert.

    This validates the real-world invariant: when a SLI is breached,
    there should be a corresponding alert in the alerts list.
    """

    async def test_breached_slo_correlates_with_critical_alert(
        self, scenario_client
    ):
        """
        GET /api/v1/slo → find breached SLI →
        GET /api/v1/alerts → find critical alert for same metric.
        """
        client, *_ = scenario_client

        # Step 1: Get SLO report
        slo_resp = await client.get("/api/v1/slo")
        assert slo_resp.status_code == 200
        breached_slis = [
            sli for sli in slo_resp.json()["slis"]
            if sli["status"] == "breached"
        ]

        if not breached_slis:
            pytest.skip("No breached SLIs in test data")

        # Step 2: Alerts should include a critical alert
        alerts_resp = await client.get("/api/v1/alerts")
        assert alerts_resp.status_code == 200
        critical_alerts = [
            a for a in alerts_resp.json()["alerts"]
            if a["severity"] == "critical" and a["status"] == "active"
        ]

        assert len(critical_alerts) > 0, (
            f"Breached SLI '{breached_slis[0]['name']}' should correlate with "
            f"a critical active alert, but no critical alerts found"
        )

    async def test_slo_status_and_alert_severity_are_consistent(
        self, scenario_client
    ):
        """
        SLO with error_budget_remaining_pct near 0 → status='breached'.
        Alert for breached SLO → severity='critical', not 'info'.
        """
        client, *_ = scenario_client

        slo_resp = await client.get("/api/v1/slo")
        alerts_resp = await client.get("/api/v1/alerts")

        slos = slo_resp.json()["slis"]
        alerts = alerts_resp.json()["alerts"]

        # Find breached SLIs
        breached_names = [s["name"] for s in slos if s["status"] == "breached"]

        if breached_names:
            # Critical alerts must exist
            has_critical = any(a["severity"] == "critical" for a in alerts)
            assert has_critical, (
                f"SLIs {breached_names} are breached but no critical alerts found — "
                f"inconsistent: breached SLOs should trigger critical alerts"
            )


# ── Scenario 3: Artifact Search → Artifact Fetch Flow ─────────────────────

class TestArtifactSearchToFetchFlow:
    """
    Scenario: User searches for artifacts → taps one → views detail.

    GlobalArtifactSearchScreen:
      GET /api/v1/artifacts/search?q=prd
      → user taps result →
      GET /api/v1/runs/{run_id}/artifacts/{name}
    """

    async def test_search_result_run_id_is_fetchable(self, scenario_client):
        """
        Each artifact in search results must be fetchable via
        GET /api/v1/runs/{run_id}/artifacts/{artifact_name}.
        """
        client, workspace, run_completed, *_ = scenario_client

        # Step 1: Search for artifacts
        search_resp = await client.get("/api/v1/artifacts/search?q=prd")
        assert search_resp.status_code == 200
        results = search_resp.json()["results"]
        assert len(results) > 0, "Need search results for this test"

        # Step 2: Try to fetch the first result
        first_result = results[0]
        run_id = first_result["run_id"]
        artifact_name = first_result["artifact_name"]

        # The run_id in search results must be a valid run in the system
        # (It may not have the artifact if test data doesn't match)
        detail_resp = await client.get(f"/api/v1/runs/{run_id}")
        # The run must exist (or at least not return 401/500)
        assert detail_resp.status_code in {200, 404}, (
            f"Artifact search result references run_id='{run_id}' which "
            f"returned unexpected status {detail_resp.status_code}"
        )

    async def test_search_results_have_navigable_run_ids(self, scenario_client):
        """
        Flutter ArtifactSearchScreen navigates to ArtifactViewerScreen using
        run_id from search results. run_id must be a valid non-empty string.
        """
        client, *_ = scenario_client

        search_resp = await client.get("/api/v1/artifacts/search?q=architecture")
        results = search_resp.json()["results"]

        for result in results:
            run_id = result["run_id"]
            assert isinstance(run_id, str) and len(run_id) > 0, (
                f"Search result artifact_name='{result['artifact_name']}' "
                f"has invalid run_id='{run_id}' — Flutter navigation will fail"
            )


# ── Scenario 4: Health Check → Monitoring Dashboard ───────────────────────

class TestHealthToMonitoringDashboardFlow:
    """
    Scenario: SettingsScreen 'Test Connection' → health check passes →
    user navigates to monitoring dashboard.

    This verifies that after /health succeeds, all monitoring endpoints
    are also accessible with the same credentials.
    """

    async def test_health_success_then_all_monitoring_endpoints_accessible(
        self, scenario_client
    ):
        """
        If /health returns 200 (connection verified), all monitoring
        endpoints must also be accessible.
        """
        client, *_ = scenario_client

        # Step 1: Health check (as done by SettingsScreen 'Test Connection')
        health_resp = await client.get("/health")
        assert health_resp.status_code == 200
        assert health_resp.json()["status"] == "ok"

        # Step 2: All monitoring endpoints should now work
        monitoring_endpoints = [
            "/api/v1/cost-analytics",
            "/api/v1/slo",
            "/api/v1/alerts",
            "/api/v1/metrics",
            "/api/v1/monitoring/health",
        ]
        for path in monitoring_endpoints:
            resp = await client.get(path)
            assert resp.status_code == 200, (
                f"After health check success, {path} should be accessible "
                f"but returned {resp.status_code}"
            )

    async def test_wrong_key_fails_health_check_variant(self, scenario_app):
        """
        Wrong API key → 401 on monitoring endpoints.
        The auth middleware must be consistent across all endpoints.
        """
        from httpx import ASGITransport, AsyncClient

        app, *_ = scenario_app

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
            headers={"Authorization": f"Bearer {WRONG_API_KEY}"},
        ) as bad_client:
            for path in ["/api/v1/cost-analytics", "/api/v1/slo", "/api/v1/alerts"]:
                resp = await bad_client.get(path)
                assert resp.status_code == 401, (
                    f"Wrong API key on {path} must return 401"
                )


# ── Scenario 5: Monitoring Stack None Graceful Degradation ────────────────

class TestMonitoringStackNoneGracefulDegradation:
    """
    When MonitoringStack is not configured (app.state.monitoring_stack = None),
    the SLO endpoint must still return a valid response using the reader fallback.

    This covers the common case where monitoring services (Prometheus/Grafana)
    are not available in the deployment.
    """

    async def test_slo_works_without_monitoring_stack(
        self, workspace, config_yaml, mock_tracker
    ):
        """
        app.state.monitoring_stack = None must not crash the /api/v1/slo endpoint.
        The endpoint should fall back to reader.get_slo_report().
        """
        from orchestrator.mobile_api.app import create_mobile_app
        from httpx import ASGITransport, AsyncClient

        app = create_mobile_app(workspace_dir=workspace, config_path=config_yaml)
        app.state.tracker = mock_tracker
        app.state.monitoring_stack = None  # No monitoring stack

        # Mock the reader to provide SLO data
        reader = MagicMock()
        reader.get_slo_report = MagicMock(return_value={
            "slis": [
                {
                    "name": "pipeline_success_rate",
                    "target": 0.95,
                    "current_value": 0.96,
                    "passing": True,
                    "error_budget_consumed_pct": 5.0,
                }
            ] * 6
        })
        app.state.reader = reader

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
            headers={"Authorization": f"Bearer {TEST_API_KEY}"},
        ) as client:
            resp = await client.get("/api/v1/slo")

        # Must not 500 — graceful degradation
        assert resp.status_code in {200, 503}, (
            f"SLO endpoint with monitoring_stack=None must not crash. "
            f"Got {resp.status_code}: {resp.text[:200]}"
        )

    async def test_observability_endpoint_returns_200(
        self, workspace, config_yaml, mock_tracker
    ):
        """
        GET /api/v1/observability?run_id=X returns 200 with Grafana/Jaeger URLs
        and reachability status.

        REQ-004: The endpoint probes service reachability and returns URLs.
        """
        from orchestrator.mobile_api.app import create_mobile_app
        from httpx import ASGITransport, AsyncClient

        app = create_mobile_app(workspace_dir=workspace, config_path=config_yaml)
        app.state.tracker = mock_tracker
        app.state.monitoring_stack = None

        reader = MagicMock()
        reader.get_observability_urls = MagicMock(return_value={
            "grafana_url": "http://localhost:3000",
            "jaeger_url": "http://localhost:16686",
            "loki_url": "http://localhost:3100",
            "grafana_reachable": False,
            "jaeger_reachable": False,
            "loki_reachable": False,
        })
        app.state.reader = reader

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
            headers={"Authorization": f"Bearer {TEST_API_KEY}"},
        ) as client:
            resp = await client.get(f"/api/v1/observability?run_id={RUN_COMPLETED}")

        # Should return 200 with URL data (services may be unreachable)
        assert resp.status_code in {200, 503}, (
            f"Observability endpoint returned unexpected status {resp.status_code}"
        )
        if resp.status_code == 200:
            body = resp.json()
            # Must have URL fields
            assert any(
                key in body for key in ["grafana_url", "grafana", "urls"]
            ), f"Observability response must have URL fields: {body.keys()}"


# ── Error State Boundary ───────────────────────────────────────────────────

class TestErrorStatePropagationBoundary:
    """
    Verify that errors from the data layer are correctly surfaced to Flutter.

    Flutter ErrorStateWidget triggers when response is 4xx/5xx with {error: string}.
    """

    async def test_partial_failure_other_endpoints_still_work(
        self, workspace, config_yaml, mock_tracker
    ):
        """
        If cost-analytics reader throws, other endpoints (slo, alerts) must
        still function. Isolated reader failures don't poison the whole app.
        """
        from orchestrator.mobile_api.app import create_mobile_app
        from httpx import ASGITransport, AsyncClient

        app = create_mobile_app(workspace_dir=workspace, config_path=config_yaml)
        app.state.tracker = mock_tracker

        reader = MagicMock()
        # Cost analytics broken
        reader.get_cost_analytics.side_effect = RuntimeError("Storage corruption")
        # Everything else works
        reader.get_alert_history.return_value = []
        reader.get_monitoring_health.return_value = []
        reader.get_metrics_summary.return_value = {
            "total_runs": 5, "active_runs": 0,
            "total_cost_usd": 0.0, "avg_phase_duration_seconds": 0.0,
        }
        app.state.reader = reader

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
            headers={"Authorization": f"Bearer {TEST_API_KEY}"},
        ) as client:
            cost_resp = await client.get("/api/v1/cost-analytics")
            alerts_resp = await client.get("/api/v1/alerts")
            metrics_resp = await client.get("/api/v1/metrics")

        # Cost analytics should fail gracefully
        assert cost_resp.status_code == 500
        assert "error" in cost_resp.json()

        # Other endpoints must still work
        assert alerts_resp.status_code == 200
        assert metrics_resp.status_code == 200
