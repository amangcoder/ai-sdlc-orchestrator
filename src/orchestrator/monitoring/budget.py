"""Budget burn-rate forecasting."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


@dataclass
class BudgetForecast:
    spent_usd: float
    remaining_usd: float
    burn_rate_usd_per_minute: float
    projected_total_usd: float
    projected_exhaustion_step: str | None
    confidence: str  # "low", "medium", "high"


@dataclass
class CostDataPoint:
    timestamp: float
    cost_usd: float
    step_name: str


class BudgetForecaster:
    """Tracks cost accumulation and predicts budget exhaustion."""

    def __init__(self, max_budget_usd: float, total_steps: int = 0) -> None:
        self.max_budget_usd = max_budget_usd
        self.total_steps = total_steps
        self._lock = threading.Lock()
        self._data_points: list[CostDataPoint] = []
        self._start_time = time.monotonic()
        self._steps_completed = 0
        self._step_costs: list[tuple[str, float]] = []  # (step_name, cost)

    def record_cost(self, step_name: str, cost_usd: float) -> None:
        with self._lock:
            self._data_points.append(CostDataPoint(
                timestamp=time.monotonic(),
                cost_usd=cost_usd,
                step_name=step_name,
            ))
            self._step_costs.append((step_name, cost_usd))
            self._steps_completed += 1

    @property
    def spent_usd(self) -> float:
        with self._lock:
            return sum(dp.cost_usd for dp in self._data_points)

    def forecast(self, remaining_steps: int | None = None) -> BudgetForecast:
        with self._lock:
            spent = sum(dp.cost_usd for dp in self._data_points)
            steps_completed = self._steps_completed
            start_time = self._start_time

        remaining_budget = max(0.0, self.max_budget_usd - spent)

        # Burn rate
        elapsed = time.monotonic() - start_time
        burn_rate = (spent / (elapsed / 60.0)) if elapsed > 0 and spent > 0 else 0.0

        # Project total cost
        if remaining_steps is None:
            remaining_steps = max(0, self.total_steps - steps_completed)

        if steps_completed > 0 and remaining_steps > 0:
            avg_cost_per_step = spent / steps_completed
            projected_total = spent + (avg_cost_per_step * remaining_steps)
        else:
            projected_total = spent

        # Find projected exhaustion step
        exhaustion_step = None
        if steps_completed > 0 and projected_total > self.max_budget_usd:
            avg_cost = spent / steps_completed
            if avg_cost > 0:
                steps_until_exhaustion = int(remaining_budget / avg_cost)
                exhaustion_step = f"~{steps_completed + steps_until_exhaustion} of {self.total_steps}"

        # Confidence
        if steps_completed >= 3:
            confidence = "high"
        elif steps_completed >= 1:
            confidence = "medium"
        else:
            confidence = "low"

        return BudgetForecast(
            spent_usd=spent,
            remaining_usd=remaining_budget,
            burn_rate_usd_per_minute=round(burn_rate, 4),
            projected_total_usd=round(projected_total, 4),
            projected_exhaustion_step=exhaustion_step,
            confidence=confidence,
        )

    def should_warn(self) -> bool:
        forecast = self.forecast()
        return forecast.projected_total_usd > self.max_budget_usd * 0.9
