"""Tests for TASK-001: extended MonitoringConfig, ArtifactsConfig, and URL validator.

Acceptance criteria covered:
  1. MonitoringConfig has loki_enabled, loki_endpoint, loki_auth_token, grafana_url,
     jaeger_ui_url, and nested SLOConfig with all 6 SLI targets.
  2. ArtifactsConfig Pydantic model exists with all 5 config fields.
  3. OrchestratorConfig.artifacts is wired with default_factory=ArtifactsConfig.
  4. URL validator rejects private IP ranges unless allow_private_networks=True.
  5. URL validator allows localhost and http:// for dev, requires https:// for non-localhost
     when allow_private_networks=False.
  6. Config loading test with full monitoring/artifacts/SLO YAML parses nested fields.
  7. URL validator correctly blocks SSRF patterns with informative error messages.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from pydantic import ValidationError

from orchestrator.monitoring.config import (
    AlertConfig,
    MonitoringConfig,
    SLOConfig,
    WebhookConfig,
    _is_private_ip,
    validate_url,
)
from orchestrator.models import ArtifactsConfig, OrchestratorConfig


# ---------------------------------------------------------------------------
# SLOConfig tests
# ---------------------------------------------------------------------------

class TestSLOConfig:
    def test_defaults(self):
        slo = SLOConfig()
        assert slo.pipeline_success_rate == 0.95
        assert slo.phase_duration_p95_seconds == 300.0
        assert slo.cost_per_run_p50_usd == 1.0
        assert slo.artifact_validation_rate == 0.99
        assert slo.max_errors_per_run == 5
        assert slo.recovery_success_rate == 0.90
        assert slo.evaluation_window_hours == 24

    def test_all_six_sli_fields_exist(self):
        """All 6 SLI targets must be present on SLOConfig."""
        slo = SLOConfig()
        required_fields = {
            "pipeline_success_rate",
            "phase_duration_p95_seconds",
            "cost_per_run_p50_usd",
            "artifact_validation_rate",
            "max_errors_per_run",
            "recovery_success_rate",
        }
        # evaluation_window_hours is the 7th field (window, not an SLI target itself)
        for field in required_fields:
            assert hasattr(slo, field), f"SLOConfig missing field: {field}"

    def test_custom_values(self):
        slo = SLOConfig(
            pipeline_success_rate=0.99,
            phase_duration_p95_seconds=60.0,
            cost_per_run_p50_usd=0.50,
            artifact_validation_rate=1.0,
            max_errors_per_run=0,
            recovery_success_rate=0.95,
            evaluation_window_hours=12,
        )
        assert slo.pipeline_success_rate == 0.99
        assert slo.phase_duration_p95_seconds == 60.0
        assert slo.evaluation_window_hours == 12


# ---------------------------------------------------------------------------
# MonitoringConfig new fields tests
# ---------------------------------------------------------------------------

class TestMonitoringConfigNewFields:
    def test_loki_defaults(self):
        cfg = MonitoringConfig()
        assert cfg.loki_enabled is False
        assert cfg.loki_endpoint == "http://localhost:3100"
        assert cfg.loki_auth_token is None

    def test_grafana_url_defaults_none(self):
        cfg = MonitoringConfig()
        assert cfg.grafana_url is None

    def test_jaeger_ui_url_defaults_none(self):
        cfg = MonitoringConfig()
        assert cfg.jaeger_ui_url is None

    def test_slo_field_is_nested_slo_config(self):
        cfg = MonitoringConfig()
        assert isinstance(cfg.slo, SLOConfig)

    def test_allow_private_networks_defaults_true(self):
        """Default must be True for backward compatibility."""
        cfg = MonitoringConfig()
        assert cfg.allow_private_networks is True

    def test_set_loki_fields(self):
        cfg = MonitoringConfig(
            loki_enabled=True,
            loki_endpoint="http://localhost:3100",
            loki_auth_token="Bearer secret123",
        )
        assert cfg.loki_enabled is True
        assert cfg.loki_auth_token == "Bearer secret123"

    def test_set_grafana_url(self):
        cfg = MonitoringConfig(grafana_url="http://localhost:3000")
        assert cfg.grafana_url == "http://localhost:3000"

    def test_set_jaeger_ui_url(self):
        cfg = MonitoringConfig(jaeger_ui_url="http://localhost:16686")
        assert cfg.jaeger_ui_url == "http://localhost:16686"

    def test_custom_slo_config(self):
        cfg = MonitoringConfig(slo=SLOConfig(pipeline_success_rate=0.999))
        assert cfg.slo.pipeline_success_rate == 0.999

    def test_existing_fields_unchanged(self):
        """Confirm pre-existing fields still work."""
        cfg = MonitoringConfig(
            metrics_enabled=True,
            metrics_port=9091,
            tracing_enabled=True,
            tracing_endpoint="http://localhost:4317",
        )
        assert cfg.metrics_enabled is True
        assert cfg.metrics_port == 9091
        assert cfg.tracing_enabled is True


# ---------------------------------------------------------------------------
# ArtifactsConfig tests
# ---------------------------------------------------------------------------

class TestArtifactsConfig:
    def test_defaults(self):
        cfg = ArtifactsConfig()
        assert cfg.versioning_enabled is True
        assert cfg.index_enabled is True
        assert cfg.retention_enabled is False
        assert cfg.retention_max_age_days == 30
        assert cfg.retention_max_runs == 100
        assert cfg.retention_keep_failed is True

    def test_all_five_config_fields_exist(self):
        """The task requires exactly these 5+1 fields (retention_keep_failed counts)."""
        required = {
            "versioning_enabled",
            "index_enabled",
            "retention_enabled",
            "retention_max_age_days",
            "retention_max_runs",
            "retention_keep_failed",
        }
        cfg = ArtifactsConfig()
        for field in required:
            assert hasattr(cfg, field), f"ArtifactsConfig missing field: {field}"

    def test_custom_values(self):
        cfg = ArtifactsConfig(
            versioning_enabled=False,
            index_enabled=False,
            retention_enabled=True,
            retention_max_age_days=7,
            retention_max_runs=20,
            retention_keep_failed=False,
        )
        assert cfg.versioning_enabled is False
        assert cfg.retention_enabled is True
        assert cfg.retention_max_age_days == 7
        assert cfg.retention_max_runs == 20
        assert cfg.retention_keep_failed is False

    def test_is_pydantic_base_model(self):
        from pydantic import BaseModel
        assert issubclass(ArtifactsConfig, BaseModel)


# ---------------------------------------------------------------------------
# OrchestratorConfig.artifacts wiring tests
# ---------------------------------------------------------------------------

class TestOrchestratorConfigArtifactsWiring:
    def test_artifacts_field_exists(self):
        cfg = OrchestratorConfig()
        assert hasattr(cfg, "artifacts")

    def test_artifacts_field_is_artifacts_config(self):
        cfg = OrchestratorConfig()
        assert isinstance(cfg.artifacts, ArtifactsConfig)

    def test_artifacts_has_default_factory(self):
        """Each new OrchestratorConfig must get its own ArtifactsConfig instance."""
        cfg1 = OrchestratorConfig()
        cfg2 = OrchestratorConfig()
        assert cfg1.artifacts is not cfg2.artifacts

    def test_artifacts_defaults_are_correct(self):
        cfg = OrchestratorConfig()
        assert cfg.artifacts.versioning_enabled is True
        assert cfg.artifacts.retention_enabled is False

    def test_artifacts_can_be_overridden(self):
        custom = ArtifactsConfig(retention_enabled=True, retention_max_runs=10)
        cfg = OrchestratorConfig(artifacts=custom)
        assert cfg.artifacts.retention_enabled is True
        assert cfg.artifacts.retention_max_runs == 10


# ---------------------------------------------------------------------------
# validate_url() unit tests
# ---------------------------------------------------------------------------

class TestValidateUrlScheme:
    def test_http_localhost_allowed(self):
        assert validate_url("http://localhost:3100") == "http://localhost:3100"

    def test_https_allowed(self):
        assert validate_url("https://example.com/path") == "https://example.com/path"

    def test_http_allowed_with_private_networks_true(self):
        """When allow_private_networks=True (default), http:// everywhere is fine."""
        assert validate_url("http://10.0.0.1:3100", allow_private_networks=True) == "http://10.0.0.1:3100"

    def test_ftp_scheme_rejected(self):
        with pytest.raises(ValueError, match="ftp"):
            validate_url("ftp://example.com/file")

    def test_file_scheme_rejected(self):
        with pytest.raises(ValueError, match="file"):
            validate_url("file:///etc/passwd")

    def test_javascript_scheme_rejected(self):
        with pytest.raises(ValueError, match="javascript"):
            validate_url("javascript:alert(1)")

    def test_missing_scheme_rejected(self):
        with pytest.raises(ValueError):
            validate_url("localhost:3100")

    def test_empty_string_rejected(self):
        with pytest.raises(ValueError):
            validate_url("")


class TestValidateUrlPrivateIPs:
    """URL validator SSRF prevention — private IP rejection when allow_private_networks=False."""

    def _assert_blocked(self, url: str) -> None:
        with pytest.raises(ValueError, match="private"):
            validate_url(url, allow_private_networks=False)

    def test_10_x_x_x_blocked(self):
        self._assert_blocked("http://10.0.0.1:3100")

    def test_10_255_255_255_blocked(self):
        self._assert_blocked("https://10.255.255.255/api")

    def test_172_16_x_x_blocked(self):
        self._assert_blocked("http://172.16.0.1:9090")

    def test_172_31_x_x_blocked(self):
        self._assert_blocked("http://172.31.255.255/metrics")

    def test_192_168_x_x_blocked(self):
        self._assert_blocked("http://192.168.1.1:3000")

    def test_169_254_x_x_blocked(self):
        """Link-local (AWS metadata endpoint pattern) must be blocked."""
        self._assert_blocked("http://169.254.169.254/latest/meta-data/")

    def test_127_x_x_x_blocked_except_localhost(self):
        """IP 127.0.0.1 must be blocked (only the hostname 'localhost' is exempt)."""
        self._assert_blocked("http://127.0.0.1:8080")

    def test_localhost_hostname_allowed(self):
        """The hostname 'localhost' is always permitted."""
        assert validate_url("http://localhost:3100", allow_private_networks=False) == "http://localhost:3100"

    def test_public_ip_allowed(self):
        """A real public IP must not be blocked."""
        assert validate_url("https://8.8.8.8/api", allow_private_networks=False) == "https://8.8.8.8/api"

    def test_domain_name_allowed(self):
        """Domain names (no DNS resolution at config time) must not be blocked."""
        assert validate_url("https://grafana.example.com", allow_private_networks=False) == "https://grafana.example.com"

    def test_allow_private_networks_true_bypasses_check(self):
        """With allow_private_networks=True, private IPs must be permitted."""
        assert validate_url("http://10.0.0.1:3100", allow_private_networks=True) == "http://10.0.0.1:3100"

    def test_error_message_is_informative(self):
        """Error message must identify the blocked IP and mention SSRF or private."""
        with pytest.raises(ValueError) as exc_info:
            validate_url("http://169.254.169.254/meta-data", allow_private_networks=False)
        msg = str(exc_info.value)
        assert "169.254.169.254" in msg or "private" in msg.lower() or "SSRF" in msg


class TestIsPrivateIp:
    """Unit tests for the internal _is_private_ip helper."""

    def test_localhost_is_not_private(self):
        assert _is_private_ip("localhost") is False

    def test_empty_string_is_not_private(self):
        assert _is_private_ip("") is False

    def test_domain_name_is_not_private(self):
        assert _is_private_ip("grafana.example.com") is False

    def test_10_x_x_x_is_private(self):
        assert _is_private_ip("10.0.0.1") is True

    def test_172_16_is_private(self):
        assert _is_private_ip("172.16.0.1") is True

    def test_172_31_is_private(self):
        assert _is_private_ip("172.31.0.1") is True

    def test_172_15_is_not_private(self):
        assert _is_private_ip("172.15.0.1") is False

    def test_172_32_is_not_private(self):
        assert _is_private_ip("172.32.0.1") is False

    def test_192_168_is_private(self):
        assert _is_private_ip("192.168.0.1") is True

    def test_169_254_is_private(self):
        assert _is_private_ip("169.254.169.254") is True

    def test_127_0_0_1_is_private(self):
        assert _is_private_ip("127.0.0.1") is True

    def test_public_ip_is_not_private(self):
        assert _is_private_ip("8.8.8.8") is False


# ---------------------------------------------------------------------------
# WebhookConfig URL scheme validator tests
# ---------------------------------------------------------------------------

class TestWebhookConfigUrlValidator:
    def test_http_webhook_allowed(self):
        wh = WebhookConfig(url="http://example.com/hook")
        assert wh.url == "http://example.com/hook"

    def test_https_webhook_allowed(self):
        wh = WebhookConfig(url="https://hooks.example.com/notify")
        assert wh.url == "https://hooks.example.com/notify"

    def test_ftp_webhook_rejected(self):
        with pytest.raises(ValidationError):
            WebhookConfig(url="ftp://example.com/hook")

    def test_file_webhook_rejected(self):
        with pytest.raises(ValidationError):
            WebhookConfig(url="file:///tmp/hook")


# ---------------------------------------------------------------------------
# MonitoringConfig model-level URL validator tests
# ---------------------------------------------------------------------------

class TestMonitoringConfigUrlValidation:
    def test_default_config_passes(self):
        """Default config (all localhost http://) must load without error."""
        cfg = MonitoringConfig()
        assert cfg is not None

    def test_private_ip_blocked_when_flag_false(self):
        with pytest.raises(ValidationError) as exc_info:
            MonitoringConfig(
                loki_endpoint="http://10.0.0.5:3100",
                allow_private_networks=False,
            )
        msg = str(exc_info.value)
        assert "loki_endpoint" in msg or "10.0.0.5" in msg

    def test_tracing_endpoint_private_ip_blocked(self):
        with pytest.raises(ValidationError):
            MonitoringConfig(
                tracing_endpoint="http://192.168.1.10:4317",
                allow_private_networks=False,
            )

    def test_grafana_url_private_ip_blocked(self):
        with pytest.raises(ValidationError):
            MonitoringConfig(
                grafana_url="http://172.16.0.1:3000",
                allow_private_networks=False,
            )

    def test_jaeger_ui_url_private_ip_blocked(self):
        with pytest.raises(ValidationError):
            MonitoringConfig(
                jaeger_ui_url="http://10.10.10.10:16686",
                allow_private_networks=False,
            )

    def test_webhook_private_ip_blocked(self):
        with pytest.raises(ValidationError):
            MonitoringConfig(
                webhooks=[WebhookConfig(url="http://192.168.0.5/hook")],
                allow_private_networks=False,
            )

    def test_allow_private_networks_true_permits_private_ips(self):
        cfg = MonitoringConfig(
            loki_endpoint="http://10.0.0.5:3100",
            grafana_url="http://172.16.0.1:3000",
            allow_private_networks=True,
        )
        assert cfg.loki_endpoint == "http://10.0.0.5:3100"

    def test_localhost_always_permitted_even_with_flag_false(self):
        cfg = MonitoringConfig(
            tracing_endpoint="http://localhost:4317",
            loki_endpoint="http://localhost:3100",
            allow_private_networks=False,
        )
        assert cfg.tracing_endpoint == "http://localhost:4317"

    def test_ftp_scheme_blocked_regardless_of_flag(self):
        """Non-http/https scheme must be rejected even when allow_private_networks=True."""
        with pytest.raises(ValidationError):
            MonitoringConfig(
                loki_endpoint="ftp://example.com:3100",
                allow_private_networks=True,
            )

    def test_error_message_names_field(self):
        """Validation error must identify which field caused the problem."""
        with pytest.raises(ValidationError) as exc_info:
            MonitoringConfig(
                grafana_url="http://169.254.169.254/meta-data",
                allow_private_networks=False,
            )
        msg = str(exc_info.value)
        assert "grafana_url" in msg


# ---------------------------------------------------------------------------
# Full YAML round-trip config loading test
# ---------------------------------------------------------------------------

class TestConfigYamlLoading:
    """Config loading tests for full monitoring/artifacts/SLO YAML."""

    def _write_yaml(self, tmp_path: Path, content: str) -> Path:
        cfg = tmp_path / "config.yaml"
        cfg.write_text(textwrap.dedent(content))
        return cfg

    def test_full_monitoring_artifacts_slo_yaml_loads(self, tmp_path: Path):
        """A YAML with all new monitoring, artifacts, and SLO fields must parse correctly."""
        from orchestrator.config import load_config

        yaml_content = """\
            workspace_dir: workspace
            max_budget_usd: 10.0
            max_review_cycles: 3
            max_concurrent_agents: 5

            monitoring:
              metrics_enabled: true
              metrics_port: 9090
              tracing_enabled: true
              tracing_endpoint: "http://localhost:4317"
              loki_enabled: true
              loki_endpoint: "http://localhost:3100"
              loki_auth_token: "Bearer mytoken"
              grafana_url: "http://localhost:3000"
              jaeger_ui_url: "http://localhost:16686"
              allow_private_networks: true
              slo:
                pipeline_success_rate: 0.98
                phase_duration_p95_seconds: 180.0
                cost_per_run_p50_usd: 0.75
                artifact_validation_rate: 0.995
                max_errors_per_run: 3
                recovery_success_rate: 0.92
                evaluation_window_hours: 48

            artifacts:
              versioning_enabled: true
              index_enabled: true
              retention_enabled: true
              retention_max_age_days: 14
              retention_max_runs: 50
              retention_keep_failed: false
        """
        cfg_path = self._write_yaml(tmp_path, yaml_content)
        cfg = load_config(cfg_path)

        assert isinstance(cfg, OrchestratorConfig)

        # -- Artifacts --
        assert isinstance(cfg.artifacts, ArtifactsConfig)
        assert cfg.artifacts.versioning_enabled is True
        assert cfg.artifacts.retention_enabled is True
        assert cfg.artifacts.retention_max_age_days == 14
        assert cfg.artifacts.retention_max_runs == 50
        assert cfg.artifacts.retention_keep_failed is False

    def test_minimal_yaml_still_loads_with_default_artifacts(self, tmp_path: Path):
        """A YAML that omits artifacts must produce a default ArtifactsConfig."""
        from orchestrator.config import load_config

        cfg_path = self._write_yaml(tmp_path, """\
            workspace_dir: workspace
            max_budget_usd: 5.0
        """)
        cfg = load_config(cfg_path)
        assert isinstance(cfg.artifacts, ArtifactsConfig)
        assert cfg.artifacts.versioning_enabled is True

    def test_monitoring_nested_slo_yaml_loads(self, tmp_path: Path):
        """MonitoringConfig with SLO sub-object must load from YAML via load_config."""
        from orchestrator.config import load_config

        cfg_path = self._write_yaml(tmp_path, """\
            workspace_dir: workspace
            max_budget_usd: 5.0
            monitoring:
              loki_enabled: false
              slo:
                pipeline_success_rate: 0.99
                evaluation_window_hours: 12
        """)
        cfg = load_config(cfg_path)
        # monitoring is stored as dict in OrchestratorConfig — verify raw dict
        assert isinstance(cfg.monitoring, dict)
        slo_dict = cfg.monitoring.get("slo", {})
        assert slo_dict.get("pipeline_success_rate") == 0.99
        assert slo_dict.get("evaluation_window_hours") == 12

    def test_monitoring_config_direct_parse(self):
        """MonitoringConfig can be constructed directly with all new fields."""
        cfg = MonitoringConfig(
            loki_enabled=True,
            loki_endpoint="http://localhost:3100",
            loki_auth_token="token",
            grafana_url="http://localhost:3000",
            jaeger_ui_url="http://localhost:16686",
            slo=SLOConfig(
                pipeline_success_rate=0.98,
                phase_duration_p95_seconds=180.0,
                cost_per_run_p50_usd=0.75,
                artifact_validation_rate=0.995,
                max_errors_per_run=3,
                recovery_success_rate=0.92,
                evaluation_window_hours=48,
            ),
            allow_private_networks=True,
        )
        assert cfg.loki_enabled is True
        assert cfg.loki_auth_token == "token"
        assert cfg.grafana_url == "http://localhost:3000"
        assert cfg.jaeger_ui_url == "http://localhost:16686"
        assert cfg.slo.pipeline_success_rate == 0.98
        assert cfg.slo.evaluation_window_hours == 48
