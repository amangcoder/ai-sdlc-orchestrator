"""Model routing modes for cost/quality tradeoffs.

Provides preset modes that globally override per-agent model assignments,
allowing easy switching between cost-optimized and quality-optimized runs.
"""

from __future__ import annotations

from enum import Enum

import structlog

from orchestrator.models import (
    AgentRole,
    DebateConfig,
    ModelTier,
    OrchestratorConfig,
)
from orchestrator.roles import role_to_legacy_agent_name

logger = structlog.get_logger(__name__)


class AgentCategory(str, Enum):
    PLANNING = "planning"
    CODING = "coding"
    VERIFICATION = "verification"
    LIGHTWEIGHT = "lightweight"
    DEBATE_RESEARCH = "debate_research"
    DEBATE_MEDIATION = "debate_mediation"


class RoutingMode(str, Enum):
    FAST = "fast"
    SUPERHAIKU = "superhaiku"
    SUPERSONNET = "supersonnet"
    BALANCED = "balanced"
    OVERKILL = "overkill"


# Maps every AgentRole to its category for model routing.
ROLE_CATEGORY: dict[AgentRole, AgentCategory] = {
    # Planning
    AgentRole.PRODUCT_MANAGER: AgentCategory.PLANNING,
    AgentRole.SOFTWARE_ARCHITECT: AgentCategory.PLANNING,
    AgentRole.PRINCIPAL_ENGINEER: AgentCategory.PLANNING,
    AgentRole.MARKET_RESEARCHER: AgentCategory.PLANNING,
    AgentRole.COMPETITOR_RESEARCHER: AgentCategory.PLANNING,
    AgentRole.FIELD_SPECIALIST: AgentCategory.PLANNING,
    AgentRole.LEGAL_ADVISOR: AgentCategory.PLANNING,
    # Coding / Implementation
    AgentRole.FRONTEND_ENGINEER: AgentCategory.CODING,
    AgentRole.BACKEND_ENGINEER: AgentCategory.CODING,
    AgentRole.DATABASE_ENGINEER: AgentCategory.CODING,
    AgentRole.CACHING_PERFORMANCE_ENGINEER: AgentCategory.CODING,
    AgentRole.DEVOPS_ENGINEER: AgentCategory.CODING,
    AgentRole.MIGRATION_ENGINEER: AgentCategory.CODING,
    AgentRole.API_CONTRACT_DESIGNER: AgentCategory.CODING,
    AgentRole.AUTOMATION_ENGINEER: AgentCategory.CODING,
    AgentRole.OBSERVABILITY_ENGINEER: AgentCategory.CODING,
    AgentRole.CICD_SPECIALIST: AgentCategory.CODING,
    AgentRole.AWS_SPECIALIST: AgentCategory.CODING,
    AgentRole.AZURE_SPECIALIST: AgentCategory.CODING,
    AgentRole.GCP_SPECIALIST: AgentCategory.CODING,
    AgentRole.RUNPOD_SPECIALIST: AgentCategory.CODING,
    AgentRole.LLM_SPECIALIST: AgentCategory.CODING,
    AgentRole.AGENTIC_AI_SPECIALIST: AgentCategory.CODING,
    AgentRole.ML_SPECIALIST: AgentCategory.CODING,
    # Verification / Review
    AgentRole.BACKEND_CODE_REVIEWER: AgentCategory.VERIFICATION,
    AgentRole.FRONTEND_CODE_REVIEWER: AgentCategory.VERIFICATION,
    AgentRole.SECURITY_ENGINEER: AgentCategory.VERIFICATION,
    AgentRole.QA_PLANNER: AgentCategory.VERIFICATION,
    AgentRole.QA_EXECUTOR: AgentCategory.VERIFICATION,
    AgentRole.COMPLIANCE_AUDITOR: AgentCategory.VERIFICATION,
    AgentRole.DEPENDENCY_AUDITOR: AgentCategory.VERIFICATION,
    AgentRole.ACCESSIBILITY_AUDITOR: AgentCategory.VERIFICATION,
    AgentRole.INTEGRATION_TEST_ENGINEER: AgentCategory.VERIFICATION,
    AgentRole.LOAD_TEST_ENGINEER: AgentCategory.VERIFICATION,
    AgentRole.TECH_DEBT_ASSESSOR: AgentCategory.VERIFICATION,
    AgentRole.INCIDENT_ANALYST: AgentCategory.VERIFICATION,
    AgentRole.USER_BEHAVIOR_PSYCHOLOGIST: AgentCategory.VERIFICATION,
    AgentRole.END_USER_SIMULATOR: AgentCategory.VERIFICATION,
    AgentRole.UX_SPECIFIER: AgentCategory.VERIFICATION,
    # Lightweight
    AgentRole.DOCUMENTATION_ENGINEER: AgentCategory.LIGHTWEIGHT,
    AgentRole.GIT_MANAGER: AgentCategory.LIGHTWEIGHT,
    AgentRole.RELEASE_ENGINEER: AgentCategory.LIGHTWEIGHT,
    AgentRole.TECHNICAL_PROJECT_MANAGER: AgentCategory.LIGHTWEIGHT,
    # Debate
    AgentRole.DEEP_RESEARCHER: AgentCategory.DEBATE_RESEARCH,
    AgentRole.BRAINSTORMER: AgentCategory.DEBATE_RESEARCH,
    AgentRole.MEDIATOR: AgentCategory.DEBATE_MEDIATION,
}

# Each mode defines (base_model, escalation_model) per category.
MODE_DEFINITIONS: dict[RoutingMode, dict[AgentCategory, tuple[ModelTier, ModelTier | None]]] = {
    RoutingMode.FAST: {
        AgentCategory.PLANNING: (ModelTier.SONNET, ModelTier.OPUS),
        AgentCategory.CODING: (ModelTier.HAIKU, ModelTier.SONNET),
        AgentCategory.VERIFICATION: (ModelTier.HAIKU, ModelTier.SONNET),
        AgentCategory.LIGHTWEIGHT: (ModelTier.HAIKU, None),
        AgentCategory.DEBATE_RESEARCH: (ModelTier.HAIKU, ModelTier.SONNET),
        AgentCategory.DEBATE_MEDIATION: (ModelTier.SONNET, None),
    },
    RoutingMode.SUPERHAIKU: {
        AgentCategory.PLANNING: (ModelTier.HAIKU, ModelTier.SONNET),
        AgentCategory.CODING: (ModelTier.HAIKU, ModelTier.SONNET),
        AgentCategory.VERIFICATION: (ModelTier.HAIKU, ModelTier.SONNET),
        AgentCategory.LIGHTWEIGHT: (ModelTier.HAIKU, None),
        AgentCategory.DEBATE_RESEARCH: (ModelTier.HAIKU, ModelTier.SONNET),
        AgentCategory.DEBATE_MEDIATION: (ModelTier.HAIKU, ModelTier.SONNET),
    },
    RoutingMode.SUPERSONNET: {
        AgentCategory.PLANNING: (ModelTier.SONNET, ModelTier.OPUS),
        AgentCategory.CODING: (ModelTier.SONNET, ModelTier.OPUS),
        AgentCategory.VERIFICATION: (ModelTier.SONNET, ModelTier.OPUS),
        AgentCategory.LIGHTWEIGHT: (ModelTier.SONNET, None),
        AgentCategory.DEBATE_RESEARCH: (ModelTier.SONNET, ModelTier.OPUS),
        AgentCategory.DEBATE_MEDIATION: (ModelTier.SONNET, ModelTier.OPUS),
    },
    RoutingMode.BALANCED: {
        AgentCategory.PLANNING: (ModelTier.OPUS, None),
        AgentCategory.CODING: (ModelTier.SONNET, ModelTier.OPUS),
        AgentCategory.VERIFICATION: (ModelTier.SONNET, ModelTier.OPUS),
        AgentCategory.LIGHTWEIGHT: (ModelTier.HAIKU, ModelTier.SONNET),
        AgentCategory.DEBATE_RESEARCH: (ModelTier.SONNET, ModelTier.OPUS),
        AgentCategory.DEBATE_MEDIATION: (ModelTier.OPUS, None),
    },
    RoutingMode.OVERKILL: {
        AgentCategory.PLANNING: (ModelTier.OPUS, None),
        AgentCategory.CODING: (ModelTier.OPUS, None),
        AgentCategory.VERIFICATION: (ModelTier.OPUS, None),
        AgentCategory.LIGHTWEIGHT: (ModelTier.OPUS, None),
        AgentCategory.DEBATE_RESEARCH: (ModelTier.OPUS, None),
        AgentCategory.DEBATE_MEDIATION: (ModelTier.OPUS, None),
    },
}


def _build_agent_name_to_role() -> dict[str, AgentRole]:
    """Build inverse mapping from config agent name to AgentRole."""
    return {role_to_legacy_agent_name(role): role for role in AgentRole}


def apply_routing_mode(config: OrchestratorConfig, mode: RoutingMode) -> None:
    """Override all agent model assignments based on the selected routing mode.

    Mutates ``config`` in place: updates every agent's ``model`` and
    ``escalation_model``, plus the debate config model tiers.
    """
    name_to_role = _build_agent_name_to_role()
    mode_def = MODE_DEFINITIONS[mode]
    fallback_category = AgentCategory.CODING

    for agent_name, agent_config in config.agents.items():
        role = name_to_role.get(agent_name)
        if role is not None:
            category = ROLE_CATEGORY.get(role, fallback_category)
        else:
            logger.warning(
                "model_routing_unknown_agent",
                agent=agent_name,
                mode=mode.value,
                fallback_category=fallback_category.value,
            )
            category = fallback_category

        base_model, escalation_model = mode_def[category]
        agent_config.model = base_model
        agent_config.escalation_model = escalation_model

    # Override debate config models.
    research_base, _ = mode_def[AgentCategory.DEBATE_RESEARCH]
    mediation_base, _ = mode_def[AgentCategory.DEBATE_MEDIATION]
    config.debate.researcher_model = research_base
    config.debate.brainstormer_model = research_base
    config.debate.mediator_model = mediation_base

    config.routing_mode = mode.value
    logger.info("model_routing_mode_applied", mode=mode.value)
