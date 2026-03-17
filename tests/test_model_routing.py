"""Tests for model routing modes."""

from __future__ import annotations

import pytest

from orchestrator.model_routing import (
    AgentCategory,
    MODE_DEFINITIONS,
    ROLE_CATEGORY,
    RoutingMode,
    apply_routing_mode,
)
from orchestrator.models import (
    AgentConfig,
    AgentRole,
    DebateConfig,
    ModelTier,
    OrchestratorConfig,
)
from orchestrator.roles import role_to_legacy_agent_name


def _make_config(agent_names: list[str] | None = None) -> OrchestratorConfig:
    """Build a minimal OrchestratorConfig with representative agents."""
    if agent_names is None:
        agent_names = [
            "pm", "architect", "principal_engineer",  # planning
            "backend_engineer", "frontend_engineer",   # coding
            "backend_reviewer", "qa_planner",          # verification
            "documentation_engineer", "tpm",           # lightweight
        ]
    agents = {
        name: AgentConfig(name=name, model=ModelTier.SONNET, max_turns=30)
        for name in agent_names
    }
    return OrchestratorConfig(agents=agents, debate=DebateConfig())


class TestRoleCategoryMapping:
    """Every AgentRole should be mapped to a category."""

    def test_all_roles_have_category(self):
        for role in AgentRole:
            assert role in ROLE_CATEGORY, f"AgentRole.{role.name} missing from ROLE_CATEGORY"

    def test_debate_roles_separate(self):
        assert ROLE_CATEGORY[AgentRole.DEEP_RESEARCHER] == AgentCategory.DEBATE_RESEARCH
        assert ROLE_CATEGORY[AgentRole.BRAINSTORMER] == AgentCategory.DEBATE_RESEARCH
        assert ROLE_CATEGORY[AgentRole.MEDIATOR] == AgentCategory.DEBATE_MEDIATION


class TestModeDefinitions:
    """Each mode should define all categories."""

    def test_all_modes_cover_all_categories(self):
        for mode in RoutingMode:
            for cat in AgentCategory:
                assert cat in MODE_DEFINITIONS[mode], (
                    f"Mode {mode.value} missing category {cat.value}"
                )

    def test_overkill_all_opus(self):
        for cat, (base, esc) in MODE_DEFINITIONS[RoutingMode.OVERKILL].items():
            assert base == ModelTier.OPUS, f"Overkill {cat.value} base should be opus"
            assert esc is None, f"Overkill {cat.value} should have no escalation"

    def test_superhaiku_all_haiku_base(self):
        for cat, (base, _) in MODE_DEFINITIONS[RoutingMode.SUPERHAIKU].items():
            assert base == ModelTier.HAIKU, f"SuperHaiku {cat.value} base should be haiku"

    def test_supersonnet_all_sonnet_base(self):
        for cat, (base, _) in MODE_DEFINITIONS[RoutingMode.SUPERSONNET].items():
            assert base == ModelTier.SONNET, f"SuperSonnet {cat.value} base should be sonnet"


class TestApplyRoutingMode:
    """Test apply_routing_mode mutates config correctly."""

    def test_overkill_sets_all_opus(self):
        config = _make_config()
        apply_routing_mode(config, RoutingMode.OVERKILL)

        for name, ac in config.agents.items():
            assert ac.model == ModelTier.OPUS, f"{name} should be opus in overkill"
            assert ac.escalation_model is None, f"{name} should have no escalation in overkill"

    def test_fast_planning_is_sonnet(self):
        config = _make_config()
        apply_routing_mode(config, RoutingMode.FAST)

        assert config.agents["pm"].model == ModelTier.SONNET
        assert config.agents["architect"].model == ModelTier.SONNET
        assert config.agents["principal_engineer"].model == ModelTier.SONNET

    def test_fast_coding_is_haiku(self):
        config = _make_config()
        apply_routing_mode(config, RoutingMode.FAST)

        assert config.agents["backend_engineer"].model == ModelTier.HAIKU
        assert config.agents["frontend_engineer"].model == ModelTier.HAIKU

    def test_fast_verification_is_haiku(self):
        config = _make_config()
        apply_routing_mode(config, RoutingMode.FAST)

        assert config.agents["backend_reviewer"].model == ModelTier.HAIKU
        assert config.agents["qa_planner"].model == ModelTier.HAIKU

    def test_fast_lightweight_is_haiku(self):
        config = _make_config()
        apply_routing_mode(config, RoutingMode.FAST)

        assert config.agents["documentation_engineer"].model == ModelTier.HAIKU
        assert config.agents["tpm"].model == ModelTier.HAIKU

    def test_balanced_planning_is_opus(self):
        config = _make_config()
        apply_routing_mode(config, RoutingMode.BALANCED)

        assert config.agents["pm"].model == ModelTier.OPUS
        assert config.agents["pm"].escalation_model is None

    def test_balanced_coding_is_sonnet_with_opus_escalation(self):
        config = _make_config()
        apply_routing_mode(config, RoutingMode.BALANCED)

        assert config.agents["backend_engineer"].model == ModelTier.SONNET
        assert config.agents["backend_engineer"].escalation_model == ModelTier.OPUS

    def test_balanced_lightweight_is_haiku(self):
        config = _make_config()
        apply_routing_mode(config, RoutingMode.BALANCED)

        assert config.agents["documentation_engineer"].model == ModelTier.HAIKU
        assert config.agents["tpm"].model == ModelTier.HAIKU

    def test_debate_config_updated(self):
        config = _make_config()
        apply_routing_mode(config, RoutingMode.OVERKILL)

        assert config.debate.researcher_model == ModelTier.OPUS
        assert config.debate.brainstormer_model == ModelTier.OPUS
        assert config.debate.mediator_model == ModelTier.OPUS

    def test_debate_config_fast(self):
        config = _make_config()
        apply_routing_mode(config, RoutingMode.FAST)

        assert config.debate.researcher_model == ModelTier.HAIKU
        assert config.debate.brainstormer_model == ModelTier.HAIKU
        assert config.debate.mediator_model == ModelTier.SONNET

    def test_routing_mode_stored(self):
        config = _make_config()
        apply_routing_mode(config, RoutingMode.BALANCED)

        assert config.routing_mode == "balanced"

    def test_unknown_agent_falls_back_to_coding(self):
        """Agents not in role mapping should get CODING category tier."""
        config = _make_config(["pm", "mystery_agent"])
        apply_routing_mode(config, RoutingMode.BALANCED)

        # mystery_agent should get coding tier: sonnet + opus escalation
        assert config.agents["mystery_agent"].model == ModelTier.SONNET
        assert config.agents["mystery_agent"].escalation_model == ModelTier.OPUS

    def test_superhaiku_all_haiku(self):
        config = _make_config()
        apply_routing_mode(config, RoutingMode.SUPERHAIKU)

        for name, ac in config.agents.items():
            assert ac.model == ModelTier.HAIKU, f"{name} should be haiku in superhaiku"

    def test_supersonnet_all_sonnet(self):
        config = _make_config()
        apply_routing_mode(config, RoutingMode.SUPERSONNET)

        for name, ac in config.agents.items():
            assert ac.model == ModelTier.SONNET, f"{name} should be sonnet in supersonnet"
