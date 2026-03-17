"""Monitoring configuration models."""

from __future__ import annotations

from pydantic import BaseModel, Field


class WebhookConfig(BaseModel):
    """A single webhook endpoint for alerting."""
    url: str
    events: list[str] = Field(default_factory=lambda: [
        "run_complete", "phase_failed", "budget_exceeded",
    ])


class AlertConfig(BaseModel):
    """Controls which alert events are enabled."""
    on_failure: bool = True
    on_budget_warning: bool = True
    on_completion: bool = True


class MonitoringConfig(BaseModel):
    """Top-level monitoring configuration — all features opt-in."""
    metrics_enabled: bool = False
    metrics_port: int = 9090
    tracing_enabled: bool = False
    tracing_endpoint: str = "http://localhost:4317"
    tracing_console: bool = False
    dashboard_port: int = 8080
    webhooks: list[WebhookConfig] = Field(default_factory=list)
    alerts: AlertConfig = Field(default_factory=AlertConfig)
