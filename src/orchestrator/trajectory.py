"""Trajectory tracking: structured action→observation→reward records per agent.

Inspired by SONA trajectory concepts but implemented natively with real
persistence (JSONL) and cross-run learning capabilities. Trajectories
record what each agent did, what happened, and how well it worked —
enabling adaptive routing and self-improving pipelines over time.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class TrajectoryStep:
    """A single action→observation→reward record within a trajectory."""

    action: str
    observation: str = ""
    reward: float = 0.0  # -1.0 (failure) to 1.0 (perfect)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "observation": self.observation,
            "reward": self.reward,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }


@dataclass
class Trajectory:
    """Full trajectory for one agent invocation within a pipeline run."""

    trajectory_id: str
    run_id: str
    agent_name: str
    agent_role: str
    model_tier: str
    step_name: str  # workflow step (e.g., "Architecture", "Implementation")
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    ended_at: str | None = None
    verdict: str = "pending"  # success | failure | partial | pending
    steps: list[TrajectoryStep] = field(default_factory=list)
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    retry_count: int = 0
    escalated: bool = False

    def add_step(
        self,
        action: str,
        observation: str = "",
        reward: float = 0.0,
        **metadata: Any,
    ) -> TrajectoryStep:
        step = TrajectoryStep(
            action=action,
            observation=observation,
            reward=reward,
            metadata=metadata,
        )
        self.steps.append(step)
        return step

    def complete(
        self,
        verdict: str,
        cost_usd: float = 0.0,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> None:
        self.ended_at = datetime.now(timezone.utc).isoformat()
        self.verdict = verdict
        self.cost_usd = cost_usd
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens

    @property
    def duration_s(self) -> float:
        if not self.ended_at:
            return 0.0
        start = datetime.fromisoformat(self.started_at)
        end = datetime.fromisoformat(self.ended_at)
        return (end - start).total_seconds()

    @property
    def avg_reward(self) -> float:
        if not self.steps:
            return 0.0
        return sum(s.reward for s in self.steps) / len(self.steps)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trajectory_id": self.trajectory_id,
            "run_id": self.run_id,
            "agent_name": self.agent_name,
            "agent_role": self.agent_role,
            "model_tier": self.model_tier,
            "step_name": self.step_name,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "verdict": self.verdict,
            "steps": [s.to_dict() for s in self.steps],
            "cost_usd": self.cost_usd,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "retry_count": self.retry_count,
            "escalated": self.escalated,
            "duration_s": self.duration_s,
            "avg_reward": self.avg_reward,
        }


class TrajectoryStore:
    """Thread-safe JSONL-backed trajectory storage with in-memory index.

    Stores trajectories in two tiers:
    - Per-run: workspace/runs/<run_id>/trajectories.jsonl
    - Global:  ~/.orchestrator/trajectories/index.jsonl (cross-run learning)
    """

    def __init__(
        self,
        run_dir: Path,
        global_dir: Path | None = None,
    ) -> None:
        self._run_dir = run_dir
        self._run_dir.mkdir(parents=True, exist_ok=True)
        self._run_file = self._run_dir / "trajectories.jsonl"

        self._global_dir = global_dir or Path.home() / ".orchestrator" / "trajectories"
        self._global_dir.mkdir(parents=True, exist_ok=True)
        self._global_file = self._global_dir / "index.jsonl"

        self._active: dict[str, Trajectory] = {}
        self._lock = threading.Lock()

    def begin(
        self,
        run_id: str,
        agent_name: str,
        agent_role: str,
        model_tier: str,
        step_name: str,
    ) -> Trajectory:
        trajectory_id = f"traj-{int(time.time() * 1000)}-{agent_name}"
        traj = Trajectory(
            trajectory_id=trajectory_id,
            run_id=run_id,
            agent_name=agent_name,
            agent_role=agent_role,
            model_tier=model_tier,
            step_name=step_name,
        )
        with self._lock:
            self._active[trajectory_id] = traj
        return traj

    def end(self, trajectory: Trajectory) -> None:
        with self._lock:
            self._active.pop(trajectory.trajectory_id, None)
        self._persist(trajectory)

    def _persist(self, trajectory: Trajectory) -> None:
        record = json.dumps(trajectory.to_dict(), default=str)
        # Write to both per-run and global stores
        with self._lock:
            with self._run_file.open("a") as f:
                f.write(record + "\n")
            with self._global_file.open("a") as f:
                f.write(record + "\n")

    def get_active(self) -> list[Trajectory]:
        with self._lock:
            return list(self._active.values())

    def load_run_trajectories(self) -> list[dict[str, Any]]:
        if not self._run_file.exists():
            return []
        results = []
        for line in self._run_file.read_text().strip().splitlines():
            if line.strip():
                try:
                    results.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return results

    def query_global(
        self,
        agent_role: str | None = None,
        model_tier: str | None = None,
        verdict: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Query global trajectory index for cross-run learning."""
        if not self._global_file.exists():
            return []
        results = []
        for line in reversed(self._global_file.read_text().strip().splitlines()):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if agent_role and entry.get("agent_role") != agent_role:
                continue
            if model_tier and entry.get("model_tier") != model_tier:
                continue
            if verdict and entry.get("verdict") != verdict:
                continue
            results.append(entry)
            if len(results) >= limit:
                break
        return results

    def compute_success_rate(
        self,
        agent_role: str,
        model_tier: str | None = None,
        lookback: int = 20,
    ) -> float | None:
        """Compute success rate for an agent role from global trajectory history.

        Returns None if insufficient data (< 3 trajectories).
        """
        entries = self.query_global(agent_role=agent_role, model_tier=model_tier, limit=lookback)
        if len(entries) < 3:
            return None
        successes = sum(1 for e in entries if e.get("verdict") == "success")
        return successes / len(entries)

    def compute_avg_cost(
        self,
        agent_role: str,
        model_tier: str | None = None,
        lookback: int = 20,
    ) -> float | None:
        """Compute average cost for an agent role from global trajectory history."""
        entries = self.query_global(agent_role=agent_role, model_tier=model_tier, limit=lookback)
        if len(entries) < 3:
            return None
        costs = [e.get("cost_usd", 0.0) for e in entries]
        return sum(costs) / len(costs)

    def get_run_summary(self) -> dict[str, Any]:
        """Summarize trajectories for the current run."""
        trajectories = self.load_run_trajectories()
        if not trajectories:
            return {"total": 0}
        total = len(trajectories)
        successes = sum(1 for t in trajectories if t.get("verdict") == "success")
        total_cost = sum(t.get("cost_usd", 0.0) for t in trajectories)
        total_steps = sum(len(t.get("steps", [])) for t in trajectories)
        avg_reward = 0.0
        rewards = [t.get("avg_reward", 0.0) for t in trajectories if t.get("steps")]
        if rewards:
            avg_reward = sum(rewards) / len(rewards)
        return {
            "total": total,
            "successes": successes,
            "failures": total - successes,
            "success_rate": successes / total if total else 0.0,
            "total_cost_usd": round(total_cost, 4),
            "total_steps": total_steps,
            "avg_reward": round(avg_reward, 3),
        }
