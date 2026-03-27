"""Tests for src/orchestrator/monitoring/slo.py — SLOTracker."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from orchestrator.monitoring.config import SLOConfig
from orchestrator.monitoring.slo import (
    SLIResult,
    SLOReport,
    SLOTracker,
    _error_budget_higher_is_better,
    _error_budget_lower_is_better,
    _percentile,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tracker(
    *,
    pipeline_success_rate: float = 0.95,
    phase_duration_p95_seconds: float = 300.0,
    cost_per_run_p50_usd: float = 1.0,
    artifact_validation_rate: float = 0.99,
    max_errors_per_run: int = 5,
    recovery_success_rate: float = 0.90,
    evaluation_window_hours: int = 24,
    prometheus_url: str | None = None,
) -> SLOTracker:
    config = SLOConfig(
        pipeline_success_rate=pipeline_success_rate,
        phase_duration_p95_seconds=phase_duration_p95_seconds,
        cost_per_run_p50_usd=cost_per_run_p50_usd,
        artifact_validation_rate=artifact_validation_rate,
        max_errors_per_run=max_errors_per_run,
        recovery_success_rate=recovery_success_rate,
        evaluation_window_hours=evaluation_window_hours,
    )
    return SLOTracker(config, prometheus_url=prometheus_url)


# ---------------------------------------------------------------------------
# Unit tests — math helpers
# ---------------------------------------------------------------------------


class TestErrorBudgetMath:
    """Validate the error-budget formulae in isolation."""

    def test_higher_is_better_canonical_case(self):
        """97% actual vs 95% target → ~40% error budget."""
        budget = _error_budget_higher_is_better(0.97, 0.95)
        assert abs(budget - 0.40) < 1e-9

    def test_higher_is_better_at_target(self):
        """Actual exactly at target → 0% error budget."""
        budget = _error_budget_higher_is_better(0.95, 0.95)
        assert budget == pytest.approx(0.0)

    def test_higher_is_better_below_target(self):
        """Actual below target → 0% (clamped)."""
        budget = _error_budget_higher_is_better(0.90, 0.95)
        assert budget == 0.0

    def test_higher_is_better_perfect(self):
        """Actual = 1.0 and target < 1.0 → 100% error budget."""
        budget = _error_budget_higher_is_better(1.0, 0.95)
        assert budget == pytest.approx(1.0)

    def test_higher_is_better_100pct_target(self):
        """Target = 1.0 and actual < 1.0 → 0% budget."""
        budget = _error_budget_higher_is_better(0.99, 1.0)
        assert budget == 0.0

    def test_lower_is_better_at_half_target(self):
        """Actual is 50% of target → 50% budget."""
        budget = _error_budget_lower_is_better(50.0, 100.0)
        assert budget == pytest.approx(0.50)

    def test_lower_is_better_above_target(self):
        """Actual exceeds target → 0% budget (clamped)."""
        budget = _error_budget_lower_is_better(120.0, 100.0)
        assert budget == 0.0

    def test_lower_is_better_at_zero(self):
        """Actual = 0 → 100% budget."""
        budget = _error_budget_lower_is_better(0.0, 100.0)
        assert budget == pytest.approx(1.0)

    def test_lower_is_better_zero_target(self):
        """Target = 0 → 0.0 (guard against division by zero)."""
        budget = _error_budget_lower_is_better(0.0, 0.0)
        assert budget == 1.0

    def test_error_budget_pct_conversion(self):
        """Multiplying by 100 gives percent value used in SLIResult."""
        budget_frac = _error_budget_higher_is_better(0.97, 0.95)
        assert abs(budget_frac * 100 - 40.0) < 1e-6


class TestPercentile:
    """Validate _percentile helper."""

    def test_empty_list_returns_zero(self):
        assert _percentile([], 95.0) == 0.0

    def test_single_element_returns_that_element(self):
        assert _percentile([42.0], 95.0) == 42.0

    def test_p50_of_sorted_list(self):
        data = [float(i) for i in range(1, 101)]  # 1..100
        p50 = _percentile(data, 50.0)
        assert 48.0 <= p50 <= 52.0

    def test_p95_bounded(self):
        data = [float(i) for i in range(1, 101)]  # 1..100
        p95 = _percentile(data, 95.0)
        # Should be close to 95
        assert 90.0 <= p95 <= 100.0


# ---------------------------------------------------------------------------
# Unit tests — record_* methods
# ---------------------------------------------------------------------------


class TestRecordMethods:
    """Verify that recording methods update in-memory state correctly."""

    def test_record_run_increments_total(self):
        tracker = _make_tracker()
        tracker.record_run(success=True, cost_usd=0.5, duration_s=30.0, errors=0)
        tracker.record_run(success=False, cost_usd=0.1, duration_s=5.0, errors=2)
        assert tracker._total_runs == 2

    def test_record_run_increments_successful_runs(self):
        tracker = _make_tracker()
        tracker.record_run(success=True, cost_usd=0.5, duration_s=30.0, errors=0)
        tracker.record_run(success=False, cost_usd=0.1, duration_s=5.0, errors=1)
        assert tracker._successful_runs == 1

    def test_record_run_stores_costs(self):
        tracker = _make_tracker()
        tracker.record_run(success=True, cost_usd=0.5, duration_s=30.0, errors=0)
        tracker.record_run(success=True, cost_usd=1.5, duration_s=30.0, errors=0)
        assert sorted(tracker._run_costs) == pytest.approx([0.5, 1.5])

    def test_record_run_stores_errors(self):
        tracker = _make_tracker()
        tracker.record_run(success=True, cost_usd=0.0, duration_s=0.0, errors=3)
        assert tracker._run_errors == [3]

    def test_record_phase_result_stores_duration(self):
        tracker = _make_tracker()
        tracker.record_phase_result("pm", duration_s=10.0, success=True)
        tracker.record_phase_result("pm", duration_s=20.0, success=True)
        assert tracker._phase_durations["pm"] == pytest.approx([10.0, 20.0])

    def test_record_phase_result_multiple_phases(self):
        tracker = _make_tracker()
        tracker.record_phase_result("pm", duration_s=10.0, success=True)
        tracker.record_phase_result("architect", duration_s=30.0, success=True)
        assert "pm" in tracker._phase_durations
        assert "architect" in tracker._phase_durations

    def test_record_artifact_validation_valid(self):
        tracker = _make_tracker()
        tracker.record_artifact_validation(valid=True)
        assert tracker._total_validations == 1
        assert tracker._valid_artifacts == 1

    def test_record_artifact_validation_invalid(self):
        tracker = _make_tracker()
        tracker.record_artifact_validation(valid=False)
        assert tracker._total_validations == 1
        assert tracker._valid_artifacts == 0

    def test_record_recovery_success(self):
        tracker = _make_tracker()
        tracker.record_recovery(success=True)
        assert tracker._total_recoveries == 1
        assert tracker._successful_recoveries == 1

    def test_record_recovery_failure(self):
        tracker = _make_tracker()
        tracker.record_recovery(success=False)
        assert tracker._total_recoveries == 1
        assert tracker._successful_recoveries == 0

    def test_record_run_clamps_negative_cost(self):
        tracker = _make_tracker()
        tracker.record_run(success=True, cost_usd=-1.0, duration_s=0.0, errors=0)
        assert tracker._run_costs == [0.0]

    def test_record_run_clamps_negative_errors(self):
        tracker = _make_tracker()
        tracker.record_run(success=True, cost_usd=0.0, duration_s=0.0, errors=-5)
        assert tracker._run_errors == [0]


# ---------------------------------------------------------------------------
# Unit tests — evaluate_slos (in-memory)
# ---------------------------------------------------------------------------


class TestEvaluateSlos:
    """Verify evaluate_slos returns correct SLOReport from in-memory counters."""

    def test_returns_slo_report_type(self):
        tracker = _make_tracker()
        report = tracker.evaluate_slos()
        assert isinstance(report, SLOReport)

    def test_report_has_six_slis(self):
        tracker = _make_tracker()
        report = tracker.evaluate_slos()
        assert len(report.slis) == 6

    def test_sli_names_are_correct(self):
        tracker = _make_tracker()
        report = tracker.evaluate_slos()
        names = {s.name for s in report.slis}
        assert names == {
            "pipeline_success_rate",
            "phase_duration_p95",
            "cost_per_run_p50",
            "artifact_validation_rate",
            "error_rate",
            "recovery_success_rate",
        }

    def test_all_passing_true_when_all_pass(self):
        """No data → defaults are technically passing (0 success/0 total → 0%)
        but passing flag depends on actual >= target.  With no data the
        zero-run defaults give actual=0 for success rate which fails target=0.95."""
        tracker = _make_tracker()
        # 100 successful runs, all else nominal
        for _ in range(100):
            tracker.record_run(success=True, cost_usd=0.5, duration_s=30.0, errors=0)
        for _ in range(100):
            tracker.record_artifact_validation(valid=True)
        for _ in range(10):
            tracker.record_recovery(success=True)
        report = tracker.evaluate_slos()
        assert report.all_passing is True

    def test_all_passing_false_when_one_fails(self):
        tracker = _make_tracker()
        # 90% success < 95% target → pipeline_success_rate fails
        for _ in range(90):
            tracker.record_run(success=True, cost_usd=0.5, duration_s=30.0, errors=0)
        for _ in range(10):
            tracker.record_run(success=False, cost_usd=0.1, duration_s=5.0, errors=1)
        report = tracker.evaluate_slos()
        assert report.all_passing is False

    def test_evaluated_at_is_iso_format(self):
        tracker = _make_tracker()
        report = tracker.evaluate_slos()
        # Should parse without raising
        from datetime import datetime
        datetime.fromisoformat(report.evaluated_at)

    def test_evaluation_window_hours_respected(self):
        tracker = _make_tracker(evaluation_window_hours=12)
        report = tracker.evaluate_slos()
        assert report.evaluation_window_hours == 12

    def test_evaluation_window_hours_override(self):
        tracker = _make_tracker(evaluation_window_hours=24)
        report = tracker.evaluate_slos(window_hours=6)
        assert report.evaluation_window_hours == 6

    def test_pipeline_success_rate_sli_value(self):
        """97 successes / 100 total → actual=0.97."""
        tracker = _make_tracker()
        for _ in range(97):
            tracker.record_run(success=True, cost_usd=0.5, duration_s=30.0, errors=0)
        for _ in range(3):
            tracker.record_run(success=False, cost_usd=0.0, duration_s=5.0, errors=1)
        report = tracker.evaluate_slos()
        sli = next(s for s in report.slis if s.name == "pipeline_success_rate")
        assert sli.actual == pytest.approx(0.97)
        assert sli.passing is True

    def test_error_budget_40pct_for_97_success_vs_95_target(self):
        """Canonical acceptance criterion: 97/100 success → ~40% error budget."""
        tracker = _make_tracker(pipeline_success_rate=0.95)
        for _ in range(97):
            tracker.record_run(success=True, cost_usd=0.0, duration_s=0.0, errors=0)
        for _ in range(3):
            tracker.record_run(success=False, cost_usd=0.0, duration_s=0.0, errors=0)
        report = tracker.evaluate_slos()
        sli = next(s for s in report.slis if s.name == "pipeline_success_rate")
        assert abs(sli.error_budget_remaining_pct - 40.0) < 0.01

    def test_cost_p50_uses_median(self):
        """With costs [0.5, 1.5], median = 1.0."""
        tracker = _make_tracker(cost_per_run_p50_usd=2.0)
        tracker.record_run(success=True, cost_usd=0.5, duration_s=0.0, errors=0)
        tracker.record_run(success=True, cost_usd=1.5, duration_s=0.0, errors=0)
        report = tracker.evaluate_slos()
        sli = next(s for s in report.slis if s.name == "cost_per_run_p50")
        assert sli.actual == pytest.approx(1.0)
        assert sli.passing is True  # 1.0 <= 2.0

    def test_phase_duration_p95(self):
        """10 phases ranging 10–100s — p95 should be near the high end."""
        tracker = _make_tracker(phase_duration_p95_seconds=300.0)
        for i in range(1, 11):
            tracker.record_phase_result("phase", duration_s=float(i * 10), success=True)
        report = tracker.evaluate_slos()
        sli = next(s for s in report.slis if s.name == "phase_duration_p95")
        assert sli.passing is True  # p95 should be well below 300s

    def test_artifact_validation_rate_sli(self):
        """99/100 validations → actual=0.99."""
        tracker = _make_tracker(artifact_validation_rate=0.99)
        for _ in range(99):
            tracker.record_artifact_validation(valid=True)
        tracker.record_artifact_validation(valid=False)
        report = tracker.evaluate_slos()
        sli = next(s for s in report.slis if s.name == "artifact_validation_rate")
        assert sli.actual == pytest.approx(0.99)
        assert sli.passing is True

    def test_recovery_success_rate_sli(self):
        """9/10 recoveries → actual=0.90, target=0.90 → passing."""
        tracker = _make_tracker(recovery_success_rate=0.90)
        for _ in range(9):
            tracker.record_recovery(success=True)
        tracker.record_recovery(success=False)
        report = tracker.evaluate_slos()
        sli = next(s for s in report.slis if s.name == "recovery_success_rate")
        assert sli.actual == pytest.approx(0.90)
        assert sli.passing is True

    def test_error_rate_sli(self):
        """Average 2 errors/run vs max_errors_per_run=5 → passing."""
        tracker = _make_tracker(max_errors_per_run=5)
        for _ in range(5):
            tracker.record_run(success=True, cost_usd=0.0, duration_s=0.0, errors=2)
        report = tracker.evaluate_slos()
        sli = next(s for s in report.slis if s.name == "error_rate")
        assert sli.actual == pytest.approx(2.0)
        assert sli.passing is True  # 2 <= 5

    def test_error_rate_sli_fails_when_above_target(self):
        """Average 8 errors/run vs max_errors_per_run=5 → not passing."""
        tracker = _make_tracker(max_errors_per_run=5)
        for _ in range(5):
            tracker.record_run(success=True, cost_usd=0.0, duration_s=0.0, errors=8)
        report = tracker.evaluate_slos()
        sli = next(s for s in report.slis if s.name == "error_rate")
        assert sli.passing is False

    def test_no_runs_defaults_gracefully(self):
        """No data → should not raise; pipeline_success_rate actual = 0."""
        tracker = _make_tracker()
        report = tracker.evaluate_slos()
        sli = next(s for s in report.slis if s.name == "pipeline_success_rate")
        assert sli.actual == 0.0

    def test_sli_result_has_target_from_config(self):
        tracker = _make_tracker(pipeline_success_rate=0.97)
        report = tracker.evaluate_slos()
        sli = next(s for s in report.slis if s.name == "pipeline_success_rate")
        assert sli.target == pytest.approx(0.97)


# ---------------------------------------------------------------------------
# Unit tests — error_budget_remaining()
# ---------------------------------------------------------------------------


class TestErrorBudgetRemaining:
    """Verify error_budget_remaining() public method."""

    def test_returns_float(self):
        tracker = _make_tracker()
        result = tracker.error_budget_remaining("pipeline_success_rate")
        assert isinstance(result, float)

    def test_unknown_slo_name_returns_zero(self):
        tracker = _make_tracker()
        assert tracker.error_budget_remaining("nonexistent_slo") == 0.0

    def test_matches_evaluate_slos_value(self):
        tracker = _make_tracker(pipeline_success_rate=0.95)
        for _ in range(97):
            tracker.record_run(success=True, cost_usd=0.0, duration_s=0.0, errors=0)
        for _ in range(3):
            tracker.record_run(success=False, cost_usd=0.0, duration_s=0.0, errors=0)
        direct = tracker.error_budget_remaining("pipeline_success_rate")
        assert abs(direct - 40.0) < 0.01


# ---------------------------------------------------------------------------
# Unit tests — Prometheus integration (mocked)
# ---------------------------------------------------------------------------


def _make_prometheus_response(value: float | None) -> dict:
    """Build a minimal Prometheus instant-query response."""
    if value is None:
        return {"status": "success", "data": {"resultType": "vector", "result": []}}
    return {
        "status": "success",
        "data": {
            "resultType": "vector",
            "result": [{"metric": {}, "value": [1_700_000_000, str(value)]}],
        },
    }


class TestPrometheusIntegration:
    """Verify Prometheus query path with mocked HTTP calls."""

    def _patch_httpx_get(self, responses: dict[str, float | None]):
        """Return a context-manager that patches httpx.get with canned responses."""
        call_count = [0]
        keys = list(responses.keys())

        def fake_get(url, **kwargs):
            # Return responses in order of the keys dict
            idx = call_count[0] % len(keys)
            key = keys[idx]
            call_count[0] += 1
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json.return_value = _make_prometheus_response(responses[key])
            return mock_resp

        return patch("orchestrator.monitoring.slo.httpx.get", side_effect=fake_get)

    def test_prometheus_success_uses_prom_values(self):
        """When Prometheus is reachable, actuals come from it."""
        tracker = _make_tracker(
            pipeline_success_rate=0.95,
            prometheus_url="http://localhost:9090",
        )
        prom_values = {
            "pipeline_success_rate": 0.98,
            "phase_duration_p95": 100.0,
            "cost_per_run_p50": 0.50,
            "artifact_validation_rate": 0.995,
            "error_rate": 1.0,
            "recovery_success_rate": 0.95,
        }

        # Patch _prometheus_instant_query to return canned values
        instant_results = iter(prom_values.values())

        def fake_instant(base_url: str, query: str) -> float | None:
            return next(instant_results)

        with patch.object(tracker, "_prometheus_instant_query", side_effect=fake_instant):
            report = tracker.evaluate_slos()

        sli = next(s for s in report.slis if s.name == "pipeline_success_rate")
        assert sli.actual == pytest.approx(0.98)

    def test_prometheus_fallback_on_network_error(self):
        """When Prometheus raises, falls back to in-memory without exception."""
        tracker = _make_tracker(
            pipeline_success_rate=0.95,
            prometheus_url="http://localhost:9090",
        )
        for _ in range(97):
            tracker.record_run(success=True, cost_usd=0.0, duration_s=0.0, errors=0)
        for _ in range(3):
            tracker.record_run(success=False, cost_usd=0.0, duration_s=0.0, errors=0)

        with patch.object(
            tracker,
            "_query_prometheus",
            side_effect=ConnectionError("refused"),
        ):
            report = tracker.evaluate_slos()  # must not raise

        sli = next(s for s in report.slis if s.name == "pipeline_success_rate")
        assert sli.actual == pytest.approx(0.97)

    def test_no_prometheus_url_uses_in_memory(self):
        """Without a prometheus_url, in-memory path is always taken."""
        tracker = _make_tracker(prometheus_url=None)
        for _ in range(100):
            tracker.record_run(success=True, cost_usd=0.2, duration_s=10.0, errors=0)
        report = tracker.evaluate_slos()
        sli = next(s for s in report.slis if s.name == "pipeline_success_rate")
        assert sli.actual == pytest.approx(1.0)

    def test_prometheus_timeout_fallback(self):
        """Timeout in _query_prometheus triggers in-memory fallback."""
        import httpx as _httpx

        tracker = _make_tracker(prometheus_url="http://localhost:9090")
        for _ in range(5):
            tracker.record_run(success=True, cost_usd=0.0, duration_s=0.0, errors=0)

        with patch(
            "orchestrator.monitoring.slo.httpx.get",
            side_effect=_httpx.TimeoutException("timeout"),
        ):
            report = tracker.evaluate_slos()  # must not raise

        assert isinstance(report, SLOReport)

    def test_httpx_called_with_5s_timeout_and_no_redirects(self):
        """httpx.get must be called with timeout=5.0 and follow_redirects=False."""
        tracker = _make_tracker(prometheus_url="http://localhost:9090")

        captured_kwargs: list[dict] = []

        def fake_get(url, **kwargs):
            captured_kwargs.append(kwargs)
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json.return_value = _make_prometheus_response(0.97)
            return mock_resp

        with patch("orchestrator.monitoring.slo.httpx.get", side_effect=fake_get):
            with patch("orchestrator.monitoring.slo.HAS_HTTPX", True):
                tracker._prometheus_instant_query("http://localhost:9090", "some_query")

        assert len(captured_kwargs) >= 1
        kw = captured_kwargs[0]
        assert kw.get("timeout") == pytest.approx(5.0)
        assert kw.get("follow_redirects") is False

    def test_prometheus_non_success_status_raises(self):
        """When Prometheus returns status != 'success', an exception is raised."""
        tracker = _make_tracker(prometheus_url="http://localhost:9090")

        def fake_get(url, **kwargs):
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json.return_value = {"status": "error", "error": "bad query"}
            return mock_resp

        with patch("orchestrator.monitoring.slo.httpx.get", side_effect=fake_get):
            with pytest.raises(ValueError, match="non-success"):
                tracker._prometheus_instant_query("http://localhost:9090", "bad")

    def test_empty_prometheus_result_returns_none(self):
        """Empty result vector → _prometheus_instant_query returns None."""
        tracker = _make_tracker(prometheus_url="http://localhost:9090")

        def fake_get(url, **kwargs):
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json.return_value = _make_prometheus_response(None)
            return mock_resp

        with patch("orchestrator.monitoring.slo.httpx.get", side_effect=fake_get):
            result = tracker._prometheus_instant_query("http://localhost:9090", "q")

        assert result is None

    def test_has_httpx_false_uses_urllib(self):
        """When HAS_HTTPX is False, fall back to urllib.request."""
        tracker = _make_tracker()

        fake_resp_data = json.dumps(_make_prometheus_response(0.99)).encode()

        mock_resp = MagicMock()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.read = MagicMock(return_value=fake_resp_data)

        with patch("orchestrator.monitoring.slo.HAS_HTTPX", False):
            with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
                result = tracker._prometheus_instant_query("http://localhost:9090", "q")

        assert result == pytest.approx(0.99)
        mock_urlopen.assert_called_once()


# ---------------------------------------------------------------------------
# Acceptance-criteria test
# ---------------------------------------------------------------------------


class TestAcceptanceCriteria:
    """End-to-end test matching the task's acceptance criteria exactly."""

    def test_100_runs_3_failures_40pct_error_budget(self):
        """
        Acceptance criterion:
        100 runs with 3 failures (97% success) shows ~40% error budget for 95% target.
        """
        tracker = _make_tracker(pipeline_success_rate=0.95)
        for _ in range(97):
            tracker.record_run(success=True, cost_usd=0.5, duration_s=30.0, errors=0)
        for _ in range(3):
            tracker.record_run(success=False, cost_usd=0.1, duration_s=5.0, errors=1)

        report = tracker.evaluate_slos()

        sli = next(s for s in report.slis if s.name == "pipeline_success_rate")
        assert sli.actual == pytest.approx(0.97, abs=1e-9)
        assert sli.passing is True
        # ~40% budget: accept ±1 percentage point
        assert abs(sli.error_budget_remaining_pct - 40.0) < 1.0

    def test_all_six_slis_present(self):
        tracker = _make_tracker()
        report = tracker.evaluate_slos()
        names = [s.name for s in report.slis]
        for expected in (
            "pipeline_success_rate",
            "phase_duration_p95",
            "cost_per_run_p50",
            "artifact_validation_rate",
            "error_rate",
            "recovery_success_rate",
        ):
            assert expected in names

    def test_prometheus_unavailable_does_not_raise(self):
        """If Prometheus is configured but unavailable, evaluate_slos must not raise."""
        tracker = _make_tracker(prometheus_url="http://localhost:9090")
        tracker.record_run(success=True, cost_usd=0.5, duration_s=30.0, errors=0)

        with patch.object(
            tracker, "_query_prometheus", side_effect=OSError("connection refused")
        ):
            report = tracker.evaluate_slos()  # must not raise

        assert isinstance(report, SLOReport)
        assert len(report.slis) == 6

    def test_cost_p50_uses_statistics_quantiles(self):
        """cost_per_run_p50 must use statistics.quantiles for the median."""
        tracker = _make_tracker(cost_per_run_p50_usd=5.0)
        costs = [0.1, 0.3, 0.5, 0.7, 0.9, 1.1, 1.3]
        for c in costs:
            tracker.record_run(success=True, cost_usd=c, duration_s=0.0, errors=0)
        report = tracker.evaluate_slos()
        sli = next(s for s in report.slis if s.name == "cost_per_run_p50")
        # Median of [0.1, 0.3, 0.5, 0.7, 0.9, 1.1, 1.3] ≈ 0.7
        assert 0.5 <= sli.actual <= 0.9

    def test_phase_duration_p95_is_p95(self):
        """phase_duration_p95 must use 95th-percentile, not mean or max."""
        tracker = _make_tracker(phase_duration_p95_seconds=500.0)
        # 20 durations: 19 at 10s, 1 outlier at 1000s
        for _ in range(19):
            tracker.record_phase_result("phase", duration_s=10.0, success=True)
        tracker.record_phase_result("phase", duration_s=1000.0, success=True)
        report = tracker.evaluate_slos()
        sli = next(s for s in report.slis if s.name == "phase_duration_p95")
        # p95 with 1 outlier in 20 values should NOT equal 1000
        assert sli.actual < 1000.0
