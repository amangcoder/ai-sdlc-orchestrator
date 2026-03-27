"""Monitoring configuration models."""

from __future__ import annotations

import ipaddress
from typing import Optional
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# URL validation helpers (SSRF prevention)
# ---------------------------------------------------------------------------

_ALLOWED_SCHEMES = frozenset({"http", "https"})

# Private/link-local CIDR ranges blocked for SSRF prevention
_PRIVATE_NETWORKS: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),  # link-local
    ipaddress.ip_network("127.0.0.0/8"),     # loopback (but "localhost" hostname is exempt)
    ipaddress.ip_network("::1/128"),          # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),         # IPv6 unique-local
    ipaddress.ip_network("fe80::/10"),        # IPv6 link-local
]


def _check_scheme(url: str) -> None:
    """Raise ValueError if the URL scheme is not http or https."""
    parsed = urlparse(url)
    if not parsed.scheme:
        raise ValueError(
            f"URL {url!r} is missing a scheme. Only http and https are permitted."
        )
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise ValueError(
            f"URL scheme {parsed.scheme!r} is not allowed in {url!r}; "
            "only http and https are permitted (no ftp://, file://, etc.)."
        )


def _is_private_ip(hostname: str) -> bool:
    """Return True if *hostname* is a private/link-local IP address.

    The hostname "localhost" is always considered safe and returns False.
    Domain names (non-IP strings) are allowed at config-parse time (no DNS
    resolution performed here).
    """
    if not hostname or hostname.lower() == "localhost":
        return False
    try:
        addr = ipaddress.ip_address(hostname)
    except ValueError:
        # Not an IP literal — domain names pass this check
        return False
    return any(addr in net for net in _PRIVATE_NETWORKS)


def validate_url(url: str, *, allow_private_networks: bool = True) -> str:
    """Centralized URL validator for all monitoring/webhook endpoints.

    Enforces:
    1. Scheme must be ``http`` or ``https`` — no ``ftp://``, ``file://``, etc.
    2. When *allow_private_networks* is ``False``, IP-literal hostnames that
       fall in private/link-local ranges (10.x.x.x, 172.16-31.x.x,
       192.168.x.x, 169.254.x.x, 127.x.x.x except "localhost") are rejected
       to prevent Server-Side Request Forgery (SSRF).

    Raises:
        ValueError: with an informative message describing which rule was
            violated.

    Returns:
        The original *url* string unchanged if all checks pass.
    """
    _check_scheme(url)

    if not allow_private_networks:
        parsed = urlparse(url)
        hostname = parsed.hostname or ""
        if _is_private_ip(hostname):
            raise ValueError(
                f"URL {url!r} targets a private/link-local IP address "
                f"({hostname!r}), which is blocked to prevent SSRF attacks. "
                "Set allow_private_networks=True if this is intentional "
                "(e.g., a local development or docker-compose environment)."
            )

    return url


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------

class WebhookConfig(BaseModel):
    """A single webhook endpoint for alerting."""

    url: str
    events: list[str] = Field(default_factory=lambda: [
        "run_complete", "phase_failed", "budget_exceeded",
    ])

    @field_validator("url")
    @classmethod
    def _validate_url_scheme(cls, v: str) -> str:
        """Reject non-http/https schemes at parse time (independent of allow_private_networks)."""
        _check_scheme(v)
        return v


class AlertConfig(BaseModel):
    """Controls which alert events are enabled."""

    on_failure: bool = True
    on_budget_warning: bool = True
    on_completion: bool = True


class SLOConfig(BaseModel):
    """Service Level Objective targets for the six core pipeline SLIs.

    All thresholds are evaluated over *evaluation_window_hours*.
    """

    #: Enable SLO tracking and evaluation.
    enabled: bool = False

    #: Fraction of pipeline runs that must succeed (0–1).
    pipeline_success_rate: float = 0.95
    #: 95th-percentile phase duration budget in seconds.
    phase_duration_p95_seconds: float = 300.0
    #: Median (p50) cost per run budget in USD.
    cost_per_run_p50_usd: float = 1.0
    #: Fraction of artifact writes that must pass schema validation (0–1).
    artifact_validation_rate: float = 0.99
    #: Maximum number of errors allowed in a single run.
    max_errors_per_run: int = 5
    #: Fraction of failed runs that are successfully recovered (0–1).
    recovery_success_rate: float = 0.90
    #: Rolling window in hours over which SLIs are evaluated.
    evaluation_window_hours: int = 24


# ---------------------------------------------------------------------------
# Top-level config
# ---------------------------------------------------------------------------

class MonitoringConfig(BaseModel):
    """Top-level monitoring configuration — all features opt-in."""

    # ── Prometheus / metrics ──────────────────────────────────────────────
    metrics_enabled: bool = False
    metrics_port: int = 9090

    # ── OpenTelemetry tracing ─────────────────────────────────────────────
    tracing_enabled: bool = False
    tracing_endpoint: str = "http://localhost:4317"
    tracing_console: bool = False

    # ── Dashboard ─────────────────────────────────────────────────────────
    dashboard_port: int = 8080

    # ── Alerting webhooks ─────────────────────────────────────────────────
    webhooks: list[WebhookConfig] = Field(default_factory=list)
    alerts: AlertConfig = Field(default_factory=AlertConfig)

    # ── Loki log shipping ─────────────────────────────────────────────────
    loki_enabled: bool = False
    loki_endpoint: str = "http://localhost:3100"
    #: Optional Bearer token sent in the Authorization header to Loki.
    loki_auth_token: Optional[str] = None

    # ── External UI endpoints (informational / deep-link generation) ──────
    #: Base URL of the Grafana instance (e.g. "http://localhost:3000").
    grafana_url: Optional[str] = None
    #: Base URL of the Jaeger UI (e.g. "http://localhost:16686").
    jaeger_ui_url: Optional[str] = None

    # ── Service Level Objectives ──────────────────────────────────────────
    slo: SLOConfig = Field(default_factory=SLOConfig)
    #: Base URL of the Prometheus server for SLO queries (e.g. "http://localhost:9090").
    prometheus_url: Optional[str] = None

    # ── Security ──────────────────────────────────────────────────────────
    #: Set to False in production to block private-IP URLs (SSRF prevention).
    #: Defaults to True for backward compatibility with dev/docker-compose
    #: environments that use 10.x.x.x or 172.x.x.x addresses internally.
    allow_private_networks: bool = True

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------

    @model_validator(mode="after")
    def _validate_all_urls(self) -> "MonitoringConfig":
        """Validate scheme and (optionally) private-IP restriction for every URL field."""
        # Fields that always have a value
        always_present: dict[str, str] = {
            "tracing_endpoint": self.tracing_endpoint,
            "loki_endpoint": self.loki_endpoint,
        }
        # Optional URL fields
        optional_present: dict[str, str] = {}
        if self.grafana_url is not None:
            optional_present["grafana_url"] = self.grafana_url
        if self.jaeger_ui_url is not None:
            optional_present["jaeger_ui_url"] = self.jaeger_ui_url
        if self.prometheus_url is not None:
            optional_present["prometheus_url"] = self.prometheus_url

        errors: list[str] = []
        for field_name, url in {**always_present, **optional_present}.items():
            try:
                validate_url(url, allow_private_networks=self.allow_private_networks)
            except ValueError as exc:
                errors.append(f"{field_name}: {exc}")

        # Validate webhook URLs (scheme already checked at WebhookConfig level;
        # here we add private-IP restriction when allow_private_networks=False)
        for i, webhook in enumerate(self.webhooks):
            try:
                validate_url(webhook.url, allow_private_networks=self.allow_private_networks)
            except ValueError as exc:
                errors.append(f"webhooks[{i}].url: {exc}")

        if errors:
            raise ValueError(
                "MonitoringConfig URL validation failed:\n" + "\n".join(f"  - {e}" for e in errors)
            )
        return self
