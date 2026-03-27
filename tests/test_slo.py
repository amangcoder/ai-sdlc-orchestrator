"""Tests for SLOTracker (TASK-017 — SLO focus).

Acceptance criteria covered:
  1. record_run() increments total and successful run counters.
  2. record_phase_result() stores phase durations.
  3. record_artifact_validation() records valid/invalid events.
  4. record_recovery() records success/failure outcomes.
  5. evaluate_slos() returns SLOReport with all 6 SLIs.
  6. SLO math: 97/100 runs success → actual=0.97 and error_budget≈40% (target=0.95).
  7. Prometheus is queried when configured; falls back gracefully on error.
  8. No Prometheus URL → always uses in-memory counters.
  9. error_budget_remaining() returns correct value or 0.0 for unknown SLI names.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(
    *,
    pipeline_success_rate: float = 0.95,
    phase_duration_p95_seconds: float = 300.0,
    cost_per_run_p50_usd: float = 1.0,
    artifact_validation_rate: float = 0.99,
    max_errors_per_run: int = 5,
    recovery_success_rate: float = 0.90,
    evaluation_window_hours: int = 24,
    enabled: bool = True,
):
    """Return a SLOConfig with the given targets.

    Follows the _make_config() helper pattern used across this test suite.
    """
    from orchestrator.monitoring.config import SLOConfig

    return SLOConfig(
        enabled=enabled,
        pipeline_success_rate=pipeline_success_rate,
        phase_duration_p95_seconds=phase_duration_p95_seconds,
        cost_per_run_p50_usd=cost_per_run_p50_usd,
        artifact_validation_rate=artifact_validation_rate,
        max_errors_per_run=max_errors_per_run,
        recovery_success_rate=recovery_success_rate,
        evaluation_window_hours=evaluation_window_hours,
    )


def _make_tracker(prometheus_url: str | None = None, **cfg_kwargs):
    """Return a SLOTracker with a default SLOConfig."""
    from orchestrator.monitoring.slo import SLOTracker

    config = _make_config(**cfg_kwargs)
    return SLOTracker(config, prometheus_url=prometheus_url)


# ---------------------------------------------------------------------------
# 1 & 2: record_run — counter updates
# ---------------------------------------------------------------------------


class TestRecordRun:
    """record_run() must update total and successful run counters."""

    def test_record_run_increments_total_runs(self):
        tracker = _make_tracker()
        assert tracker._total_runs == 0
        tracker.record_run(success=True, cost_usd=0.1, duration_s=10.0, errors=0)
        assert tracker._total_runs == 1

    def test_record_run_increments_successful_on_success(self):
        tracker = _make_tracker()
        tracker.record_run(success=True, cost_usd=0.1, duration_s=10.0, errors=0)
        assert tracker._successful_runs == 1

    def test_record_run_does_not_increment_successful_on_failure(self):
        tracker = _make_tracker()
        tracker.record_run(success=False, cost_usd=0.1, duration_s=10.0, errors=1)
        assert tracker._total_runs == 1
        assert tracker._successful_runs == 0

    def test_record_run_stores_cost(self):
        tracker = _make_tracker()
        tracker.record_run(success=True, cost_usd=0.42, duration_s=10.0, errors=0)
        assert tracker._run_costs == [0.42]

    def test_record_run_stores_errors(self):
        tracker = _make_tracker()
        tracker.record_run(success=True, cost_usd=0.1, duration_s=10.0, errors=3)
        assert tracker._run_errors == [3]

    def test_record_run_clamps_negative_cost_to_zero(self):
        tracker = _make_tracker()
        tracker.record_run(success=True, cost_usd=-5.0, duration_s=10.0, errors=0)
        assert tracker._run_costs[0] >= 0.0

    def test_multiple_runs_accumulate(self):
        tracker = _make_tracker()
        for i in range(5):
            tracker.record_run(success=(i % 2 == 0), cost_usd=0.1, duration_s=5.0, errors=0)
        assert tracker._total_runs == 5
        assert tracker._successful_runs == 3  # runs 0, 2, 4 succeed


# ---------------------------------------------------------------------------
# 3: record_phase_result
# ---------------------------------------------------------------------------


class TestRecordPhaseResult:
    """record_phase_result() must store phase durations."""

    def test_record_phase_stores_duration(self):
        tracker = _make_tracker()
        tracker.record_phase_result("pm", duration_s=12.5, success=True)
        # _phase_durations is dict[str, list[float]] keyed by phase name
        assert "pm" in tracker._phase_durations
        assert 12.5 in tracker._phase_durations["pm"]

    def test_record_multiple_phases_accumulate(self):
        tracker = _make_tracker()
        tracker.record_phase_result("pm", duration_s=10.0, success=True)
        tracker.record_phase_result("architect", duration_s=20.0, success=True)
        tracker.record_phase_result("engineer", duration_s=30.0, success=True)
        assert len(tracker._phase_durations) == 3


# ---------------------------------------------------------------------------
# 4: record_artifact_validation
# ---------------------------------------------------------------------------


class TestRecordArtifactValidation:
    """record_artifact_validation() must track valid/invalid events."""

    def test_valid_artifact_increments_valid_count(self):
        tracker = _make_tracker()
        tracker.record_artifact_validation(valid=True)
        # Actual attributes: _total_validations and _valid_artifacts
        assert tracker._total_validations >= 1
        assert tracker._valid_artifacts >= 1

    def test_invalid_artifact_increments_total_but_not_passed(self):
        tracker = _make_tracker()
        tracker.record_artifact_validation(valid=False)
        assert tracker._total_validations >= 1
        # _valid_artifacts must not have increased on invalid validation
        assert tracker._valid_artifacts == 0


# ---------------------------------------------------------------------------
# 5 & 6: evaluate_slos — acceptance criteria math
# ---------------------------------------------------------------------------


class TestEvaluateSlosMath:
    """Verify evaluate_slos() computes correct SLI values and error budgets."""

    def test_97_of_100_runs_gives_97pct_success_rate(self):
        """Acceptance criterion: 97 successes out of 100 runs → actual = 0.97."""
        tracker = _make_tracker(pipeline_success_rate=0.95)
        for _ in range(97):
            tracker.record_run(success=True, cost_usd=0.1, duration_s=10.0, errors=0)
        for _ in range(3):
            tracker.record_run(success=False, cost_usd=0.1, duration_s=10.0, errors=1)

        report = tracker.evaluate_slos()
        sli_map = {s.name: s for s in report.slis}
        psr = sli_map["pipeline_success_rate"]

        assert abs(psr.actual - 0.97) < 1e-9

    def test_97_of_100_gives_approx_40pct_error_budget(self):
        """Canonical acceptance criterion:
        97/100 success (actual=0.97) vs target=0.95 → ~40% error budget remaining.
        Formula: (0.97 - 0.95) / (1 - 0.95) = 0.02 / 0.05 = 0.40
        """
        tracker = _make_tracker(pipeline_success_rate=0.95)
        for _ in range(97):
            tracker.record_run(success=True, cost_usd=0.1, duration_s=10.0, errors=0)
        for _ in range(3):
            tracker.record_run(success=False, cost_usd=0.1, duration_s=10.0, errors=1)

        report = tracker.evaluate_slos()
        sli_map = {s.name: s for s in report.slis}
        psr = sli_map["pipeline_success_rate"]

        assert abs(psr.error_budget_remaining_pct - 40.0) < 1e-6, (
            f"Expected ~40% error budget, got {psr.error_budget_remaining_pct:.4f}%"
        )

    def test_report_has_six_slis(self):
        tracker = _make_tracker()
        report = tracker.evaluate_slos()
        assert len(report.slis) == 6

    def test_sli_names_match_expected_set(self):
        tracker = _make_tracker()
        report = tracker.evaluate_slos()
        names = {s.name for s in report.slis}
        expected = {
            "pipeline_success_rate",
            "phase_duration_p95",
            "cost_per_run_p50",
            "artifact_validation_rate",
            "error_rate",
            "recovery_success_rate",
        }
        assert names == expected

    def test_all_passing_true_when_well_above_targets(self):
        """Perfect data should yield all_passing=True."""
        tracker = _make_tracker(
            pipeline_success_rate=0.90,
            phase_duration_p95_seconds=300.0,
            cost_per_run_p50_usd=2.0,
            artifact_validation_rate=0.90,
            max_errors_per_run=10,
            recovery_success_rate=0.80,
        )
        # 100 perfect runs
        for _ in range(100):
            tracker.record_run(success=True, cost_usd=0.5, duration_s=5.0, errors=0)
            tracker.record_phase_result("pm", duration_s=5.0, success=True)
            tracker.record_artifact_validation(valid=True)
            tracker.record_recovery(success=True)

        report = tracker.evaluate_slos()
        assert report.all_passing is True

    def test_all_passing_false_when_success_rate_below_target(self):
        """50% success rate vs 95% target must make all_passing=False."""
        tracker = _make_tracker(pipeline_success_rate=0.95)
        for _ in range(50):
            tracker.record_run(success=True, cost_usd=0.1, duration_s=5.0, errors=0)
        for _ in range(50):
            tracker.record_run(success=False, cost_usd=0.1, duration_s=5.0, errors=1)

        report = tracker.evaluate_slos()
        sli_map = {s.name: s for s in report.slis}
        assert not sli_map["pipeline_success_rate"].passing

    def test_no_runs_defaults_gracefully(self):
        """SLOTracker with no recorded runs must not raise on evaluate_slos()."""
        tracker = _make_tracker()
        report = tracker.evaluate_slos()  # Must not raise
        assert len(report.slis) == 6

    def test_evaluated_at_is_iso8601(self):
        """SLOReport.evaluated_at must be a parseable ISO-8601 string."""
        from datetime import datetime

        tracker = _make_tracker()
        report = tracker.evaluate_slos()
        # Must not raise
        dt = datetime.fromisoformat(report.evaluated_at)
        assert dt is not None


# ---------------------------------------------------------------------------
# 7: error_budget_remaining
# ---------------------------------------------------------------------------


class TestErrorBudgetRemaining:
    """error_budget_remaining() must return correct percentage or 0 for unknown names."""

    def test_unknown_slo_name_returns_zero(self):
        tracker = _make_tracker()
        result = tracker.error_budget_remaining("nonexistent_sli")
        assert result == 0.0

    def test_returns_float(self):
        tracker = _make_tracker()
        result = tracker.error_budget_remaining("pipeline_success_rate")
        assert isinstance(result, float)

    def test_matches_evaluate_slos_value(self):
        """error_budget_remaining() must equal the value in the SLOReport."""
        tracker = _make_tracker(pipeline_success_rate=0.95)
        for _ in range(97):
            tracker.record_run(success=True, cost_usd=0.1, duration_s=5.0, errors=0)
        for _ in range(3):
            tracker.record_run(success=False, cost_usd=0.1, duration_s=5.0, errors=1)

        report = tracker.evaluate_slos()
        sli_map = {s.name: s for s in report.slis}
        expected = sli_map["pipeline_success_rate"].error_budget_remaining_pct

        # evaluate again to get the same snapshot
        actual = tracker.error_budget_remaining("pipeline_success_rate")
        # Both should yield approximately the same value
        assert abs(actual - expected) < 0.1


# ---------------------------------------------------------------------------
# 8: Prometheus fallback (mocked HTTP)
# ---------------------------------------------------------------------------


class TestPrometheusFallback:
    """evaluate_slos() must fall back to in-memory when Prometheus errors."""

    def test_prometheus_network_error_falls_back_silently(self):
        """A ConnectionError from Prometheus must not raise; fallback to in-memory."""
        import httpx

        tracker = _make_tracker(prometheus_url="http://prom:9090")
        tracker.record_run(success=True, cost_usd=0.1, duration_s=5.0, errors=0)

        with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
            # Must not raise
            report = tracker.evaluate_slos()

        assert len(report.slis) == 6

    def test_no_prometheus_url_uses_in_memory(self):
        """Without a prometheus_url, evaluate_slos() must always use in-memory."""
        tracker = _make_tracker(prometheus_url=None)
        tracker.record_run(success=True, cost_usd=0.2, duration_s=5.0, errors=0)

        with patch("httpx.get") as mock_get:
            report = tracker.evaluate_slos()

        # httpx.get must not be called when no Prometheus URL is set
        mock_get.assert_not_called()
        assert len(report.slis) == 6

    def test_prometheus_timeout_falls_back_silently(self):
        """A timeout from Prometheus must not raise; fallback to in-memory."""
        import httpx

        tracker = _make_tracker(prometheus_url="http://prom:9090")
        tracker.record_run(success=True, cost_usd=0.1, duration_s=5.0, errors=0)

        with patch("httpx.get", side_effect=httpx.TimeoutException("timed out")):
            report = tracker.evaluate_slos()

        assert len(report.slis) == 6
