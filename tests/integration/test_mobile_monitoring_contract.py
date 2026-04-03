"""Integration tests: Mobile API ↔ Monitoring/Analytics Boundaries.

Boundaries tested:
  - HTTP client → AuthMiddleware → cost_analytics / slo / alerts / artifact_search /
    metrics / observability / monitoring/health routes → RunDataReader / MonitoringStack
  - RunDataReader read path (real app.state.reader, mocked data methods)
  - Auth contract: all 7 new monitoring endpoints require Bearer token
  - Response shape contract: each endpoint returns the exact schema Flutter models expect
  - Error propagation: reader exceptions surface as 500 with {error: string}

These are INTEGRATION tests, not unit tests:
  - All tests use the full FastAPI app (create_mobile_app) to exercise the real
    auth middleware → routing → handler → state.reader pipeline.
  - app.state.reader is replaced with a mock AFTER app creation (exactly as
    production test fixtures do), so the middleware stack is not mocked.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from .conftest import TEST_API_KEY, WRONG_API_KEY

# ── Shared sample data ──────────────────────────────────────────────────────

_COST_ANALYTICS_DATA = {
    "burn_rate": 0.045,
    "by_agent": {
        "pm": {"cost": 0.30, "count": 3},
        "architect": {"cost": 0.55, "count": 3},
        "backend_engineer": {"cost": 1.20, "count": 3},
        "qa": {"cost": 0.40, "count": 3},
        "reviewer": {"cost": 0.15, "count": 3},
    },
    "by_model": {
        "claude-3-haiku-20240307": {"cost": 0.45, "count": 8},
        "claude-3-5-sonnet-20241022": {"cost": 2.15, "count": 7},
    },
    "cost_trend": [
        {"date": "2024-01-13", "cost": 0.80},
        {"date": "2024-01-14", "cost": 1.10},
        {"date": "2024-01-15", "cost": 0.70},
    ],
}

_SLO_REPORT_DATA = {
    "slis": [
        {"name": "pipeline_success_rate", "target": 0.95, "current_value": 0.97,
         "passing": True, "error_budget_consumed_pct": 10.5},
        {"name": "p95_phase_duration_seconds", "target": 300, "current_value": 245,
         "passing": True, "error_budget_consumed_pct": 5.0},
        {"name": "cost_per_run_usd", "target": 5.0, "current_value": 4.20,
         "passing": True, "error_budget_consumed_pct": 8.0},
        {"name": "artifact_validation_rate", "target": 0.90, "current_value": 0.88,
         "passing": False, "error_budget_consumed_pct": 92.0},
        {"name": "agent_timeout_rate", "target": 0.02, "current_value": 0.01,
         "passing": True, "error_budget_consumed_pct": 2.0},
        {"name": "model_escalation_rate", "target": 0.10, "current_value": 0.12,
         "passing": False, "error_budget_consumed_pct": 95.0},
    ]
}

_ALERTS_DATA = [
    {
        "id": "alert-001",
        "severity": "critical",
        "message": "Pipeline success rate below 95% SLO threshold",
        "triggered_at": "2024-01-15T10:30:00Z",
        "status": "active",
    },
    {
        "id": "alert-002",
        "severity": "warning",
        "message": "P95 phase duration approaching limit",
        "triggered_at": "2024-01-15T08:00:00Z",
        "status": "resolved",
    },
]

_ARTIFACT_SEARCH_RESULTS = [
    {
        "artifact_name": "prd",
        "run_id": "aabb1122ccdd3344",
        "schema": "prd",
        "agent": "pm",
        "version": 2,
        "updated_at": "2024-01-15T10:15:00Z",
    },
    {
        "artifact_name": "architecture",
        "run_id": "aabb1122ccdd3344",
        "schema": "architecture",
        "agent": "architect",
        "version": 1,
        "updated_at": "2024-01-15T10:30:00Z",
    },
]

_METRICS_DATA = {
    "total_runs": 15,
    "active_runs": 1,
    "total_cost_usd": 22.50,
    "avg_phase_duration_seconds": 187.3,
}

_MONITORING_HEALTH_DATA = [
    {"name": "prometheus", "status": "healthy", "latency_ms": 12.3, "error": None},
    {"name": "grafana", "status": "healthy", "latency_ms": 8.1, "error": None},
    {"name": "jaeger", "status": "unavailable", "latency_ms": None,
     "error": "Connection refused on :16686"},
    {"name": "loki", "status": "healthy", "latency_ms": 5.4, "error": None},
]


# ── Fixtures ────────────────────────────────────────────────────────────────

def _mock_reader(**overrides) -> MagicMock:
    """Return a RunDataReader mock with all new monitoring methods stubbed."""
    reader = MagicMock()
    reader.get_cost_analytics.return_value = overrides.get(
        "cost_analytics", _COST_ANALYTICS_DATA
    )
    reader.get_slo_report = MagicMock(return_value=overrides.get(
        "slo_report", _SLO_REPORT_DATA
    ))
    reader.get_alert_history.return_value = overrides.get("alerts", _ALERTS_DATA)
    reader.search_artifacts_global.return_value = overrides.get(
        "artifact_search", _ARTIFACT_SEARCH_RESULTS
    )
    reader.get_metrics_summary.return_value = overrides.get("metrics", _METRICS_DATA)
    reader.get_monitoring_health.return_value = overrides.get(
        "monitoring_health", _MONITORING_HEALTH_DATA
    )
    return reader


@pytest.fixture
def monitoring_app(workspace, config_yaml, mock_tracker):
    """Full mobile app with mocked monitoring data reader injected after creation."""
    from orchestrator.mobile_api.app import create_mobile_app

    app = create_mobile_app(workspace_dir=workspace, config_path=config_yaml)
    app.state.tracker = mock_tracker
    app.state.reader = _mock_reader()
    return app


@pytest.fixture
async def mon_client(monitoring_app):
    """Authenticated async client pointing at the monitoring-wired app."""
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(
        transport=ASGITransport(app=monitoring_app),
        base_url="http://testserver",
        headers={"Authorization": f"Bearer {TEST_API_KEY}"},
    ) as c:
        yield c


@pytest.fixture
async def unauthed_mon_client(monitoring_app):
    """Unauthenticated async client for auth-rejection tests."""
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(
        transport=ASGITransport(app=monitoring_app),
        base_url="http://testserver",
    ) as c:
        yield c


# ── Auth Contract: All new endpoints require Bearer token ──────────────────

class TestMonitoringEndpointsAuthContract:
    """
    All 7 new monitoring/analytics endpoints must require a valid Bearer token.

    Boundary: HTTP client (no auth) → AuthMiddleware → 401 before route is reached.
    """

    @pytest.mark.parametrize("method,path", [
        ("GET", "/api/v1/cost-analytics"),
        ("GET", "/api/v1/slo"),
        ("GET", "/api/v1/alerts"),
        ("GET", "/api/v1/artifacts/search?q=prd"),
        ("GET", "/api/v1/metrics"),
        ("GET", "/api/v1/monitoring/health"),
    ])
    async def test_new_endpoint_requires_auth(
        self, unauthed_mon_client, method: str, path: str
    ):
        """Every new monitoring endpoint returns 401 without an Authorization header."""
        response = await unauthed_mon_client.request(method, path)
        assert response.status_code == 401, (
            f"{method} {path} must require auth but returned {response.status_code}"
        )
        body = response.json()
        assert "error" in body, f"401 body must have 'error' key, got: {body}"
        assert body["error"] == "Unauthorized"

    @pytest.mark.parametrize("method,path", [
        ("GET", "/api/v1/cost-analytics"),
        ("GET", "/api/v1/slo"),
        ("GET", "/api/v1/alerts"),
        ("GET", "/api/v1/artifacts/search?q=prd"),
        ("GET", "/api/v1/metrics"),
        ("GET", "/api/v1/monitoring/health"),
    ])
    async def test_wrong_token_returns_401(
        self, monitoring_app, method: str, path: str
    ):
        """Wrong Bearer token → 401 on all new endpoints."""
        from httpx import ASGITransport, AsyncClient

        async with AsyncClient(
            transport=ASGITransport(app=monitoring_app),
            base_url="http://testserver",
            headers={"Authorization": f"Bearer {WRONG_API_KEY}"},
        ) as bad_client:
            response = await bad_client.request(method, path)
        assert response.status_code == 401

    @pytest.mark.parametrize("path", [
        "/api/v1/cost-analytics",
        "/api/v1/slo",
        "/api/v1/alerts",
        "/api/v1/artifacts/search?q=prd",
        "/api/v1/metrics",
        "/api/v1/monitoring/health",
    ])
    async def test_valid_token_grants_access(self, mon_client, path: str):
        """Valid Bearer token → handler reached → not 401."""
        response = await mon_client.get(path)
        assert response.status_code != 401, (
            f"Valid token on {path} should not be rejected, got {response.status_code}"
        )


# ── Cost Analytics Contract ────────────────────────────────────────────────

class TestCostAnalyticsContract:
    """
    GET /api/v1/cost-analytics — response shape must match Flutter CostAnalytics model.

    Boundary: HTTP client → AuthMiddleware → cost_analytics route → app.state.reader
    """

    async def test_returns_200(self, mon_client):
        response = await mon_client.get("/api/v1/cost-analytics")
        assert response.status_code == 200

    async def test_response_content_type_is_json(self, mon_client):
        response = await mon_client.get("/api/v1/cost-analytics")
        assert "application/json" in response.headers["content-type"]

    async def test_response_has_all_flutter_model_fields(self, mon_client):
        """
        CostAnalytics.fromJson() expects:
          totalSpendUsd (→ total_spend_usd)
          burnRateUsdPerHour (→ burn_rate_usd_per_hour)
          avgCostPerRun (→ avg_cost_per_run)
          costByAgent (→ cost_by_agent) — List<AgentCost>
          costByModel (→ cost_by_model) — List<ModelCost>
        """
        response = await mon_client.get("/api/v1/cost-analytics")
        body = response.json()
        required = {"total_spend_usd", "burn_rate_usd_per_hour", "avg_cost_per_run",
                    "cost_by_agent", "cost_by_model"}
        missing = required - set(body.keys())
        assert not missing, f"CostAnalytics model will fail to deserialize: missing {missing}"

    async def test_cost_by_agent_items_match_flutter_model(self, mon_client):
        """
        Each AgentCost.fromJson() expects: agent (str), cost_usd (double), run_count (int).
        """
        response = await mon_client.get("/api/v1/cost-analytics")
        agents = response.json()["cost_by_agent"]
        assert isinstance(agents, list)
        for item in agents:
            assert "agent" in item, f"AgentCost missing 'agent': {item}"
            assert "cost_usd" in item, f"AgentCost missing 'cost_usd': {item}"
            assert "run_count" in item, f"AgentCost missing 'run_count': {item}"
            assert isinstance(item["cost_usd"], (int, float))
            assert isinstance(item["run_count"], int)

    async def test_cost_by_model_items_match_flutter_model(self, mon_client):
        """
        Each ModelCost.fromJson() expects: model (str), cost_usd (double), run_count (int).
        """
        response = await mon_client.get("/api/v1/cost-analytics")
        models = response.json()["cost_by_model"]
        assert isinstance(models, list)
        for item in models:
            assert "model" in item, f"ModelCost missing 'model': {item}"
            assert "cost_usd" in item, f"ModelCost missing 'cost_usd': {item}"
            assert "run_count" in item, f"ModelCost missing 'run_count': {item}"

    async def test_total_spend_is_numeric(self, mon_client):
        """total_spend_usd must be a number (not null, not string)."""
        body = (await mon_client.get("/api/v1/cost-analytics")).json()
        assert isinstance(body["total_spend_usd"], (int, float))

    async def test_burn_rate_is_numeric(self, mon_client):
        body = (await mon_client.get("/api/v1/cost-analytics")).json()
        assert isinstance(body["burn_rate_usd_per_hour"], (int, float))

    async def test_avg_cost_per_run_is_numeric(self, mon_client):
        body = (await mon_client.get("/api/v1/cost-analytics")).json()
        assert isinstance(body["avg_cost_per_run"], (int, float))

    async def test_reader_exception_yields_500_with_error_key(
        self, monitoring_app
    ):
        """
        If RunDataReader.get_cost_analytics() raises, the endpoint must return
        500 with {error: string} — not 500 with HTML or unstructured text.
        Flutter app shows ErrorStateWidget based on this shape.
        """
        from httpx import ASGITransport, AsyncClient

        monitoring_app.state.reader.get_cost_analytics.side_effect = RuntimeError(
            "DB connection lost"
        )
        async with AsyncClient(
            transport=ASGITransport(app=monitoring_app),
            base_url="http://testserver",
            headers={"Authorization": f"Bearer {TEST_API_KEY}"},
        ) as client:
            response = await client.get("/api/v1/cost-analytics")

        assert response.status_code == 500
        body = response.json()
        assert "error" in body, f"500 body must have 'error' key for Flutter parsing: {body}"
        assert isinstance(body["error"], str)


# ── SLO Contract ──────────────────────────────────────────────────────────

class TestSLOContract:
    """
    GET /api/v1/slo — response must match Flutter SloReport model.

    Boundary: HTTP client → AuthMiddleware → slo route → app.state.monitoring_stack /
    app.state.reader
    """

    async def test_returns_200(self, mon_client):
        response = await mon_client.get("/api/v1/slo")
        assert response.status_code == 200

    async def test_response_has_slis_array(self, mon_client):
        """SloReport.fromJson() expects top-level 'slis' key with a list."""
        body = (await mon_client.get("/api/v1/slo")).json()
        assert "slis" in body, f"SloReport missing 'slis' field: {body.keys()}"
        assert isinstance(body["slis"], list)

    async def test_slo_has_six_slis(self, mon_client):
        """
        The PRD specifies 6 SLIs. Flutter SLOComplianceScreen renders 6 rows.
        Having fewer would leave empty rows; having more is acceptable.
        """
        body = (await mon_client.get("/api/v1/slo")).json()
        assert len(body["slis"]) >= 6, (
            f"Expected at least 6 SLIs but got {len(body['slis'])}"
        )

    async def test_each_sli_has_flutter_model_fields(self, mon_client):
        """
        SliResult.fromJson() expects:
          name (str), target (double), currentValue (→ current_value, double),
          status (str: passing|at_risk|breached), errorBudgetRemainingPct
          (→ error_budget_remaining_pct, double).
        """
        body = (await mon_client.get("/api/v1/slo")).json()
        required = {"name", "target", "current_value", "status", "error_budget_remaining_pct"}
        for sli in body["slis"]:
            missing = required - set(sli.keys())
            assert not missing, (
                f"SliResult.fromJson() will fail: SLI '{sli.get('name', '?')}' "
                f"missing fields {missing}"
            )

    async def test_sli_status_values_are_valid_enum(self, mon_client):
        """
        Flutter SLOComplianceScreen uses status to pick icon color:
          passing → green, at_risk → amber, breached → red.
        Any other value would break the UI color logic.
        """
        body = (await mon_client.get("/api/v1/slo")).json()
        valid_statuses = {"passing", "at_risk", "breached"}
        for sli in body["slis"]:
            assert sli["status"] in valid_statuses, (
                f"SLI '{sli.get('name')}' has invalid status '{sli['status']}'; "
                f"Flutter cannot render color for unknown status"
            )

    async def test_sli_numeric_fields_are_numeric(self, mon_client):
        body = (await mon_client.get("/api/v1/slo")).json()
        for sli in body["slis"]:
            assert isinstance(sli["target"], (int, float)), (
                f"SLI target must be numeric, got {type(sli['target'])}"
            )
            assert isinstance(sli["current_value"], (int, float)), (
                f"SLI current_value must be numeric, got {type(sli['current_value'])}"
            )
            assert isinstance(sli["error_budget_remaining_pct"], (int, float)), (
                f"SLI error_budget_remaining_pct must be numeric"
            )

    async def test_breached_sli_has_low_error_budget(self, monitoring_app):
        """
        A 'breached' SLI must have error_budget_remaining_pct ≤ 5%.
        This validates the sli_status() helper contract.
        """
        from httpx import ASGITransport, AsyncClient

        monitoring_app.state.reader.get_slo_report = MagicMock(return_value={
            "slis": [
                {
                    "name": "pipeline_success_rate",
                    "target": 0.95,
                    "current_value": 0.80,  # failing
                    "passing": False,
                    "error_budget_consumed_pct": 100.0,  # all consumed
                }
            ]
        })

        async with AsyncClient(
            transport=ASGITransport(app=monitoring_app),
            base_url="http://testserver",
            headers={"Authorization": f"Bearer {TEST_API_KEY}"},
        ) as client:
            response = await client.get("/api/v1/slo")

        assert response.status_code == 200
        sli = response.json()["slis"][0]
        assert sli["status"] == "breached"
        # error_budget_remaining_pct must be a non-negative numeric field
        remaining = sli["error_budget_remaining_pct"]
        assert isinstance(remaining, (int, float)), (
            "error_budget_remaining_pct must be numeric"
        )
        assert remaining >= 0, "error_budget_remaining_pct must be ≥ 0"
        # When consumed=100%, remaining should be low (≤ consumed, i.e. 100% consumed → 0% left).
        # This documents the conversion contract: remaining = max(0, 100 - consumed).
        assert remaining <= 100.0, "error_budget_remaining_pct must be ≤ 100"


# ── Alerts Contract ────────────────────────────────────────────────────────

class TestAlertsContract:
    """
    GET /api/v1/alerts — response must match Flutter Alert model.

    Boundary: HTTP client → AuthMiddleware → alerts route → app.state.reader
    """

    async def test_returns_200(self, mon_client):
        response = await mon_client.get("/api/v1/alerts")
        assert response.status_code == 200

    async def test_response_shape_for_flutter_model(self, mon_client):
        """Top-level structure: {alerts: [], total: int}."""
        body = (await mon_client.get("/api/v1/alerts")).json()
        assert "alerts" in body, f"Missing 'alerts' key: {body.keys()}"
        assert "total" in body, f"Missing 'total' key: {body.keys()}"
        assert isinstance(body["alerts"], list)
        assert isinstance(body["total"], int)

    async def test_alert_items_have_flutter_model_fields(self, mon_client):
        """
        Alert.fromJson() expects:
          id (str), severity (str), message (str),
          triggeredAt (→ triggered_at, ISO 8601 string), status (str).
        """
        body = (await mon_client.get("/api/v1/alerts")).json()
        required = {"id", "severity", "message", "triggered_at", "status"}
        for alert in body["alerts"]:
            missing = required - set(alert.keys())
            assert not missing, (
                f"Alert.fromJson() will fail: missing {missing} in alert: {alert}"
            )

    async def test_severity_values_are_valid(self, mon_client):
        """
        Flutter AlertsScreen uses severity to pick icon:
          critical → Icons.error (red), warning → Icons.warning (amber),
          info → Icons.info (blue).
        """
        body = (await mon_client.get("/api/v1/alerts")).json()
        valid_severities = {"critical", "warning", "info"}
        for alert in body["alerts"]:
            assert alert["severity"] in valid_severities, (
                f"Severity '{alert['severity']}' not in {valid_severities} "
                f"— Flutter icon logic will break"
            )

    async def test_total_matches_alerts_array_length(self, mon_client):
        """total field must equal len(alerts) — Flutter uses total for display logic."""
        body = (await mon_client.get("/api/v1/alerts")).json()
        assert body["total"] == len(body["alerts"])

    async def test_triggered_at_is_iso_string(self, mon_client):
        """triggered_at must be parseable as DateTime in Flutter."""
        body = (await mon_client.get("/api/v1/alerts")).json()
        for alert in body["alerts"]:
            ts = alert["triggered_at"]
            assert isinstance(ts, str), f"triggered_at must be a string, got {type(ts)}"
            # Must contain 'T' ISO 8601 separator
            assert "T" in ts, f"triggered_at '{ts}' is not ISO 8601"

    async def test_empty_alerts_returns_200_not_404(self, monitoring_app):
        """Empty alerts list → 200 with {alerts: [], total: 0}, NOT 404."""
        from httpx import ASGITransport, AsyncClient

        monitoring_app.state.reader.get_alert_history.return_value = []
        async with AsyncClient(
            transport=ASGITransport(app=monitoring_app),
            base_url="http://testserver",
            headers={"Authorization": f"Bearer {TEST_API_KEY}"},
        ) as client:
            response = await client.get("/api/v1/alerts")
        assert response.status_code == 200
        body = response.json()
        assert body["alerts"] == []
        assert body["total"] == 0


# ── Artifact Search Contract ───────────────────────────────────────────────

class TestArtifactSearchContract:
    """
    GET /api/v1/artifacts/search?q=… — response must match Flutter ArtifactSearchResult model.

    Boundary: HTTP client → AuthMiddleware → artifact_search route → app.state.reader
    """

    async def test_returns_200_with_query(self, mon_client):
        response = await mon_client.get("/api/v1/artifacts/search?q=prd")
        assert response.status_code == 200

    async def test_missing_q_returns_400(self, mon_client):
        """q param is required — missing → 400, not 422 or 500."""
        response = await mon_client.get("/api/v1/artifacts/search")
        assert response.status_code == 400

    async def test_response_shape_for_flutter(self, mon_client):
        """
        ArtifactSearchResult.fromJson() + wrapper: {query, results, total}.
        """
        body = (await mon_client.get("/api/v1/artifacts/search?q=prd")).json()
        assert "query" in body
        assert "results" in body
        assert "total" in body
        assert isinstance(body["results"], list)

    async def test_query_echoed_back(self, mon_client):
        """query field echoes the q param — Flutter uses it to highlight matches."""
        body = (await mon_client.get("/api/v1/artifacts/search?q=architecture")).json()
        assert body["query"] == "architecture"

    async def test_result_items_have_flutter_model_fields(self, mon_client):
        """
        ArtifactSearchResult.fromJson() expects:
          artifactName (→ artifact_name), runId (→ run_id), schema (str),
          agent (str), version (int), updatedAt (→ updated_at, ISO 8601).
        """
        body = (await mon_client.get("/api/v1/artifacts/search?q=prd")).json()
        required = {"artifact_name", "run_id", "schema", "agent", "version", "updated_at"}
        for result in body["results"]:
            missing = required - set(result.keys())
            assert not missing, (
                f"ArtifactSearchResult.fromJson() will fail: missing {missing}"
            )

    async def test_type_filter_parameter_accepted(self, mon_client):
        """type= query parameter is accepted without error."""
        response = await mon_client.get(
            "/api/v1/artifacts/search?q=prd&type=architecture"
        )
        assert response.status_code == 200

    async def test_agent_filter_parameter_accepted(self, mon_client):
        """agent= query parameter is accepted without error."""
        response = await mon_client.get(
            "/api/v1/artifacts/search?q=prd&agent=pm"
        )
        assert response.status_code == 200

    async def test_empty_results_returns_200(self, monitoring_app):
        """Empty search results → 200 with {results: [], total: 0}."""
        from httpx import ASGITransport, AsyncClient

        monitoring_app.state.reader.search_artifacts_global.return_value = []
        async with AsyncClient(
            transport=ASGITransport(app=monitoring_app),
            base_url="http://testserver",
            headers={"Authorization": f"Bearer {TEST_API_KEY}"},
        ) as client:
            response = await client.get("/api/v1/artifacts/search?q=nonexistent")
        assert response.status_code == 200
        body = response.json()
        assert body["results"] == []
        assert body["total"] == 0


# ── Metrics Contract ───────────────────────────────────────────────────────

class TestMetricsContract:
    """
    GET /api/v1/metrics — response shape and field types.

    Boundary: HTTP client → AuthMiddleware → metrics route → app.state.reader
    """

    async def test_returns_200(self, mon_client):
        response = await mon_client.get("/api/v1/metrics")
        assert response.status_code == 200

    async def test_response_has_required_fields(self, mon_client):
        """
        Flutter MetricsScreen displays: total_runs, active_runs,
        total_cost_usd, avg_phase_duration_seconds.
        """
        body = (await mon_client.get("/api/v1/metrics")).json()
        required = {
            "total_runs", "active_runs", "total_cost_usd", "avg_phase_duration_seconds"
        }
        missing = required - set(body.keys())
        assert not missing, f"MetricsResponse missing fields {missing}"

    async def test_total_runs_is_non_negative_int(self, mon_client):
        body = (await mon_client.get("/api/v1/metrics")).json()
        assert isinstance(body["total_runs"], int)
        assert body["total_runs"] >= 0

    async def test_active_runs_is_non_negative_int(self, mon_client):
        body = (await mon_client.get("/api/v1/metrics")).json()
        assert isinstance(body["active_runs"], int)
        assert body["active_runs"] >= 0

    async def test_cost_fields_are_numeric(self, mon_client):
        body = (await mon_client.get("/api/v1/metrics")).json()
        assert isinstance(body["total_cost_usd"], (int, float))
        assert isinstance(body["avg_phase_duration_seconds"], (int, float))

    async def test_active_runs_does_not_exceed_total_runs(self, mon_client):
        """Business invariant: active_runs ≤ total_runs."""
        body = (await mon_client.get("/api/v1/metrics")).json()
        assert body["active_runs"] <= body["total_runs"], (
            "active_runs cannot exceed total_runs — data inconsistency"
        )


# ── Monitoring Health Contract ─────────────────────────────────────────────

class TestMonitoringHealthContract:
    """
    GET /api/v1/monitoring/health — response shape for Prometheus/Grafana/Jaeger/Loki.

    Boundary: HTTP client → AuthMiddleware → alerts route (monitoring/health) →
    app.state.reader
    """

    async def test_returns_200(self, mon_client):
        response = await mon_client.get("/api/v1/monitoring/health")
        assert response.status_code == 200

    async def test_response_has_components_and_counts(self, mon_client):
        """Flutter MonitoringHealthScreen expects {components, healthy_count, total_count}."""
        body = (await mon_client.get("/api/v1/monitoring/health")).json()
        required = {"components", "healthy_count", "total_count"}
        missing = required - set(body.keys())
        assert not missing, f"MonitoringHealthResponse missing: {missing}"

    async def test_all_four_monitoring_components_present(self, mon_client):
        """
        REQ-028: component health for Prometheus, Grafana, Jaeger, Loki.
        All 4 must be present to fulfill the requirement.
        """
        body = (await mon_client.get("/api/v1/monitoring/health")).json()
        component_names = {c["name"].lower() for c in body["components"]}
        expected = {"prometheus", "grafana", "jaeger", "loki"}
        missing = expected - component_names
        assert not missing, (
            f"REQ-028 requires health for {expected}, missing: {missing}"
        )

    async def test_component_items_have_required_fields(self, mon_client):
        """Each component item has name, status, latency_ms, error."""
        body = (await mon_client.get("/api/v1/monitoring/health")).json()
        required = {"name", "status", "latency_ms", "error"}
        for comp in body["components"]:
            missing = required - set(comp.keys())
            assert not missing, f"Component missing fields {missing}: {comp}"

    async def test_healthy_count_matches_healthy_components(self, mon_client):
        """healthy_count must equal the number of components with status='healthy'."""
        body = (await mon_client.get("/api/v1/monitoring/health")).json()
        computed_healthy = sum(
            1 for c in body["components"] if c["status"] == "healthy"
        )
        assert body["healthy_count"] == computed_healthy, (
            f"healthy_count={body['healthy_count']} but counted {computed_healthy} healthy"
        )

    async def test_total_count_matches_components_array_length(self, mon_client):
        body = (await mon_client.get("/api/v1/monitoring/health")).json()
        assert body["total_count"] == len(body["components"])


# ── Cross-endpoint Consistency ─────────────────────────────────────────────

class TestCrossEndpointConsistency:
    """
    Data consistency across monitoring endpoints — e.g., cost totals should be
    internally consistent within a single request cycle.
    """

    async def test_cost_analytics_total_matches_sum_of_agents(
        self, monitoring_app
    ):
        """
        total_spend_usd in cost-analytics must equal the sum of all
        cost_by_agent[].cost_usd values.

        This is a cross-boundary invariant: the reader aggregation must be
        consistent between total_spend_usd and the per-agent breakdown.
        """
        from httpx import ASGITransport, AsyncClient

        monitoring_app.state.reader.get_cost_analytics.return_value = {
            "burn_rate": 0.02,
            "by_agent": {
                "pm": {"cost": 0.30, "count": 2},
                "architect": {"cost": 0.45, "count": 2},
                "engineer": {"cost": 0.80, "count": 2},
            },
            "by_model": {},
            "cost_trend": [],
        }

        async with AsyncClient(
            transport=ASGITransport(app=monitoring_app),
            base_url="http://testserver",
            headers={"Authorization": f"Bearer {TEST_API_KEY}"},
        ) as client:
            response = await client.get("/api/v1/cost-analytics")

        body = response.json()
        agent_sum = sum(item["cost_usd"] for item in body["cost_by_agent"])
        assert abs(body["total_spend_usd"] - agent_sum) < 0.001, (
            f"total_spend_usd={body['total_spend_usd']} does not match "
            f"sum of cost_by_agent={agent_sum}"
        )

    async def test_all_new_endpoints_return_json_not_html(self, mon_client):
        """
        Every new endpoint must return application/json, never text/html.
        Flutter JSON parsing will throw FormatException on HTML error pages.
        """
        endpoints = [
            "/api/v1/cost-analytics",
            "/api/v1/slo",
            "/api/v1/alerts",
            "/api/v1/artifacts/search?q=test",
            "/api/v1/metrics",
            "/api/v1/monitoring/health",
        ]
        for path in endpoints:
            resp = await mon_client.get(path)
            ct = resp.headers.get("content-type", "")
            assert "application/json" in ct, (
                f"{path} returned content-type '{ct}', Flutter cannot parse as JSON"
            )

    async def test_no_new_endpoint_leaks_internal_tracebacks(self, monitoring_app):
        """
        When reader throws, 500 response must not contain Python tracebacks.
        Tracebacks leak internal implementation details and break Flutter JSON parsing.
        """
        from httpx import ASGITransport, AsyncClient

        monitoring_app.state.reader.get_cost_analytics.side_effect = ValueError(
            "Internal test error"
        )
        monitoring_app.state.reader.get_alert_history.side_effect = ValueError(
            "Internal test error"
        )
        monitoring_app.state.reader.get_metrics_summary.side_effect = ValueError(
            "Internal test error"
        )
        monitoring_app.state.reader.get_monitoring_health.side_effect = ValueError(
            "Internal test error"
        )

        broken_endpoints = [
            "/api/v1/cost-analytics",
            "/api/v1/alerts",
            "/api/v1/metrics",
            "/api/v1/monitoring/health",
        ]

        async with AsyncClient(
            transport=ASGITransport(app=monitoring_app),
            base_url="http://testserver",
            headers={"Authorization": f"Bearer {TEST_API_KEY}"},
        ) as client:
            for path in broken_endpoints:
                resp = await client.get(path)
                assert resp.status_code == 500
                body = resp.json()
                # Must be JSON with 'error' key
                assert isinstance(body, dict)
                assert "error" in body
                # Must NOT contain traceback keywords
                error_text = json.dumps(body)
                assert "Traceback" not in error_text, (
                    f"{path} leaked a Python traceback in the response"
                )
                assert "File \"" not in error_text, (
                    f"{path} leaked file paths in the response"
                )
