"""Configuration loading for the orchestrator."""

from __future__ import annotations

from pathlib import Path

import yaml

from orchestrator.models import (
    AgentConfig,
    ModelTier,
    OrchestratorConfig,
    PhaseConfig,
)

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "default.yaml"


def load_config(config_path: Path | None = None) -> OrchestratorConfig:
    """Load orchestrator configuration from a YAML file."""
    path = config_path or DEFAULT_CONFIG_PATH
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(path) as f:
        raw = yaml.safe_load(f)

    phases = {}
    for name, phase_data in raw.get("phases", {}).items():
        phases[name] = PhaseConfig(**phase_data)

    agents = {}
    for name, agent_data in raw.get("agents", {}).items():
        if agent_data.get("model"):
            agent_data["model"] = ModelTier(agent_data["model"])
        if agent_data.get("escalation_model"):
            agent_data["escalation_model"] = ModelTier(agent_data["escalation_model"])
        agents[name] = AgentConfig(**agent_data)

    return OrchestratorConfig(
        workspace_dir=raw.get("workspace_dir", "workspace"),
        max_review_cycles=raw.get("max_review_cycles", 3),
        max_budget_usd=raw.get("max_budget_usd", 50.0),
        phases=phases,
        agents=agents,
    )
