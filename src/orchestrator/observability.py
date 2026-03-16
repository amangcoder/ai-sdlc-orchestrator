"""Observability: JSONL run logging with structlog."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

import structlog


class RunLogger:
    def __init__(self, log_dir: Path, run_id: str) -> None:
        self.run_id = run_id
        log_dir.mkdir(parents=True, exist_ok=True)
        self._log_path = log_dir / f"run-{run_id}.jsonl"
        self._log_path.touch()
        self._cumulative_cost: float = 0.0
        self._lock = threading.Lock()
        self._log = structlog.get_logger(__name__)

    def log_event(self, event_type: str, data: dict) -> None:
        if event_type == "agent_result":
            self._cumulative_cost += data.get("cost_usd", 0.0)
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "run_id": self.run_id,
            "event": event_type,
            **data,
        }
        with self._lock:
            with self._log_path.open("a") as f:
                f.write(json.dumps(record, default=str) + "\n")
        self._log.info(event_type, **data)

    def check_budget(self, max_budget_usd: float) -> str | None:
        if max_budget_usd <= 0:
            return None
        ratio = self._cumulative_cost / max_budget_usd
        if ratio >= 1.0:
            return "exceeded"
        if ratio >= 0.8:
            return "warning"
        return None

    @property
    def cumulative_cost_usd(self) -> float:
        return self._cumulative_cost
