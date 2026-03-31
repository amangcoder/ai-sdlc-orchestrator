"""Tests for the SLO compliance dashboard routes and get_slo_report() helper."""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient

from orchestrator.dashboard.data import _default_slo_report, get_slo_report
from orchestrator.dashboard.routes.slo import (
    _SLI_DISPLAY_NAMES,
    _budget_color,
    _enrich_slis,
    _format_sli_value,
    create_slo_router,
)
from orchestrator.monitoring.slo import SLIResult, SLOReport

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

TEMPLATES_DIR = (
    Path(__file__).parent.parent
    / "src"
    / "orchestrator"
    / "dashboard"
    / "templates"
)


def _make_app(monitoring_stack: Optional[Any] = None) -> FastAPI:
    """Create a minimal FastAPI test app with the SLO router mounted."""
    app = FastAPI()
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    router = create_slo_router(templates, monitoring_stack=monitoring_stack)
    app.include_router(router)
    return app


def _client(monitoring_stack: Optional[Any] = None) -> TestClient:
    return TestClient(_make_app(monitoring_stack), raise_server_exceptions=True)


def _make_slo_report(
    *,
    pipeline_success: float = 0.97,
    phase_dur_p95: float = 120.0,
    cost_p50: float = 0.50,
    artifact_rate: float = 1.0,
    error_rate: float = 0.5,
    recovery_rate: float = 1.0,
) -> SLOReport:
    """Build a fully-populated SLOReport for tests."""
    slis = [
        SLIResult(
            name="pipeline_success_rate",
            target=0.95,
            actual=pipeline_success,
            passing=pipeline_success >= 0.95,
            error_budget_remaining_pct=40.0,
        ),
        SLIResult(
            name="phase_duration_p95",
            target=300.0,
            actual=phase_dur_p95,
            passing=phase_dur_p95 <= 300.0,
            error_budget_remaining_pct=60.0,
        ),
        SLIResult(
            name="cost_per_run_p50",
            target=1.0,
            actual=cost_p50,
            passing=cost_p50 <= 1.0,
            error_budget_remaining_pct=50.0,
        ),
        SLIResult(
            name="artifact_validation_rate",
            target=0.99,
            actual=artifact_rate,
            passing=artifact_rate >= 0.99,
            error_budget_remaining_pct=100.0,
        ),
        SLIResult(
            name="error_rate",
            target=5.0,
            actual=error_rate,
            passing=error_rate <= 5.0,
            error_budget_remaining_pct=90.0,
        ),
        SLIResult(
            name="recovery_success_rate",
            target=0.90,
            actual=recovery_rate,
            passing=recovery_rate >= 0.90,
            error_budget_remaining_pct=100.0,
        ),
    ]
    return SLOReport(
        evaluated_at=datetime.now(tz=timezone.utc).isoformat(),
        evaluation_window_hours=24,
        slis=slis,
        all_passing=all(s.passing for s in slis),
    )


def _make_monitoring_stack(report: Optional[SLOReport] = None) -> MagicMock:
    """Return a mock MonitoringStack with an attached SLOTracker mock."""
    stack = MagicMock()
    tracker = MagicMock()
    tracker.evaluate_slos.return_value = report or _make_slo_report()
    stack.slo_tracker = tracker
    return stack


# ---------------------------------------------------------------------------
# _default_slo_report()
# ---------------------------------------------------------------------------


class TestDefaultSloReport:
    """Unit tests for the neutral default SLO report."""

    def test_returns_slo_report_instance(self) -> None:
        result = _default_slo_report()
        assert isinstance(result, SLOReport)

    def test_has_six_slis(self) -> None:
        result = _default_slo_report()
        assert len(result.slis) == 6

    def test_sli_names_are_canonical(self) -> None:
        result = _default_slo_report()
        names = {s.name for s in result.slis}
        expected = {
            "pipeline_success_rate",
            "phase_duration_p95",
            "cost_per_run_p50",
            "artifact_validation_rate",
            "error_rate",
            "recovery_success_rate",
        }
        assert names == expected

    def test_error_budgets_are_100(self) -> None:
        result = _default_slo_report()
        for sli in result.slis:
            assert sli.error_budget_remaining_pct == 100.0, (
                f"{sli.name}: expected 100.0, got {sli.error_budget_remaining_pct}"
            )

    def test_evaluation_window_hours_is_24(self) -> None:
        result = _default_slo_report()
        assert result.evaluation_window_hours == 24

    def test_all_passing_is_false(self) -> None:
        # pipeline_success_rate actual=0.0 (no runs) → passing=False
        result = _default_slo_report()
        assert result.all_passing is False


# ---------------------------------------------------------------------------
# get_slo_report()
# ---------------------------------------------------------------------------


class TestGetSloReport:
    """Unit tests for the get_slo_report() data helper."""

    def test_data_available_false_without_stack(self) -> None:
        result = get_slo_report(monitoring_stack=None)
        assert result["data_available"] is False

    def test_returns_default_report_without_stack(self) -> None:
        result = get_slo_report(monitoring_stack=None)
        assert result["report"] is not None
        assert isinstance(result["report"], SLOReport)

    def test_data_available_true_with_stack(self) -> None:
        stack = _make_monitoring_stack()
        result = get_slo_report(monitoring_stack=stack)
        assert result["data_available"] is True

    def test_calls_evaluate_slos_on_tracker(self) -> None:
        stack = _make_monitoring_stack()
        get_slo_report(monitoring_stack=stack)
        stack.slo_tracker.evaluate_slos.assert_called_once()

    def test_returns_real_report_from_tracker(self) -> None:
        expected_report = _make_slo_report()
        stack = _make_monitoring_stack(report=expected_report)
        result = get_slo_report(monitoring_stack=stack)
        assert result["report"] is expected_report

    def test_falls_back_to_default_when_slo_tracker_is_none(self) -> None:
        stack = MagicMock()
        stack.slo_tracker = None
        result = get_slo_report(monitoring_stack=stack)
        assert result["data_available"] is False
        assert isinstance(result["report"], SLOReport)

    def test_falls_back_to_default_when_evaluate_slos_raises(self) -> None:
        stack = MagicMock()
        stack.slo_tracker.evaluate_slos.side_effect = RuntimeError("boom")
        result = get_slo_report(monitoring_stack=stack)
        assert result["data_available"] is False
        assert isinstance(result["report"], SLOReport)


# ---------------------------------------------------------------------------
# _budget_color()
# ---------------------------------------------------------------------------


class TestBudgetColor:
    """Unit tests for the error-budget color classifier."""

    def test_100_pct_is_green(self) -> None:
        assert _budget_color(100.0) == "green"

    def test_80_pct_is_green(self) -> None:
        assert _budget_color(80.0) == "green"

    def test_79_pct_is_yellow(self) -> None:
        assert _budget_color(79.9) == "yellow"

    def test_20_pct_is_yellow(self) -> None:
        assert _budget_color(20.0) == "yellow"

    def test_19_pct_is_red(self) -> None:
        assert _budget_color(19.9) == "red"

    def test_0_pct_is_red(self) -> None:
        assert _budget_color(0.0) == "red"


# ---------------------------------------------------------------------------
# _format_sli_value()
# ---------------------------------------------------------------------------


class TestFormatSliValue:
    """Unit tests for the SLI value formatter."""

    def test_pipeline_success_rate_as_percent(self) -> None:
        assert _format_sli_value("pipeline_success_rate", 0.95) == "95.00%"

    def test_artifact_validation_rate_as_percent(self) -> None:
        assert _format_sli_value("artifact_validation_rate", 1.0) == "100.00%"

    def test_recovery_success_rate_as_percent(self) -> None:
        assert _format_sli_value("recovery_success_rate", 0.9) == "90.00%"

    def test_phase_duration_p95_as_seconds(self) -> None:
        assert _format_sli_value("phase_duration_p95", 120.0) == "120.0s"

    def test_cost_per_run_p50_as_usd(self) -> None:
        assert _format_sli_value("cost_per_run_p50", 0.5) == "$0.5000"

    def test_error_rate_as_plain_float(self) -> None:
        assert _format_sli_value("error_rate", 1.5) == "1.50"


# ---------------------------------------------------------------------------
# _enrich_slis()
# ---------------------------------------------------------------------------


class TestEnrichSlis:
    """Unit tests for the SLI enrichment helper."""

    def _slis(self) -> list[SLIResult]:
        return _make_slo_report().slis

    def test_returns_same_count(self) -> None:
        enriched = _enrich_slis(self._slis())
        assert len(enriched) == 6

    def test_adds_budget_color(self) -> None:
        enriched = _enrich_slis(self._slis())
        for item in enriched:
            assert "budget_color" in item
            assert item["budget_color"] in ("green", "yellow", "red")

    def test_adds_budget_pct(self) -> None:
        enriched = _enrich_slis(self._slis())
        for item in enriched:
            assert "budget_pct" in item
            assert 0.0 <= item["budget_pct"] <= 100.0

    def test_adds_target_display(self) -> None:
        enriched = _enrich_slis(self._slis())
        for item in enriched:
            assert "target_display" in item
            assert isinstance(item["target_display"], str)
            assert len(item["target_display"]) > 0

    def test_adds_actual_display(self) -> None:
        enriched = _enrich_slis(self._slis())
        for item in enriched:
            assert "actual_display" in item
            assert isinstance(item["actual_display"], str)

    def test_pipeline_success_rate_display_contains_pct_sign(self) -> None:
        enriched = _enrich_slis(self._slis())
        psr = next(e for e in enriched if e["name"] == "pipeline_success_rate")
        assert "%" in psr["target_display"]
        assert "%" in psr["actual_display"]

    def test_budget_clamped_to_0_100(self) -> None:
        # Construct an SLIResult with budget > 100 to verify clamping.
        sli = SLIResult(
            name="error_rate",
            target=5.0,
            actual=0.0,
            passing=True,
            error_budget_remaining_pct=150.0,  # deliberately > 100
        )
        enriched = _enrich_slis([sli])
        assert enriched[0]["budget_pct"] == 100.0

    def test_adds_display_name(self) -> None:
        enriched = _enrich_slis(self._slis())
        for item in enriched:
            assert "display_name" in item
            assert isinstance(item["display_name"], str)
            assert len(item["display_name"]) > 0

    def test_display_name_pipeline_success_rate(self) -> None:
        enriched = _enrich_slis(self._slis())
        psr = next(e for e in enriched if e["name"] == "pipeline_success_rate")
        assert psr["display_name"] == "Pipeline Success Rate"

    def test_display_name_phase_duration_p95(self) -> None:
        enriched = _enrich_slis(self._slis())
        item = next(e for e in enriched if e["name"] == "phase_duration_p95")
        assert item["display_name"] == "Phase Duration p95"

    def test_display_name_cost_per_run_p50(self) -> None:
        enriched = _enrich_slis(self._slis())
        item = next(e for e in enriched if e["name"] == "cost_per_run_p50")
        assert item["display_name"] == "Cost Per Run p50"

    def test_display_name_error_rate(self) -> None:
        enriched = _enrich_slis(self._slis())
        item = next(e for e in enriched if e["name"] == "error_rate")
        assert item["display_name"] == "Error Rate Per Run"

    def test_display_name_recovery_success_rate(self) -> None:
        enriched = _enrich_slis(self._slis())
        item = next(e for e in enriched if e["name"] == "recovery_success_rate")
        assert item["display_name"] == "Recovery Success Rate"

    def test_display_name_fallback_to_canonical_name(self) -> None:
        sli = SLIResult(
            name="unknown_sli",
            target=1.0,
            actual=1.0,
            passing=True,
            error_budget_remaining_pct=100.0,
        )
        enriched = _enrich_slis([sli])
        assert enriched[0]["display_name"] == "unknown_sli"


# ---------------------------------------------------------------------------
# GET /slo — HTML endpoint
# ---------------------------------------------------------------------------


class TestSloPageHtml:
    """Integration tests for the GET /slo HTML endpoint."""

    def test_returns_200_without_monitoring_stack(self) -> None:
        resp = _client().get("/slo")
        assert resp.status_code == 200

    def test_returns_200_with_monitoring_stack(self) -> None:
        stack = _make_monitoring_stack()
        resp = _client(stack).get("/slo")
        assert resp.status_code == 200

    def test_response_is_html(self) -> None:
        resp = _client().get("/slo")
        assert "text/html" in resp.headers["content-type"]

    def test_page_contains_six_sli_rows_without_stack(self) -> None:
        resp = _client().get("/slo")
        body = resp.text
        # Each SLI name appears in the table.
        sli_names = [
            "pipeline_success_rate",
            "phase_duration_p95",
            "cost_per_run_p50",
            "artifact_validation_rate",
            "error_rate",
            "recovery_success_rate",
        ]
        for name in sli_names:
            assert name in body, f"SLI {name!r} missing from page"

    def test_page_contains_six_sli_rows_with_stack(self) -> None:
        stack = _make_monitoring_stack()
        resp = _client(stack).get("/slo")
        body = resp.text
        sli_names = [
            "pipeline_success_rate",
            "phase_duration_p95",
            "cost_per_run_p50",
            "artifact_validation_rate",
            "error_rate",
            "recovery_success_rate",
        ]
        for name in sli_names:
            assert name in body, f"SLI {name!r} missing from page"

    def test_page_shows_tracker_not_enabled_notice_without_stack(self) -> None:
        resp = _client().get("/slo")
        assert "SLO tracking not enabled" in resp.text

    def test_page_does_not_show_notice_with_stack(self) -> None:
        stack = _make_monitoring_stack()
        resp = _client(stack).get("/slo")
        assert "SLO tracking not enabled" not in resp.text

    def test_page_contains_error_budget_gauges(self) -> None:
        resp = _client().get("/slo")
        # Progress bars are rendered as divs with role="progressbar".
        assert 'role="progressbar"' in resp.text

    def test_page_contains_compliance_matrix_heading(self) -> None:
        resp = _client().get("/slo")
        assert "SLI Compliance Matrix" in resp.text

    def test_page_contains_violation_timeline_heading(self) -> None:
        resp = _client().get("/slo")
        assert "Violation Timeline" in resp.text

    def test_page_title_is_slo_compliance(self) -> None:
        resp = _client().get("/slo")
        assert "SLO Compliance" in resp.text

    def test_passing_slo_shows_all_passing_status(self) -> None:
        # Build a report where all SLIs pass.
        report = _make_slo_report(
            pipeline_success=0.99,
            phase_dur_p95=50.0,
            cost_p50=0.10,
            artifact_rate=1.0,
            error_rate=0.0,
            recovery_rate=1.0,
        )
        # Patch all_passing to True.
        slis_passing = [dataclasses.replace(s, passing=True) for s in report.slis]
        report_all_pass = dataclasses.replace(report, slis=slis_passing, all_passing=True)
        stack = _make_monitoring_stack(report=report_all_pass)
        resp = _client(stack).get("/slo")
        assert "All Passing" in resp.text

    def test_failing_slo_shows_violations_status(self) -> None:
        report = _make_slo_report(pipeline_success=0.50)  # below 0.95 target
        slis_fail = []
        for s in report.slis:
            if s.name == "pipeline_success_rate":
                slis_fail.append(dataclasses.replace(s, passing=False, error_budget_remaining_pct=10.0))
            else:
                slis_fail.append(s)
        report_fail = dataclasses.replace(report, slis=slis_fail, all_passing=False)
        stack = _make_monitoring_stack(report=report_fail)
        resp = _client(stack).get("/slo")
        assert "Violations" in resp.text

    def test_no_violations_message_when_all_passing(self) -> None:
        slis_passing = [
            dataclasses.replace(s, passing=True, error_budget_remaining_pct=95.0)
            for s in _make_slo_report().slis
        ]
        report = dataclasses.replace(_make_slo_report(), slis=slis_passing, all_passing=True)
        stack = _make_monitoring_stack(report=report)
        resp = _client(stack).get("/slo")
        assert "No active violations" in resp.text

    def test_color_classes_present_for_gauges(self) -> None:
        # Progress bars should have one of the three color values.
        resp = _client().get("/slo")
        body = resp.text
        # At least one color is rendered (green for 100% budget in defaults).
        assert "#22c55e" in body or "#f59e0b" in body or "#ef4444" in body

    def test_compliance_matrix_has_five_columns(self) -> None:
        resp = _client().get("/slo")
        body = resp.text
        # Five <th> elements should appear in the compliance matrix table.
        assert "SLI Name" in body
        assert "Target" in body
        assert "Current Value" in body
        assert "Status" in body
        assert "Error Budget Remaining" in body

    def test_compliance_matrix_shows_human_readable_names(self) -> None:
        resp = _client().get("/slo")
        body = resp.text
        assert "Pipeline Success Rate" in body
        assert "Phase Duration p95" in body
        assert "Cost Per Run p50" in body
        assert "Artifact Validation Rate" in body
        assert "Error Rate Per Run" in body
        assert "Recovery Success Rate" in body

    def test_compliance_matrix_has_inline_progress_bars(self) -> None:
        # The compliance matrix table rows include progress bars.
        resp = _client().get("/slo")
        # Both the gauge section and inline matrix bars use role="progressbar".
        assert resp.text.count('role="progressbar"') >= 6

    def test_status_uses_passing_label(self) -> None:
        # Defaults have 100% error budget → green → "Passing" label.
        resp = _client().get("/slo")
        assert "Passing" in resp.text

    def test_breached_status_label_shown_for_low_budget(self) -> None:
        slis_breach = [
            dataclasses.replace(s, passing=False, error_budget_remaining_pct=5.0)
            for s in _make_slo_report().slis
        ]
        report = dataclasses.replace(_make_slo_report(), slis=slis_breach, all_passing=False)
        stack = _make_monitoring_stack(report=report)
        resp = _client(stack).get("/slo")
        assert "Breached" in resp.text

    def test_at_risk_status_label_shown_for_medium_budget(self) -> None:
        slis_risk = [
            dataclasses.replace(s, passing=False, error_budget_remaining_pct=50.0)
            for s in _make_slo_report().slis
        ]
        report = dataclasses.replace(_make_slo_report(), slis=slis_risk, all_passing=False)
        stack = _make_monitoring_stack(report=report)
        resp = _client(stack).get("/slo")
        assert "At Risk" in resp.text

    def test_summary_banner_all_passing(self) -> None:
        slis_passing = [
            dataclasses.replace(s, passing=True, error_budget_remaining_pct=95.0)
            for s in _make_slo_report().slis
        ]
        report = dataclasses.replace(_make_slo_report(), slis=slis_passing, all_passing=True)
        stack = _make_monitoring_stack(report=report)
        resp = _client(stack).get("/slo")
        assert "All SLIs Passing" in resp.text

    def test_summary_banner_breached(self) -> None:
        slis_breach = [
            dataclasses.replace(s, passing=False, error_budget_remaining_pct=5.0)
            for s in _make_slo_report().slis
        ]
        report = dataclasses.replace(_make_slo_report(), slis=slis_breach, all_passing=False)
        stack = _make_monitoring_stack(report=report)
        resp = _client(stack).get("/slo")
        assert "Breached" in resp.text

    def test_summary_banner_at_risk(self) -> None:
        slis_risk = [
            dataclasses.replace(s, passing=False, error_budget_remaining_pct=50.0)
            for s in _make_slo_report().slis
        ]
        report = dataclasses.replace(_make_slo_report(), slis=slis_risk, all_passing=False)
        stack = _make_monitoring_stack(report=report)
        resp = _client(stack).get("/slo")
        assert "At Risk" in resp.text

    def test_status_uses_checkmark_icon_for_passing(self) -> None:
        slis_passing = [
            dataclasses.replace(s, passing=True, error_budget_remaining_pct=95.0)
            for s in _make_slo_report().slis
        ]
        report = dataclasses.replace(_make_slo_report(), slis=slis_passing, all_passing=True)
        stack = _make_monitoring_stack(report=report)
        resp = _client(stack).get("/slo")
        # ✓ checkmark appears in the banner or status column
        assert "✓" in resp.text

    def test_status_uses_warning_icon_for_at_risk(self) -> None:
        slis_risk = [
            dataclasses.replace(s, passing=False, error_budget_remaining_pct=50.0)
            for s in _make_slo_report().slis
        ]
        report = dataclasses.replace(_make_slo_report(), slis=slis_risk, all_passing=False)
        stack = _make_monitoring_stack(report=report)
        resp = _client(stack).get("/slo")
        # ⚠ warning icon appears
        assert "⚠" in resp.text

    def test_status_uses_cross_icon_for_breached(self) -> None:
        slis_breach = [
            dataclasses.replace(s, passing=False, error_budget_remaining_pct=5.0)
            for s in _make_slo_report().slis
        ]
        report = dataclasses.replace(_make_slo_report(), slis=slis_breach, all_passing=False)
        stack = _make_monitoring_stack(report=report)
        resp = _client(stack).get("/slo")
        # ✗ cross icon appears
        assert "✗" in resp.text


# ---------------------------------------------------------------------------
# GET /api/v1/slo — JSON endpoint
# ---------------------------------------------------------------------------


class TestSloApiJson:
    """Integration tests for GET /api/v1/slo."""

    def test_returns_200(self) -> None:
        resp = _client().get("/api/v1/slo")
        assert resp.status_code == 200

    def test_returns_json_content_type(self) -> None:
        resp = _client().get("/api/v1/slo")
        assert "application/json" in resp.headers["content-type"]

    def test_response_has_required_top_level_keys(self) -> None:
        resp = _client().get("/api/v1/slo")
        data = resp.json()
        assert "evaluated_at" in data
        assert "evaluation_window_hours" in data
        assert "all_passing" in data
        assert "slis" in data
        assert "data_available" in data

    def test_slis_is_a_list_with_six_entries_without_stack(self) -> None:
        resp = _client().get("/api/v1/slo")
        data = resp.json()
        assert isinstance(data["slis"], list)
        assert len(data["slis"]) == 6

    def test_data_available_false_without_stack(self) -> None:
        resp = _client().get("/api/v1/slo")
        assert resp.json()["data_available"] is False

    def test_data_available_true_with_stack(self) -> None:
        stack = _make_monitoring_stack()
        resp = _client(stack).get("/api/v1/slo")
        assert resp.json()["data_available"] is True

    def test_sli_entries_have_required_fields(self) -> None:
        resp = _client().get("/api/v1/slo")
        for sli in resp.json()["slis"]:
            assert "name" in sli
            assert "target" in sli
            assert "actual" in sli
            assert "passing" in sli
            assert "error_budget_remaining_pct" in sli

    def test_all_six_sli_names_present(self) -> None:
        resp = _client().get("/api/v1/slo")
        names = {s["name"] for s in resp.json()["slis"]}
        expected = {
            "pipeline_success_rate",
            "phase_duration_p95",
            "cost_per_run_p50",
            "artifact_validation_rate",
            "error_rate",
            "recovery_success_rate",
        }
        assert names == expected

    def test_with_monitoring_stack_returns_real_values(self) -> None:
        report = _make_slo_report(pipeline_success=0.97)
        stack = _make_monitoring_stack(report=report)
        resp = _client(stack).get("/api/v1/slo")
        data = resp.json()
        psr = next(s for s in data["slis"] if s["name"] == "pipeline_success_rate")
        assert abs(psr["actual"] - 0.97) < 1e-6

    def test_evaluation_window_hours_is_integer(self) -> None:
        resp = _client().get("/api/v1/slo")
        assert isinstance(resp.json()["evaluation_window_hours"], int)


# ---------------------------------------------------------------------------
# Monitoring stack slo_tracker property
# ---------------------------------------------------------------------------


class TestMonitoringStackSloTrackerProperty:
    """Verify that MonitoringStack exposes a public slo_tracker property."""

    def test_property_exists(self) -> None:
        from orchestrator.monitoring import MonitoringStack
        assert hasattr(MonitoringStack, "slo_tracker")

    def test_property_returns_none_when_slo_disabled(self) -> None:
        from orchestrator.monitoring import MonitoringStack
        from orchestrator.monitoring.config import MonitoringConfig

        config = MonitoringConfig()  # slo.enabled defaults to False
        stack = MonitoringStack(
            config=config,
            run_id="test-run",
            workspace=Path("/tmp"),
        )
        assert stack.slo_tracker is None

    def test_property_returns_tracker_when_slo_enabled(self) -> None:
        from orchestrator.monitoring import MonitoringStack
        from orchestrator.monitoring.config import MonitoringConfig, SLOConfig
        from orchestrator.monitoring.slo import SLOTracker

        slo_config = SLOConfig(enabled=True)
        config = MonitoringConfig(slo=slo_config)
        stack = MonitoringStack(
            config=config,
            run_id="test-run",
            workspace=Path("/tmp"),
        )
        assert stack.slo_tracker is not None
        assert isinstance(stack.slo_tracker, SLOTracker)


# ---------------------------------------------------------------------------
# _SLI_DISPLAY_NAMES
# ---------------------------------------------------------------------------


class TestSliDisplayNames:
    """Unit tests for the _SLI_DISPLAY_NAMES mapping."""

    def test_all_six_canonical_names_present(self) -> None:
        expected = {
            "pipeline_success_rate",
            "phase_duration_p95",
            "cost_per_run_p50",
            "artifact_validation_rate",
            "error_rate",
            "recovery_success_rate",
        }
        assert expected.issubset(set(_SLI_DISPLAY_NAMES.keys()))

    def test_display_names_are_non_empty_strings(self) -> None:
        for canonical, display in _SLI_DISPLAY_NAMES.items():
            assert isinstance(display, str), f"{canonical!r} display is not a string"
            assert len(display) > 0, f"{canonical!r} display is empty"

    def test_pipeline_success_rate_display(self) -> None:
        assert _SLI_DISPLAY_NAMES["pipeline_success_rate"] == "Pipeline Success Rate"

    def test_error_rate_display(self) -> None:
        assert _SLI_DISPLAY_NAMES["error_rate"] == "Error Rate Per Run"


# ---------------------------------------------------------------------------
# Banner context variables (at_risk_count, breached_count, banner_status)
# ---------------------------------------------------------------------------


class TestBannerContext:
    """Integration tests verifying banner_status context values drive the HTML banner."""

    def test_banner_passing_rendered_when_all_green(self) -> None:
        slis_green = [
            dataclasses.replace(s, passing=True, error_budget_remaining_pct=90.0)
            for s in _make_slo_report().slis
        ]
        report = dataclasses.replace(_make_slo_report(), slis=slis_green, all_passing=True)
        stack = _make_monitoring_stack(report=report)
        resp = _client(stack).get("/slo")
        assert "All SLIs Passing" in resp.text

    def test_banner_at_risk_rendered_for_yellow_slis(self) -> None:
        # 20–80% budget → yellow color from _budget_color
        slis_yellow = [
            dataclasses.replace(s, passing=False, error_budget_remaining_pct=50.0)
            for s in _make_slo_report().slis
        ]
        report = dataclasses.replace(_make_slo_report(), slis=slis_yellow, all_passing=False)
        stack = _make_monitoring_stack(report=report)
        resp = _client(stack).get("/slo")
        body = resp.text
        assert "At Risk" in body
        assert "Breached" not in body.split("All SLIs Passing")[0]  # banner shows At Risk not Breached

    def test_banner_breached_rendered_for_red_slis(self) -> None:
        # <20% budget → red color from _budget_color
        slis_red = [
            dataclasses.replace(s, passing=False, error_budget_remaining_pct=5.0)
            for s in _make_slo_report().slis
        ]
        report = dataclasses.replace(_make_slo_report(), slis=slis_red, all_passing=False)
        stack = _make_monitoring_stack(report=report)
        resp = _client(stack).get("/slo")
        assert "Breached" in resp.text

    def test_banner_worst_status_wins_breached_over_at_risk(self) -> None:
        # Mix: some yellow (50%), one red (5%) — banner should say Breached.
        slis_mixed = []
        for i, s in enumerate(_make_slo_report().slis):
            if i == 0:
                slis_mixed.append(dataclasses.replace(s, passing=False, error_budget_remaining_pct=5.0))
            else:
                slis_mixed.append(dataclasses.replace(s, passing=False, error_budget_remaining_pct=50.0))
        report = dataclasses.replace(_make_slo_report(), slis=slis_mixed, all_passing=False)
        stack = _make_monitoring_stack(report=report)
        resp = _client(stack).get("/slo")
        assert "Breached" in resp.text
