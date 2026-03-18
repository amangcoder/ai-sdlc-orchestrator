"""Pydantic v2 request/response models for the Mobile API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


# ── Response Models ────────────────────────────────────────────────────────

class PhaseStateResponse(BaseModel):
    """Per-phase status and cost information."""

    status: str
    cost_usd: float = 0.0
    error: str | None = None
    model_tier: str | None = None


class RunSummaryResponse(BaseModel):
    """Summary of a single orchestration run (used in list endpoint)."""

    run_id: str
    feature_request: str
    workflow_type: str
    status: str
    current_step: str | None = None
    total_cost_usd: float = 0.0
    start_time: str
    end_time: str | None = None
    steps_completed: int = 0
    steps_total: int = 0


class RunDetailResponse(BaseModel):
    """Full detail of a single orchestration run."""

    run_id: str
    feature_request: str
    workflow_type: str
    status: str
    current_step: str | None = None
    total_cost_usd: float = 0.0
    start_time: str | None = None
    end_time: str | None = None
    steps_completed: int = 0
    steps_total: int = 0
    phases: dict[str, Any] = Field(default_factory=dict)
    workflow_tasks: list[Any] = Field(default_factory=list)


class RunStartResponse(BaseModel):
    """Response after successfully starting an orchestration run."""

    run_id: str
    status: str = "started"


class CancelResponse(BaseModel):
    """Response from the cancel endpoint."""

    cancelled: bool


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    version: str
    active_runs: int


class ConfigResponse(BaseModel):
    """GET /api/v1/config response."""

    config: dict[str, Any]
    redacted_keys: list[str]


class ConfigUpdateResponse(BaseModel):
    """PUT /api/v1/config success response."""

    status: str = "updated"
    backup_path: str
    redacted_keys: list[str] = Field(default_factory=list)
    note: str = "Config changes take effect on the next run start"


# ── Request Models ─────────────────────────────────────────────────────────

# Valid workflow types accepted by the mobile API
WorkflowTypeEnum = Literal[
    "feature_development",
    "bugfix",
    "refactor",
    "performance_optimization",
    "security_audit",
]


class RunStartRequest(BaseModel):
    """Request body for POST /api/v1/runs."""

    feature_request: str = Field(..., min_length=1, max_length=10_000)
    workflow_type: WorkflowTypeEnum = "feature_development"
    debate: bool = False
    knowledge: bool | None = None
    enhanced_perception: bool = False
    max_budget_usd: float = Field(default=50.0, ge=1.0, le=500.0)
    dry_run: bool = False
    resume_run_id: str | None = None


class ConfigUpdateRequest(BaseModel):
    """Request body for PUT /api/v1/config."""

    updates: dict[str, Any] = Field(default_factory=dict)
