"""Health check endpoint served alongside Prometheus metrics."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler
from typing import Any


class HealthState:
    """Shared mutable state for the health check endpoint."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.run_id: str | None = None
        self.current_step: str | None = None
        self.budget_remaining_usd: float | None = None
        self._start_time = time.monotonic()

    def update(
        self,
        run_id: str | None = None,
        current_step: str | None = None,
        budget_remaining_usd: float | None = None,
    ) -> None:
        with self._lock:
            if run_id is not None:
                self.run_id = run_id
            if current_step is not None:
                self.current_step = current_step
            if budget_remaining_usd is not None:
                self.budget_remaining_usd = budget_remaining_usd

    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            return {
                "status": "running" if self.run_id else "idle",
                "run_id": self.run_id,
                "current_step": self.current_step,
                "budget_remaining_usd": self.budget_remaining_usd,
                "uptime_seconds": round(time.monotonic() - self._start_time, 1),
            }


# Global singleton — set by MonitoringStack, read by the metrics server's /healthz handler
_health_state = HealthState()


def get_health_state() -> HealthState:
    return _health_state
