"""Tests for ContainerConfig Pydantic model and load_config() container section parsing.

Covers TASK-007 acceptance criteria for container configuration:
  - ContainerConfig defaults match the architecture specification
  - Pydantic validates field types
  - OrchestratorConfig includes ContainerConfig via default_factory
  - load_config() correctly parses the container: YAML section
  - Existing _make_config() test helper still works (backward compatibility)
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from pydantic import ValidationError

from orchestrator.config import load_config
from orchestrator.models import ContainerConfig, OrchestratorConfig


# ── Helpers ───────────────────────────────────────────────────────────────────


def _write_yaml(tmp_path: Path, content: str) -> Path:
    """Write *content* to a temp YAML file and return its Path."""
    cfg = tmp_path / "test_config.yaml"
    cfg.write_text(textwrap.dedent(content))
    return cfg


# Minimal valid YAML that satisfies load_config() — mirrors tests/test_config.py
MINIMAL_YAML = """\
workspace_dir: workspace
max_review_cycles: 3
max_budget_usd: 10.0
max_concurrent_agents: 0
"""


# ── TestContainerConfigDefaults ───────────────────────────────────────────────


class TestContainerConfigDefaults:
    """Verify ContainerConfig() defaults match the architecture spec."""

    def test_enabled_default_is_false(self):
        cfg = ContainerConfig()
        assert cfg.enabled is False, "Container mode must be opt-in (enabled=False by default)"

    def test_image_default(self):
        cfg = ContainerConfig()
        assert cfg.image == "ai-sdlc-orchestrator:latest"

    def test_memory_limit_default(self):
        cfg = ContainerConfig()
        assert cfg.memory_limit == "8g"

    def test_cpu_limit_default(self):
        cfg = ContainerConfig()
        assert cfg.cpu_limit == 4.0

    def test_pids_limit_default(self):
        cfg = ContainerConfig()
        assert cfg.pids_limit == 500

    def test_network_mode_default(self):
        cfg = ContainerConfig()
        assert cfg.network_mode == "orchestrator-net"

    def test_seccomp_profile_path_default_is_none(self):
        cfg = ContainerConfig()
        assert cfg.seccomp_profile_path is None

    def test_apparmor_profile_default_is_none(self):
        cfg = ContainerConfig()
        assert cfg.apparmor_profile is None

    def test_env_file_default_is_none(self):
        cfg = ContainerConfig()
        assert cfg.env_file is None

    def test_extra_tmpfs_default_is_empty_list(self):
        cfg = ContainerConfig()
        assert cfg.extra_tmpfs == []


# ── TestContainerConfigCustomValues ──────────────────────────────────────────


class TestContainerConfigCustomValues:
    """Verify ContainerConfig accepts and stores custom field values."""

    def test_enabled_true(self):
        cfg = ContainerConfig(enabled=True)
        assert cfg.enabled is True

    def test_custom_image(self):
        cfg = ContainerConfig(image="myrepo/orchestrator:v1.0")
        assert cfg.image == "myrepo/orchestrator:v1.0"

    def test_custom_memory_limit(self):
        cfg = ContainerConfig(memory_limit="4g")
        assert cfg.memory_limit == "4g"

    def test_custom_cpu_limit(self):
        cfg = ContainerConfig(cpu_limit=2.0)
        assert cfg.cpu_limit == 2.0

    def test_custom_pids_limit(self):
        cfg = ContainerConfig(pids_limit=200)
        assert cfg.pids_limit == 200

    def test_custom_network_mode(self):
        cfg = ContainerConfig(network_mode="bridge")
        assert cfg.network_mode == "bridge"

    def test_extra_tmpfs_list(self):
        cfg = ContainerConfig(extra_tmpfs=["/var/tmp", "/run"])
        assert cfg.extra_tmpfs == ["/var/tmp", "/run"]

    def test_env_file_path(self, tmp_path):
        env_file = tmp_path / ".orchestrator.env"
        env_file.write_text("ANTHROPIC_API_KEY=sk-test\n")
        cfg = ContainerConfig(env_file=env_file)
        assert cfg.env_file == env_file

    def test_seccomp_profile_path(self):
        cfg = ContainerConfig(seccomp_profile_path="/infra/docker/seccomp-profile.json")
        assert cfg.seccomp_profile_path == "/infra/docker/seccomp-profile.json"

    def test_apparmor_profile(self):
        cfg = ContainerConfig(apparmor_profile="orchestrator-aa-profile")
        assert cfg.apparmor_profile == "orchestrator-aa-profile"


# ── TestContainerConfigValidation ────────────────────────────────────────────


class TestContainerConfigValidation:
    """Verify Pydantic rejects invalid field types."""

    def test_invalid_cpu_limit_string(self):
        with pytest.raises((ValidationError, ValueError)):
            ContainerConfig(cpu_limit="not-a-number")

    def test_invalid_pids_limit_string(self):
        with pytest.raises((ValidationError, ValueError)):
            ContainerConfig(pids_limit="many")

    def test_invalid_enabled_string(self):
        with pytest.raises((ValidationError, ValueError)):
            ContainerConfig(enabled="yes-please")

    def test_invalid_extra_tmpfs_not_list(self):
        with pytest.raises((ValidationError, ValueError)):
            ContainerConfig(extra_tmpfs="/tmp")  # should be a list


# ── TestOrchestratorConfigIntegration ────────────────────────────────────────


class TestOrchestratorConfigIntegration:
    """Verify OrchestratorConfig includes ContainerConfig via default_factory."""

    def test_orchestrator_config_has_container_field(self):
        cfg = OrchestratorConfig()
        assert hasattr(cfg, "container"), "OrchestratorConfig must have a 'container' field"

    def test_container_field_is_container_config(self):
        cfg = OrchestratorConfig()
        assert isinstance(cfg.container, ContainerConfig)

    def test_container_defaults_on_orchestrator_config(self):
        cfg = OrchestratorConfig()
        assert cfg.container.enabled is False
        assert cfg.container.image == "ai-sdlc-orchestrator:latest"
        assert cfg.container.pids_limit == 500

    def test_existing_fields_unaffected(self):
        """Adding ContainerConfig must not break existing OrchestratorConfig fields."""
        cfg = OrchestratorConfig(max_review_cycles=5, max_budget_usd=25.0)
        assert cfg.max_review_cycles == 5
        assert cfg.max_budget_usd == 25.0
        # Container field still has defaults
        assert cfg.container.enabled is False

    def test_make_config_pattern_still_works(self):
        """The _make_config() test helper pattern used in 30+ tests must still work.

        _make_config() constructs OrchestratorConfig with keyword args — adding
        an optional field with default_factory must not break it.
        """
        cfg = OrchestratorConfig(
            max_review_cycles=3,
            max_budget_usd=50.0,
            workspace_dir="workspace",
        )
        assert isinstance(cfg.container, ContainerConfig)
        assert cfg.container.enabled is False

    def test_container_field_can_be_overridden(self):
        """OrchestratorConfig can accept a custom ContainerConfig."""
        custom = ContainerConfig(enabled=True, memory_limit="4g")
        cfg = OrchestratorConfig(container=custom)
        assert cfg.container.enabled is True
        assert cfg.container.memory_limit == "4g"


# ── TestLoadConfigContainer ───────────────────────────────────────────────────


class TestLoadConfigContainer:
    """Verify load_config() correctly parses the container: YAML section."""

    def test_load_config_with_container_enabled_true(self, tmp_path):
        yaml_content = MINIMAL_YAML + "\ncontainer:\n  enabled: true\n"
        cfg_path = _write_yaml(tmp_path, yaml_content)
        config = load_config(cfg_path)
        assert config.container.enabled is True

    def test_load_config_container_enabled_false_by_default(self, tmp_path):
        cfg_path = _write_yaml(tmp_path, MINIMAL_YAML)
        config = load_config(cfg_path)
        assert config.container.enabled is False

    def test_load_config_container_image_override(self, tmp_path):
        yaml_content = MINIMAL_YAML + "\ncontainer:\n  image: myrepo/orchestrator:v2\n"
        cfg_path = _write_yaml(tmp_path, yaml_content)
        config = load_config(cfg_path)
        assert config.container.image == "myrepo/orchestrator:v2"

    def test_load_config_container_memory_limit_override(self, tmp_path):
        yaml_content = MINIMAL_YAML + "\ncontainer:\n  memory_limit: 4g\n"
        cfg_path = _write_yaml(tmp_path, yaml_content)
        config = load_config(cfg_path)
        assert config.container.memory_limit == "4g"

    def test_load_config_container_full_section(self, tmp_path):
        yaml_content = (
            MINIMAL_YAML
            + "\ncontainer:\n"
            "  enabled: true\n"
            "  image: custom-image:latest\n"
            "  memory_limit: 2g\n"
            "  cpu_limit: 2.0\n"
            "  pids_limit: 100\n"
            "  network_mode: bridge\n"
        )
        cfg_path = _write_yaml(tmp_path, yaml_content)
        config = load_config(cfg_path)
        assert config.container.enabled is True
        assert config.container.image == "custom-image:latest"
        assert config.container.memory_limit == "2g"
        assert config.container.cpu_limit == 2.0
        assert config.container.pids_limit == 100
        assert config.container.network_mode == "bridge"

    def test_load_config_without_container_section_uses_defaults(self, tmp_path):
        cfg_path = _write_yaml(tmp_path, MINIMAL_YAML)
        config = load_config(cfg_path)
        # All defaults must be present even when the section is absent
        assert config.container.enabled is False
        assert config.container.image == "ai-sdlc-orchestrator:latest"
        assert config.container.cpu_limit == 4.0

    def test_load_config_exploration_section_parsed(self, tmp_path):
        """Verify exploration YAML section is correctly parsed (bug-fix test).

        Prior to TASK-001, load_config() silently dropped the exploration: section.
        This test confirms that overriding exploration_depth takes effect.
        """
        yaml_content = MINIMAL_YAML + "\nexploration:\n  exploration_depth: deep\n"
        cfg_path = _write_yaml(tmp_path, yaml_content)
        config = load_config(cfg_path)
        # If load_config() correctly parses exploration:, this will be "deep"
        # (not the default "normal")
        assert config.exploration.exploration_depth == "deep"
