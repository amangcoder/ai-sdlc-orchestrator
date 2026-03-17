"""Tests for config loading, ConfigurationError surfacing, and OrchestratorConfig validators.

Covers TASK-016 acceptance criteria:
  1. load_config() raises ConfigurationError (not raw ValidationError) on invalid YAML values.
  2. ConfigurationError message identifies the offending field and expected valid range.
  3. OrchestratorConfig rejects max_budget_usd <= 0 with a clear validation error.
  4. OrchestratorConfig rejects max_review_cycles < 1 and max_concurrent_agents < 0.
  5. Valid config still loads successfully without errors.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from pydantic import ValidationError

from orchestrator.config import _format_validation_error, load_config
from orchestrator.models import OrchestratorConfig
from orchestrator.monitoring.errors import ConfigurationError, ErrorCode


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_yaml(tmp_path: Path, content: str) -> Path:
    """Write *content* to a temp YAML file and return its Path."""
    cfg = tmp_path / "test_config.yaml"
    cfg.write_text(textwrap.dedent(content))
    return cfg


MINIMAL_VALID_YAML = """\
    workspace_dir: workspace
    max_review_cycles: 3
    max_budget_usd: 10.0
    max_concurrent_agents: 0
"""


# ---------------------------------------------------------------------------
# ConfigurationError class tests
# ---------------------------------------------------------------------------

class TestConfigurationError:
    def test_is_orchestrator_error(self):
        from orchestrator.monitoring.errors import OrchestratorError
        err = ConfigurationError("bad field x")
        assert isinstance(err, OrchestratorError)

    def test_error_code(self):
        err = ConfigurationError("bad field x")
        assert err.error_code == ErrorCode.CONFIGURATION_INVALID

    def test_message_preserved(self):
        err = ConfigurationError("orchestrator.max_budget_usd: must be > 0")
        assert "max_budget_usd" in str(err)


# ---------------------------------------------------------------------------
# _format_validation_error helper tests
# ---------------------------------------------------------------------------

class TestFormatValidationError:
    def test_includes_section_and_field(self):
        try:
            OrchestratorConfig(max_budget_usd=-1)
        except ValidationError as exc:
            msg = _format_validation_error("orchestrator", exc)
        assert "orchestrator" in msg
        assert "max_budget_usd" in msg

    def test_starts_with_invalid_configuration(self):
        try:
            OrchestratorConfig(max_budget_usd=-5)
        except ValidationError as exc:
            msg = _format_validation_error("orchestrator", exc)
        assert msg.startswith("Invalid configuration")

    def test_strips_pydantic_value_error_prefix(self):
        """Pydantic v2 prepends 'Value error, ' — the helper should strip that."""
        try:
            OrchestratorConfig(max_budget_usd=-1)
        except ValidationError as exc:
            msg = _format_validation_error("orchestrator", exc)
        # Should NOT contain "Value error," verbatim
        assert "Value error," not in msg

    def test_multiple_errors_joined(self):
        try:
            OrchestratorConfig(max_budget_usd=-1, max_review_cycles=0)
        except ValidationError as exc:
            msg = _format_validation_error("orchestrator", exc)
        # Both fields should appear
        assert "max_budget_usd" in msg
        assert "max_review_cycles" in msg


# ---------------------------------------------------------------------------
# OrchestratorConfig field validator tests
# ---------------------------------------------------------------------------

class TestOrchestratorConfigValidators:
    def test_valid_config_passes(self):
        cfg = OrchestratorConfig(
            max_budget_usd=1.0,
            max_review_cycles=1,
            max_concurrent_agents=0,
        )
        assert cfg.max_budget_usd == 1.0
        assert cfg.max_review_cycles == 1
        assert cfg.max_concurrent_agents == 0

    # max_budget_usd
    def test_budget_negative_rejected(self):
        with pytest.raises(ValidationError) as exc_info:
            OrchestratorConfig(max_budget_usd=-1.0)
        errors = exc_info.value.errors()
        assert any("max_budget_usd" in str(e["loc"]) for e in errors)

    def test_budget_zero_rejected(self):
        with pytest.raises(ValidationError) as exc_info:
            OrchestratorConfig(max_budget_usd=0.0)
        errors = exc_info.value.errors()
        assert any("max_budget_usd" in str(e["loc"]) for e in errors)

    def test_budget_positive_accepted(self):
        cfg = OrchestratorConfig(max_budget_usd=0.01)
        assert cfg.max_budget_usd == pytest.approx(0.01)

    # max_review_cycles
    def test_review_cycles_zero_rejected(self):
        with pytest.raises(ValidationError) as exc_info:
            OrchestratorConfig(max_review_cycles=0)
        errors = exc_info.value.errors()
        assert any("max_review_cycles" in str(e["loc"]) for e in errors)

    def test_review_cycles_negative_rejected(self):
        with pytest.raises(ValidationError) as exc_info:
            OrchestratorConfig(max_review_cycles=-3)
        errors = exc_info.value.errors()
        assert any("max_review_cycles" in str(e["loc"]) for e in errors)

    def test_review_cycles_one_accepted(self):
        cfg = OrchestratorConfig(max_review_cycles=1)
        assert cfg.max_review_cycles == 1

    # max_concurrent_agents
    def test_concurrent_agents_negative_rejected(self):
        with pytest.raises(ValidationError) as exc_info:
            OrchestratorConfig(max_concurrent_agents=-1)
        errors = exc_info.value.errors()
        assert any("max_concurrent_agents" in str(e["loc"]) for e in errors)

    def test_concurrent_agents_zero_accepted(self):
        """0 means unlimited — must be accepted."""
        cfg = OrchestratorConfig(max_concurrent_agents=0)
        assert cfg.max_concurrent_agents == 0

    def test_concurrent_agents_positive_accepted(self):
        cfg = OrchestratorConfig(max_concurrent_agents=8)
        assert cfg.max_concurrent_agents == 8

    def test_error_message_identifies_field_and_range_budget(self):
        with pytest.raises(ValidationError) as exc_info:
            OrchestratorConfig(max_budget_usd=-99.0)
        msg = str(exc_info.value)
        # Message should reference > 0
        assert "0" in msg

    def test_error_message_identifies_field_and_range_cycles(self):
        with pytest.raises(ValidationError) as exc_info:
            OrchestratorConfig(max_review_cycles=0)
        msg = str(exc_info.value)
        assert "1" in msg


# ---------------------------------------------------------------------------
# load_config() raises ConfigurationError (not raw ValidationError)
# ---------------------------------------------------------------------------

class TestLoadConfigRaisesConfigurationError:
    def test_valid_minimal_yaml_loads(self, tmp_path: Path):
        cfg_path = _write_yaml(tmp_path, MINIMAL_VALID_YAML)
        cfg = load_config(cfg_path)
        assert isinstance(cfg, OrchestratorConfig)
        assert cfg.max_budget_usd == 10.0

    def test_default_config_loads(self):
        """The shipped default.yaml must load cleanly."""
        cfg = load_config()
        assert isinstance(cfg, OrchestratorConfig)
        assert cfg.max_budget_usd > 0

    def test_negative_budget_raises_configuration_error(self, tmp_path: Path):
        cfg_path = _write_yaml(tmp_path, """\
            workspace_dir: workspace
            max_budget_usd: -5.0
            max_review_cycles: 3
        """)
        with pytest.raises(ConfigurationError) as exc_info:
            load_config(cfg_path)
        assert not isinstance(exc_info.value, ValidationError)

    def test_negative_budget_error_not_raw_validation_error(self, tmp_path: Path):
        cfg_path = _write_yaml(tmp_path, """\
            max_budget_usd: -1
        """)
        with pytest.raises(ConfigurationError):
            load_config(cfg_path)

    def test_error_message_names_offending_field(self, tmp_path: Path):
        cfg_path = _write_yaml(tmp_path, """\
            max_budget_usd: -100
        """)
        with pytest.raises(ConfigurationError) as exc_info:
            load_config(cfg_path)
        assert "max_budget_usd" in str(exc_info.value)

    def test_zero_review_cycles_raises_configuration_error(self, tmp_path: Path):
        cfg_path = _write_yaml(tmp_path, """\
            workspace_dir: workspace
            max_budget_usd: 10.0
            max_review_cycles: 0
        """)
        with pytest.raises(ConfigurationError) as exc_info:
            load_config(cfg_path)
        assert "max_review_cycles" in str(exc_info.value)

    def test_negative_concurrent_agents_raises_configuration_error(self, tmp_path: Path):
        cfg_path = _write_yaml(tmp_path, """\
            workspace_dir: workspace
            max_budget_usd: 10.0
            max_concurrent_agents: -2
        """)
        with pytest.raises(ConfigurationError) as exc_info:
            load_config(cfg_path)
        assert "max_concurrent_agents" in str(exc_info.value)

    def test_invalid_phase_config_raises_configuration_error(self, tmp_path: Path):
        """Invalid phase sub-section → ConfigurationError naming the phase."""
        cfg_path = _write_yaml(tmp_path, """\
            max_budget_usd: 10.0
            phases:
              PRD:
                agent: pm_agent
                max_retries: not_an_integer
        """)
        with pytest.raises(ConfigurationError) as exc_info:
            load_config(cfg_path)
        msg = str(exc_info.value)
        assert "phases" in msg

    def test_invalid_agent_config_raises_configuration_error(self, tmp_path: Path):
        """Invalid agent sub-section → ConfigurationError naming the agent."""
        cfg_path = _write_yaml(tmp_path, """\
            max_budget_usd: 10.0
            agents:
              pm:
                name: pm
                model: sonnet
                max_turns: not_an_integer
        """)
        with pytest.raises(ConfigurationError) as exc_info:
            load_config(cfg_path)
        msg = str(exc_info.value)
        assert "agents" in msg

    def test_invalid_spawn_config_raises_configuration_error(self, tmp_path: Path):
        """spawn.max_spawn_rounds outside [1,5] → ConfigurationError."""
        cfg_path = _write_yaml(tmp_path, """\
            max_budget_usd: 10.0
            spawn:
              max_spawn_rounds: 99
        """)
        with pytest.raises(ConfigurationError) as exc_info:
            load_config(cfg_path)
        msg = str(exc_info.value)
        assert "spawn" in msg

    def test_error_identifies_expected_range_in_message(self, tmp_path: Path):
        """The ConfigurationError message should hint at the valid range."""
        cfg_path = _write_yaml(tmp_path, """\
            max_budget_usd: 0
        """)
        with pytest.raises(ConfigurationError) as exc_info:
            load_config(cfg_path)
        msg = str(exc_info.value)
        # Should contain a hint like "> 0" or "positive"
        assert "0" in msg or "positive" in msg.lower()

    def test_configuration_error_has_correct_error_code(self, tmp_path: Path):
        cfg_path = _write_yaml(tmp_path, """\
            max_budget_usd: -1
        """)
        with pytest.raises(ConfigurationError) as exc_info:
            load_config(cfg_path)
        assert exc_info.value.error_code == ErrorCode.CONFIGURATION_INVALID

    def test_file_not_found_raises_file_not_found_error(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            load_config(tmp_path / "nonexistent.yaml")
