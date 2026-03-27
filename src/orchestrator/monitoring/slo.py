"""SLO (Service Level Objective) tracker with dual-mode SLI evaluation.

Tracks six service level indicators for the orchestrator pipeline:
  1. pipeline_success_rate   — fraction of pipeline runs that succeed
  2. phase_duration_p95      — 95th-percentile phase duration in seconds
  3. cost_per_run_p50        — median (p50) cost per run in USD
  4. artifact_validation_rate — fraction of artifact writes that pass validation
  5. error_rate              — average number of errors per run
  6. recovery_success_rate   — fraction of failed runs that are successfully recovered

Evaluation is dual-mode:
  - Primary: query Prometheus HTTP API (5-second timeout, no redirects)
  - Fallback: in-memory counters accumulated via record_* methods
"""

from __future__ import annotations

import logging
import statistics
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from orchestrator.monitoring.config import SLOConfig

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional httpx dependency — fall back to urllib.request if not installed
# ---------------------------------------------------------------------------

try:
    import httpx
    HAS_HTTPX = True
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore[assignment]
    HAS_HTTPX = False


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class SLIResult:
    """Evaluation result for a single Service Level Indicator."""

    name: str
    target: float
    actual: float
    passing: bool
    error_budget_remaining_pct: float


@dataclass
class SLOReport:
    """Full SLO evaluation report covering all six SLIs."""

    evaluated_at: str          # ISO-8601 UTC timestamp
    evaluation_window_hours: int
    slis: list[SLIResult]
    all_passing: bool


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _percentile(data: list[float], pct: float) -> float:
    """Return the *pct*-th percentile of *data* (0–100 scale).

    Uses linear interpolation (the same method as NumPy's ``percentile``).
    Falls back to 0.0 for empty data and returns the single element for
    length-1 sequences.
    """
    if not data:
        return 0.0
    if len(data) == 1:
        return data[0]
    sorted_data = sorted(data)
    n = len(sorted_data)
    # k is the fractional index into the sorted array
    k = (n - 1) * pct / 100.0
    lo = int(k)
    hi = lo + 1
    if hi >= n:
        return sorted_data[lo]
    frac = k - lo
    return sorted_data[lo] + frac * (sorted_data[hi] - sorted_data[lo])


def _error_budget_higher_is_better(actual: float, target: float) -> float:
    """Compute error-budget fraction for an SLI where higher actual = better.

    Formula: ``max(0, (actual - target) / (1 - target))`` clamped to [0, 1].

    Example — actual=0.97, target=0.95:
        (0.97 - 0.95) / (1 - 0.95) = 0.02 / 0.05 = 0.40  →  40 %
    """
    if target >= 1.0:
        # Target is 100 % — any shortfall exhausts the budget entirely.
        return 0.0 if actual < target else 100.0
    budget = (actual - target) / (1.0 - target)
    return max(0.0, min(1.0, budget))


def _error_budget_lower_is_better(actual: float, target: float) -> float:
    """Compute error-budget fraction for an SLI where lower actual = better.

    Formula: ``max(0, (target - actual) / target)`` clamped to [0, 1].

    Example — actual=0.03, target=0.05:
        (0.05 - 0.03) / 0.05 = 0.02 / 0.05 = 0.40  →  40 %
    """
    if target <= 0.0:
        return 0.0 if actual > 0 else 1.0
    budget = (target - actual) / target
    return max(0.0, min(1.0, budget))


# ---------------------------------------------------------------------------
# SLOTracker
# ---------------------------------------------------------------------------

class SLOTracker:
    """Tracks six service level indicators and evaluates SLOs.

    Dual-mode evaluation:
    1. If *prometheus_url* is set, query Prometheus's HTTP API over the
       evaluation window (5-second timeout, ``follow_redirects=False``).
    2. On any Prometheus error (network, timeout, bad response), or when
       *prometheus_url* is ``None``, fall back to in-memory counters.

    Thread-safe: all internal state is protected by a single lock.

    Usage::

        tracker = SLOTracker(SLOConfig(), prometheus_url="http://localhost:9090")
        tracker.record_run(success=True, cost_usd=0.12, duration_s=45.3, errors=0)
        tracker.record_phase_result("pm", duration_s=8.2, success=True)
        report = tracker.evaluate_slos()
        print(report.all_passing)
    """

    def __init__(
        self,
        config: SLOConfig,
        prometheus_url: Optional[str] = None,
    ) -> None:
        self._config = config
        self._prometheus_url = prometheus_url
        self._lock = threading.Lock()

        # --- Run-level counters ---
        self._total_runs: int = 0
        self._successful_runs: int = 0
        self._run_costs: list[float] = []
        self._run_errors: list[int] = []

        # --- Phase-level durations keyed by phase name ---
        self._phase_durations: dict[str, list[float]] = {}

        # --- Artifact validation counters ---
        self._total_validations: int = 0
        self._valid_artifacts: int = 0

        # --- Recovery counters ---
        self._total_recoveries: int = 0
        self._successful_recoveries: int = 0

    # ------------------------------------------------------------------
    # Recording methods
    # ------------------------------------------------------------------

    def record_run(
        self,
        success: bool,
        cost_usd: float,
        duration_s: float,  # noqa: ARG002 — reserved for future use
        errors: int,
    ) -> None:
        """Record the outcome of a complete pipeline run.

        Args:
            success:    Whether the pipeline run succeeded.
            cost_usd:   Total cost of the run in USD.
            duration_s: Wall-clock duration of the run in seconds (reserved
                        for future SLI expansion; not used in current SLI math).
            errors:     Number of errors emitted during the run.
        """
        with self._lock:
            self._total_runs += 1
            if success:
                self._successful_runs += 1
            self._run_costs.append(max(0.0, cost_usd))
            self._run_errors.append(max(0, errors))

    def record_phase_result(
        self,
        phase_name: str,
        duration_s: float,
        success: bool,  # noqa: ARG002 — kept for API compatibility / future use
    ) -> None:
        """Record the duration of a single pipeline phase.

        The *success* flag is accepted for API symmetry and future use but is
        not factored into the current ``phase_duration_p95`` SLI math.

        Args:
            phase_name: Identifier for the phase (e.g. ``"pm"``, ``"architect"``).
            duration_s: Wall-clock duration in seconds.
            success:    Whether the phase completed successfully.
        """
        with self._lock:
            self._phase_durations.setdefault(phase_name, []).append(
                max(0.0, duration_s)
            )

    def record_artifact_validation(self, valid: bool) -> None:
        """Record whether an artifact write passed schema validation.

        Args:
            valid: ``True`` if the artifact passed validation; ``False`` otherwise.
        """
        with self._lock:
            self._total_validations += 1
            if valid:
                self._valid_artifacts += 1

    def record_recovery(self, success: bool) -> None:
        """Record the outcome of a recovery attempt for a failed run.

        Args:
            success: ``True`` if the recovery succeeded; ``False`` otherwise.
        """
        with self._lock:
            self._total_recoveries += 1
            if success:
                self._successful_recoveries += 1

    # ------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------

    def evaluate_slos(self, window_hours: Optional[int] = None) -> SLOReport:
        """Evaluate all six SLIs and return a :class:`SLOReport`.

        Attempts to query Prometheus first; falls back to in-memory counters
        on any error without raising an exception.

        Args:
            window_hours: Override the evaluation window (default: value from
                          :class:`~orchestrator.monitoring.config.SLOConfig`).

        Returns:
            A :class:`SLOReport` describing each SLI's current state.
        """
        effective_window = window_hours if window_hours is not None else self._config.evaluation_window_hours

        # Try Prometheus; ignore all errors and fall back to in-memory.
        prom_data: Optional[dict[str, float]] = None
        if self._prometheus_url:
            try:
                prom_data = self._query_prometheus(effective_window)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Prometheus query failed — falling back to in-memory SLI counters: %s",
                    exc,
                )

        # Compute SLI actuals from whichever source is available.
        if prom_data is not None:
            actuals = prom_data
        else:
            actuals = self._compute_in_memory_actuals()

        slis = self._build_sli_results(actuals)
        return SLOReport(
            evaluated_at=datetime.now(tz=timezone.utc).isoformat(),
            evaluation_window_hours=effective_window,
            slis=slis,
            all_passing=all(s.passing for s in slis),
        )

    def error_budget_remaining(self, slo_name: str) -> float:
        """Return the remaining error budget (0–100 %) for a named SLI.

        Convenience wrapper around :meth:`evaluate_slos`.  Returns 0.0 if
        *slo_name* is not found in the latest evaluation.

        Args:
            slo_name: One of the six canonical SLI names:
                ``pipeline_success_rate``, ``phase_duration_p95``,
                ``cost_per_run_p50``, ``artifact_validation_rate``,
                ``error_rate``, ``recovery_success_rate``.
        """
        report = self.evaluate_slos()
        for sli in report.slis:
            if sli.name == slo_name:
                return sli.error_budget_remaining_pct
        return 0.0

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _compute_in_memory_actuals(self) -> dict[str, float]:
        """Compute SLI actual values from in-memory counters (thread-safe snapshot)."""
        with self._lock:
            total_runs = self._total_runs
            successful_runs = self._successful_runs
            run_costs = list(self._run_costs)
            run_errors = list(self._run_errors)
            all_durations: list[float] = []
            for durations in self._phase_durations.values():
                all_durations.extend(durations)
            total_validations = self._total_validations
            valid_artifacts = self._valid_artifacts
            total_recoveries = self._total_recoveries
            successful_recoveries = self._successful_recoveries

        # 1. pipeline_success_rate
        pipeline_success_rate = (
            successful_runs / total_runs if total_runs > 0 else 0.0
        )

        # 2. phase_duration_p95 (seconds) — p95 across all phases
        phase_duration_p95 = _percentile(all_durations, 95.0)

        # 3. cost_per_run_p50 (USD) — median via statistics.quantiles
        if len(run_costs) >= 2:
            cost_p50 = statistics.quantiles(run_costs, n=2)[0]
        elif len(run_costs) == 1:
            cost_p50 = run_costs[0]
        else:
            cost_p50 = 0.0

        # 4. artifact_validation_rate
        artifact_validation_rate = (
            valid_artifacts / total_validations if total_validations > 0 else 1.0
        )

        # 5. error_rate — average errors per run
        error_rate = (
            sum(run_errors) / total_runs if total_runs > 0 else 0.0
        )

        # 6. recovery_success_rate
        recovery_success_rate = (
            successful_recoveries / total_recoveries
            if total_recoveries > 0
            else 1.0
        )

        return {
            "pipeline_success_rate": pipeline_success_rate,
            "phase_duration_p95": phase_duration_p95,
            "cost_per_run_p50": cost_p50,
            "artifact_validation_rate": artifact_validation_rate,
            "error_rate": error_rate,
            "recovery_success_rate": recovery_success_rate,
        }

    def _build_sli_results(self, actuals: dict[str, float]) -> list[SLIResult]:
        """Construct :class:`SLIResult` objects from *actuals* and config targets."""
        cfg = self._config
        results: list[SLIResult] = []

        # --- Helpers to reduce repetition ---
        def _add_higher_better(name: str, target: float, actual: float) -> None:
            passing = actual >= target
            budget_pct = _error_budget_higher_is_better(actual, target) * 100.0
            results.append(
                SLIResult(
                    name=name,
                    target=target,
                    actual=actual,
                    passing=passing,
                    error_budget_remaining_pct=round(budget_pct, 4),
                )
            )

        def _add_lower_better(name: str, target: float, actual: float) -> None:
            passing = actual <= target
            budget_pct = _error_budget_lower_is_better(actual, target) * 100.0
            results.append(
                SLIResult(
                    name=name,
                    target=target,
                    actual=actual,
                    passing=passing,
                    error_budget_remaining_pct=round(budget_pct, 4),
                )
            )

        _add_higher_better(
            "pipeline_success_rate",
            cfg.pipeline_success_rate,
            actuals.get("pipeline_success_rate", 0.0),
        )
        _add_lower_better(
            "phase_duration_p95",
            cfg.phase_duration_p95_seconds,
            actuals.get("phase_duration_p95", 0.0),
        )
        _add_lower_better(
            "cost_per_run_p50",
            cfg.cost_per_run_p50_usd,
            actuals.get("cost_per_run_p50", 0.0),
        )
        _add_higher_better(
            "artifact_validation_rate",
            cfg.artifact_validation_rate,
            actuals.get("artifact_validation_rate", 1.0),
        )
        _add_lower_better(
            "error_rate",
            float(cfg.max_errors_per_run),
            actuals.get("error_rate", 0.0),
        )
        _add_higher_better(
            "recovery_success_rate",
            cfg.recovery_success_rate,
            actuals.get("recovery_success_rate", 1.0),
        )

        return results

    # ------------------------------------------------------------------
    # Prometheus HTTP query
    # ------------------------------------------------------------------

    def _query_prometheus(self, window_hours: int) -> dict[str, float]:
        """Query Prometheus HTTP API and return a dict of SLI actuals.

        Raises any exception on failure so the caller can fall back to
        in-memory counters.

        Timeout is 5 seconds; redirects are not followed.
        """
        window_s = window_hours * 3600
        base = self._prometheus_url.rstrip("/")

        # PromQL expressions for each SLI
        queries: dict[str, str] = {
            "pipeline_success_rate": (
                f"sum(increase(orchestrator_run_total{{status='success'}}[{window_s}s])) / "
                f"sum(increase(orchestrator_run_total[{window_s}s]))"
            ),
            "phase_duration_p95": (
                f"histogram_quantile(0.95, rate(orchestrator_phase_duration_seconds_bucket[{window_s}s]))"
            ),
            "cost_per_run_p50": (
                f"histogram_quantile(0.50, rate(orchestrator_run_cost_usd_bucket[{window_s}s]))"
            ),
            "artifact_validation_rate": (
                f"sum(increase(orchestrator_artifact_validations_total{{status='valid'}}[{window_s}s])) / "
                f"sum(increase(orchestrator_artifact_validations_total[{window_s}s]))"
            ),
            "error_rate": (
                f"avg(rate(orchestrator_errors_total[{window_s}s]))"
            ),
            "recovery_success_rate": (
                f"sum(increase(orchestrator_recoveries_total{{status='success'}}[{window_s}s])) / "
                f"sum(increase(orchestrator_recoveries_total[{window_s}s]))"
            ),
        }

        actuals: dict[str, float] = {}
        for sli_name, expr in queries.items():
            try:
                value = self._prometheus_instant_query(base, expr)
                if value is not None:
                    actuals[sli_name] = value
            except Exception as exc:  # noqa: BLE001
                logger.debug("Prometheus SLI query %r failed: %s", sli_name, exc)
                raise  # propagate so caller falls back to in-memory

        # Fall back any missing SLIs from in-memory
        in_memory = self._compute_in_memory_actuals()
        for key, val in in_memory.items():
            actuals.setdefault(key, val)

        return actuals

    def _prometheus_instant_query(
        self, base_url: str, query: str
    ) -> Optional[float]:
        """Execute a single Prometheus instant-query and return the scalar value.

        Uses httpx when available; falls back to urllib.request otherwise.
        Both paths enforce a 5-second timeout and do not follow redirects.

        Returns:
            The numeric scalar result, or ``None`` if the result vector is empty.

        Raises:
            Exception: propagated on network/timeout/parse errors.
        """
        url = f"{base_url}/api/v1/query"
        params = {"query": query}

        if HAS_HTTPX:
            response = httpx.get(
                url,
                params=params,
                timeout=5.0,
                follow_redirects=False,
            )
            response.raise_for_status()
            data: Any = response.json()
        else:
            import urllib.parse
            import urllib.request
            import json as _json

            full_url = f"{url}?{urllib.parse.urlencode(params)}"
            req = urllib.request.Request(full_url)
            with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310
                data = _json.loads(resp.read().decode())

        # Prometheus /api/v1/query response shape:
        # {"status": "success", "data": {"resultType": "vector", "result": [...]}}
        if data.get("status") != "success":
            raise ValueError(f"Prometheus returned non-success status: {data.get('status')!r}")

        result = data.get("data", {}).get("result", [])
        if not result:
            return None

        # result[0]["value"] = [timestamp, "scalar_string"]
        raw_value = result[0].get("value", [None, None])[1]
        if raw_value is None:
            return None
        try:
            parsed = float(raw_value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Cannot parse Prometheus scalar {raw_value!r}: {exc}") from exc

        return parsed
