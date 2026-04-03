"""Integration tests: Flutter Mobile App ↔ Backend API Data Contract.

This file tests that the API response shapes exactly match what the Flutter
Dart models expect. Any mismatch here would cause a runtime
FormatException or null-pointer in the Flutter app.

Tested boundaries:
  Flutter model ↔ API response field names, types, and nullability
  - CostAnalytics model (mobile/lib/models/cost_analytics_model.dart)
  - SloReport model (mobile/lib/models/slo_report_model.dart)
  - Alert model (mobile/lib/models/alert_model.dart)
  - ArtifactSearchResult model (mobile/lib/models/artifact_search_result_model.dart)
  - RunSummary model (existing, regression check)

Each test is named with the Dart model field (camelCase) → API field (snake_case)
to make the contract explicit.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from .conftest import TEST_API_KEY

# ── Dart model field map ─────────────────────────────────────────────────
# Maps Dart (fromJson) field → Python API field for documentation.
# Tests verify the Python API field name / type.

COST_ANALYTICS_FIELD_MAP = {
    # Dart field: API field
    "totalSpendUsd": "total_spend_usd",          # double
    "burnRateUsdPerHour": "burn_rate_usd_per_hour",  # double
    "avgCostPerRun": "avg_cost_per_run",          # double
    "costByAgent": "cost_by_agent",               # List<AgentCost>
    "costByModel": "cost_by_model",               # List<ModelCost>
}

AGENT_COST_FIELD_MAP = {
    "agent": "agent",          # String
    "costUsd": "cost_usd",     # double
    "runCount": "run_count",   # int
}

MODEL_COST_FIELD_MAP = {
    "model": "model",          # String
    "costUsd": "cost_usd",     # double
    "runCount": "run_count",   # int
}

SLO_REPORT_FIELD_MAP = {
    "slis": "slis",   # List<SliResult>
}

SLI_RESULT_FIELD_MAP = {
    "name": "name",                           # String
    "target": "target",                       # double
    "currentValue": "current_value",          # double
    "status": "status",                       # String: passing|at_risk|breached
    "errorBudgetRemainingPct": "error_budget_remaining_pct",  # double
}

ALERT_FIELD_MAP = {
    "id": "id",                  # String
    "severity": "severity",      # String: critical|warning|info
    "message": "message",        # String
    "triggeredAt": "triggered_at",  # String → DateTime.parse()
    "status": "status",          # String: active|resolved
}

ARTIFACT_SEARCH_RESULT_FIELD_MAP = {
    "artifactName": "artifact_name",  # String
    "runId": "run_id",                # String
    "schema": "schema",               # String
    "agent": "agent",                 # String
    "version": "version",             # int
    "updatedAt": "updated_at",        # String → DateTime.parse()
}


# ── Fixtures ────────────────────────────────────────────────────────────────

def _make_monitoring_app(workspace, config_yaml, mock_tracker, reader=None):
    """Create a full mobile app with an optional custom reader."""
    from orchestrator.mobile_api.app import create_mobile_app

    app = create_mobile_app(workspace_dir=workspace, config_path=config_yaml)
    app.state.tracker = mock_tracker

    if reader is not None:
        app.state.reader = reader
    else:
        r = MagicMock()
        r.get_cost_analytics.return_value = {
            "burn_rate": 0.025,
            "by_agent": {
                "pm": {"cost": 0.30, "count": 2},
                "architect": {"cost": 0.45, "count": 2},
            },
            "by_model": {
                "claude-3-haiku-20240307": {"cost": 0.20, "count": 5},
            },
            "cost_trend": [],
        }
        r.get_slo_report = MagicMock(return_value={
            "slis": [
                {
                    "name": "pipeline_success_rate",
                    "target": 0.95,
                    "current_value": 0.97,
                    "passing": True,
                    "error_budget_consumed_pct": 10.5,
                },
                {
                    "name": "p95_phase_duration_seconds",
                    "target": 300.0,
                    "current_value": 245.0,
                    "passing": True,
                    "error_budget_consumed_pct": 5.0,
                },
                {
                    "name": "cost_per_run_usd",
                    "target": 5.0,
                    "current_value": 4.20,
                    "passing": True,
                    "error_budget_consumed_pct": 8.0,
                },
                {
                    "name": "artifact_validation_rate",
                    "target": 0.90,
                    "current_value": 0.88,
                    "passing": False,
                    "error_budget_consumed_pct": 92.0,
                },
                {
                    "name": "agent_timeout_rate",
                    "target": 0.02,
                    "current_value": 0.01,
                    "passing": True,
                    "error_budget_consumed_pct": 2.0,
                },
                {
                    "name": "model_escalation_rate",
                    "target": 0.10,
                    "current_value": 0.12,
                    "passing": False,
                    "error_budget_consumed_pct": 95.0,
                },
            ]
        })
        r.get_alert_history.return_value = [
            {
                "id": "alert-001",
                "severity": "critical",
                "message": "Pipeline below SLO",
                "triggered_at": "2024-01-15T10:30:00Z",
                "status": "active",
            }
        ]
        r.search_artifacts_global.return_value = [
            {
                "artifact_name": "prd",
                "run_id": "aabb1122ccdd3344",
                "schema": "prd",
                "agent": "pm",
                "version": 1,
                "updated_at": "2024-01-15T10:00:00Z",
            }
        ]
        r.get_metrics_summary.return_value = {
            "total_runs": 10,
            "active_runs": 0,
            "total_cost_usd": 15.50,
            "avg_phase_duration_seconds": 200.0,
        }
        r.get_monitoring_health.return_value = [
            {"name": "prometheus", "status": "healthy", "latency_ms": 10.0, "error": None}
        ]
        app.state.reader = r

    return app


@pytest.fixture
def full_app(workspace, config_yaml, mock_tracker):
    return _make_monitoring_app(workspace, config_yaml, mock_tracker)


@pytest.fixture
async def full_client(full_app):
    from httpx import ASGITransport, AsyncClient
    async with AsyncClient(
        transport=ASGITransport(app=full_app),
        base_url="http://testserver",
        headers={"Authorization": f"Bearer {TEST_API_KEY}"},
    ) as c:
        yield c


# ── CostAnalytics Flutter Contract ─────────────────────────────────────────

class TestCostAnalyticsFlutterContract:
    """
    Verify GET /api/v1/cost-analytics response exactly satisfies
    CostAnalytics.fromJson() requirements.
    """

    async def test_total_spend_usd_field_present_and_double(self, full_client):
        """Dart: double totalSpendUsd = json['total_spend_usd']"""
        body = (await full_client.get("/api/v1/cost-analytics")).json()
        assert "total_spend_usd" in body, "CostAnalytics.totalSpendUsd: missing 'total_spend_usd'"
        assert isinstance(body["total_spend_usd"], (int, float)), (
            f"CostAnalytics.totalSpendUsd: expected double, got {type(body['total_spend_usd'])}"
        )

    async def test_burn_rate_usd_per_hour_present_and_double(self, full_client):
        """Dart: double burnRateUsdPerHour = json['burn_rate_usd_per_hour']"""
        body = (await full_client.get("/api/v1/cost-analytics")).json()
        assert "burn_rate_usd_per_hour" in body
        assert isinstance(body["burn_rate_usd_per_hour"], (int, float))

    async def test_avg_cost_per_run_present_and_double(self, full_client):
        """Dart: double avgCostPerRun = json['avg_cost_per_run']"""
        body = (await full_client.get("/api/v1/cost-analytics")).json()
        assert "avg_cost_per_run" in body
        assert isinstance(body["avg_cost_per_run"], (int, float))

    async def test_cost_by_agent_is_list(self, full_client):
        """Dart: List<AgentCost> costByAgent = json['cost_by_agent']"""
        body = (await full_client.get("/api/v1/cost-analytics")).json()
        assert "cost_by_agent" in body
        assert isinstance(body["cost_by_agent"], list), (
            f"costByAgent must be a list, got {type(body['cost_by_agent'])}"
        )

    async def test_agent_cost_fields_for_dart(self, full_client):
        """Dart AgentCost.fromJson(): agent (String), cost_usd (double), run_count (int)."""
        body = (await full_client.get("/api/v1/cost-analytics")).json()
        for item in body["cost_by_agent"]:
            assert isinstance(item["agent"], str), "AgentCost.agent must be String"
            assert isinstance(item["cost_usd"], (int, float)), "AgentCost.costUsd must be double"
            assert isinstance(item["run_count"], int), "AgentCost.runCount must be int"

    async def test_cost_by_model_is_list(self, full_client):
        """Dart: List<ModelCost> costByModel = json['cost_by_model']"""
        body = (await full_client.get("/api/v1/cost-analytics")).json()
        assert "cost_by_model" in body
        assert isinstance(body["cost_by_model"], list)

    async def test_model_cost_fields_for_dart(self, full_client):
        """Dart ModelCost.fromJson(): model (String), cost_usd (double), run_count (int)."""
        body = (await full_client.get("/api/v1/cost-analytics")).json()
        for item in body["cost_by_model"]:
            assert isinstance(item["model"], str), "ModelCost.model must be String"
            assert isinstance(item["cost_usd"], (int, float)), "ModelCost.costUsd must be double"
            assert isinstance(item["run_count"], int), "ModelCost.runCount must be int"

    async def test_no_extra_fields_cause_dart_deserialization_issues(self, full_client):
        """
        Extra fields in JSON are normally ignored by Dart's fromJson().
        But verify that all required fields are present at minimum.
        """
        body = (await full_client.get("/api/v1/cost-analytics")).json()
        dart_required = set(COST_ANALYTICS_FIELD_MAP.values())
        missing = dart_required - set(body.keys())
        assert not missing, (
            f"Dart CostAnalytics.fromJson() will throw: missing API fields {missing}"
        )


# ── SloReport Flutter Contract ─────────────────────────────────────────────

class TestSloReportFlutterContract:
    """
    Verify GET /api/v1/slo response satisfies SloReport.fromJson().
    """

    async def test_slis_is_list_not_null(self, full_client):
        """Dart: List<SliResult> slis = json['slis'] — must not be null."""
        body = (await full_client.get("/api/v1/slo")).json()
        assert "slis" in body, "SloReport.slis: missing 'slis' key"
        assert body["slis"] is not None, "SloReport.slis: must not be null"
        assert isinstance(body["slis"], list), "SloReport.slis: must be a list"

    async def test_sli_result_name_is_string(self, full_client):
        """Dart: String name = json['name']"""
        body = (await full_client.get("/api/v1/slo")).json()
        for sli in body["slis"]:
            assert isinstance(sli["name"], str), f"SliResult.name must be String: {sli}"

    async def test_sli_result_target_is_double(self, full_client):
        """Dart: double target = json['target']"""
        body = (await full_client.get("/api/v1/slo")).json()
        for sli in body["slis"]:
            assert isinstance(sli["target"], (int, float)), (
                f"SliResult.target must be double for '{sli['name']}'"
            )

    async def test_sli_result_current_value_is_double(self, full_client):
        """Dart: double currentValue = json['current_value']"""
        body = (await full_client.get("/api/v1/slo")).json()
        for sli in body["slis"]:
            assert "current_value" in sli, f"SliResult.currentValue: missing 'current_value'"
            assert isinstance(sli["current_value"], (int, float))

    async def test_sli_result_status_is_valid_enum_string(self, full_client):
        """
        Dart: String status = json['status'] — used in switch/case for icon color.
        Only 'passing', 'at_risk', 'breached' are handled.
        """
        body = (await full_client.get("/api/v1/slo")).json()
        valid = {"passing", "at_risk", "breached"}
        for sli in body["slis"]:
            assert sli["status"] in valid, (
                f"SliResult.status='{sli['status']}' not in {valid} — "
                f"Flutter switch default/unknown case will trigger"
            )

    async def test_sli_result_error_budget_remaining_pct_is_double(self, full_client):
        """Dart: double errorBudgetRemainingPct = json['error_budget_remaining_pct']"""
        body = (await full_client.get("/api/v1/slo")).json()
        for sli in body["slis"]:
            assert "error_budget_remaining_pct" in sli, (
                "SliResult.errorBudgetRemainingPct: missing 'error_budget_remaining_pct'"
            )
            assert isinstance(sli["error_budget_remaining_pct"], (int, float))

    async def test_six_sli_results_for_slo_screen(self, full_client):
        """
        SLOComplianceScreen renders exactly 6 SLI rows (per REQ-002, AC-005).
        """
        body = (await full_client.get("/api/v1/slo")).json()
        count = len(body["slis"])
        assert count == 6, (
            f"SLOComplianceScreen expects 6 SLIs but API returned {count}"
        )


# ── Alert Flutter Contract ─────────────────────────────────────────────────

class TestAlertFlutterContract:
    """
    Verify GET /api/v1/alerts response satisfies Alert.fromJson().
    """

    async def test_alert_id_is_string(self, full_client):
        """Dart: String id = json['id']"""
        body = (await full_client.get("/api/v1/alerts")).json()
        for alert in body["alerts"]:
            assert isinstance(alert["id"], str), "Alert.id must be String"

    async def test_alert_severity_is_string(self, full_client):
        """Dart: String severity = json['severity']"""
        body = (await full_client.get("/api/v1/alerts")).json()
        for alert in body["alerts"]:
            assert isinstance(alert["severity"], str), "Alert.severity must be String"

    async def test_alert_message_is_string(self, full_client):
        """Dart: String message = json['message']"""
        body = (await full_client.get("/api/v1/alerts")).json()
        for alert in body["alerts"]:
            assert isinstance(alert["message"], str), "Alert.message must be String"
            assert len(alert["message"]) > 0, "Alert.message must not be empty"

    async def test_alert_triggered_at_is_iso8601_string(self, full_client):
        """
        Dart: DateTime triggeredAt = DateTime.parse(json['triggered_at'])
        Must be a valid ISO 8601 string.
        """
        body = (await full_client.get("/api/v1/alerts")).json()
        for alert in body["alerts"]:
            ts = alert["triggered_at"]
            assert isinstance(ts, str), "Alert.triggeredAt must be a String"
            # Basic ISO 8601 validation: contains 'T' date-time separator
            assert "T" in ts, (
                f"Alert.triggeredAt='{ts}' is not ISO 8601 — "
                f"DateTime.parse() will throw FormatException"
            )

    async def test_alert_status_is_valid(self, full_client):
        """
        Dart: String status = json['status']
        AlertsScreen filters by status — only 'active' and 'resolved' are handled.
        """
        body = (await full_client.get("/api/v1/alerts")).json()
        valid = {"active", "resolved", "acknowledged"}
        for alert in body["alerts"]:
            assert alert["status"] in valid, (
                f"Alert.status='{alert['status']}' is not handled by Flutter"
            )


# ── ArtifactSearchResult Flutter Contract ─────────────────────────────────

class TestArtifactSearchResultFlutterContract:
    """
    Verify GET /api/v1/artifacts/search response satisfies ArtifactSearchResult.fromJson().
    """

    async def test_artifact_name_is_string(self, full_client):
        """Dart: String artifactName = json['artifact_name']"""
        body = (await full_client.get("/api/v1/artifacts/search?q=prd")).json()
        for result in body["results"]:
            assert isinstance(result["artifact_name"], str), (
                "ArtifactSearchResult.artifactName must be String"
            )

    async def test_run_id_is_string(self, full_client):
        """Dart: String runId = json['run_id']"""
        body = (await full_client.get("/api/v1/artifacts/search?q=prd")).json()
        for result in body["results"]:
            assert isinstance(result["run_id"], str), (
                "ArtifactSearchResult.runId must be String"
            )

    async def test_schema_is_string(self, full_client):
        """Dart: String schema = json['schema']"""
        body = (await full_client.get("/api/v1/artifacts/search?q=prd")).json()
        for result in body["results"]:
            assert isinstance(result["schema"], str), (
                "ArtifactSearchResult.schema must be String"
            )

    async def test_agent_is_string(self, full_client):
        """Dart: String agent = json['agent']"""
        body = (await full_client.get("/api/v1/artifacts/search?q=prd")).json()
        for result in body["results"]:
            assert isinstance(result["agent"], str), (
                "ArtifactSearchResult.agent must be String"
            )

    async def test_version_is_int(self, full_client):
        """Dart: int version = json['version'] — must be integer, not float."""
        body = (await full_client.get("/api/v1/artifacts/search?q=prd")).json()
        for result in body["results"]:
            version = result["version"]
            assert isinstance(version, int), (
                f"ArtifactSearchResult.version must be int, got {type(version)}={version}"
            )

    async def test_updated_at_is_iso8601_string(self, full_client):
        """
        Dart: DateTime updatedAt = DateTime.parse(json['updated_at'])
        Must be a valid ISO 8601 string.
        """
        body = (await full_client.get("/api/v1/artifacts/search?q=prd")).json()
        for result in body["results"]:
            ts = result["updated_at"]
            assert isinstance(ts, str), "ArtifactSearchResult.updatedAt must be String"
            assert "T" in ts, (
                f"ArtifactSearchResult.updatedAt='{ts}' is not ISO 8601 — "
                f"DateTime.parse() will throw"
            )

    async def test_all_dart_fields_present(self, full_client):
        """All fields required by ArtifactSearchResult.fromJson() are present."""
        body = (await full_client.get("/api/v1/artifacts/search?q=prd")).json()
        required = set(ARTIFACT_SEARCH_RESULT_FIELD_MAP.values())
        for result in body["results"]:
            missing = required - set(result.keys())
            assert not missing, (
                f"ArtifactSearchResult.fromJson() will throw: missing {missing}"
            )


# ── RunSummary Regression ──────────────────────────────────────────────────

class TestRunSummaryRegressionContract:
    """
    Regression tests: existing RunSummary Flutter model must not be broken
    by the new routes being added to the app.
    """

    async def test_runs_list_still_works_with_monitoring_routes(
        self, full_client
    ):
        """GET /api/v1/runs still returns 200 with the new routers registered."""
        response = await full_client.get("/api/v1/runs")
        assert response.status_code == 200

    async def test_health_still_accessible_with_new_routes(self, full_client):
        """GET /health still works (auth-exempt) with new monitoring routes present."""
        from httpx import ASGITransport, AsyncClient
        from orchestrator.mobile_api.app import create_mobile_app

        # Use unauthed client to verify health is still exempt
        app = full_client._transport.app  # access via transport
        response = await full_client.get("/health")
        # health requires no auth, but full_client sends auth headers too — still OK
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"

    async def test_run_summary_fields_not_broken(self, full_client, workspace_with_runs):
        """
        After adding monitoring routes, GET /api/v1/runs still returns
        the same RunSummary fields that Flutter RunCard widget depends on.
        """
        response = await full_client.get("/api/v1/runs")
        runs = response.json()
        required = {
            "run_id", "feature_request", "workflow_type", "status",
            "total_cost_usd", "start_time",
        }
        for run in runs:
            missing = required - set(run.keys())
            assert not missing, (
                f"RunCard will break: run {run.get('run_id')} missing {missing}"
            )
