"""Configuration loading for the orchestrator."""

from __future__ import annotations

from pathlib import Path

import yaml

from orchestrator.models import (
    AgentConfig,
    DebateConfig,
    KnowledgeConfig,
    ModelTier,
    OrchestratorConfig,
    PhaseConfig,
    SpawnConfig,
    WorkflowType,
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

    default_workflow = WorkflowType.FEATURE_DEVELOPMENT
    raw_wf = raw.get("default_workflow")
    if raw_wf:
        try:
            default_workflow = WorkflowType(raw_wf)
        except ValueError:
            import logging
            valid = [w.value for w in WorkflowType]
            logging.getLogger(__name__).warning(
                f"Unknown workflow '{raw_wf}', valid options: {valid}. Defaulting to FEATURE_DEVELOPMENT"
            )

    # Parse debate config
    debate_raw = raw.get("debate", {})
    debate_config = DebateConfig(
        enabled=debate_raw.get("enabled", False),
        researcher_count=debate_raw.get("researcher_count", 2),
        brainstormer_count=debate_raw.get("brainstormer_count", 2),
        max_rounds=debate_raw.get("max_rounds", 3),
        convergence_threshold=debate_raw.get("convergence_threshold", 0.8),
        researcher_model=ModelTier(debate_raw["researcher_model"]) if debate_raw.get("researcher_model") else ModelTier.SONNET,
        brainstormer_model=ModelTier(debate_raw["brainstormer_model"]) if debate_raw.get("brainstormer_model") else ModelTier.SONNET,
        mediator_model=ModelTier(debate_raw["mediator_model"]) if debate_raw.get("mediator_model") else ModelTier.OPUS,
        researcher_max_turns=debate_raw.get("researcher_max_turns", 30),
        brainstormer_max_turns=debate_raw.get("brainstormer_max_turns", 30),
        mediator_max_turns=debate_raw.get("mediator_max_turns", 40),
    )

    # Parse knowledge config
    knowledge_raw = raw.get("knowledge", {})
    knowledge_config = KnowledgeConfig(**knowledge_raw) if knowledge_raw else KnowledgeConfig()

    # Parse spawn config
    spawn_raw = raw.get("spawn", {})
    spawn_config = SpawnConfig(**spawn_raw) if spawn_raw else SpawnConfig()

    return OrchestratorConfig(
        workspace_dir=raw.get("workspace_dir", "workspace"),
        max_review_cycles=raw.get("max_review_cycles", 3),
        max_budget_usd=raw.get("max_budget_usd", 50.0),
        default_workflow=default_workflow,
        enhanced_perception=raw.get("enhanced_perception", False),
        checklist_verify=raw.get("checklist_verify", True),
        tech_stack_confirmation=raw.get("tech_stack_confirmation", True),
        max_concurrent_agents=raw.get("max_concurrent_agents", 0),
        phases=phases,
        agents=agents,
        spawn=spawn_config,
        debate=debate_config,
        knowledge=knowledge_config,
        monitoring=raw.get("monitoring", {}),
    )
