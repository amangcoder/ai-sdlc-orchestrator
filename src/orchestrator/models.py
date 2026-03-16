"""Pydantic models for artifacts, orchestrator state, and configuration."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# --- Enums ---

class Priority(str, Enum):
    MUST = "must"
    SHOULD = "should"
    COULD = "could"


class Complexity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class IssueSeverity(str, Enum):
    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"
    NIT = "nit"


class QAVerdict(str, Enum):
    PASS = "pass"
    FAIL = "fail"


class ReviewVerdict(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"
    REQUEST_CHANGES = "request_changes"


class PhaseStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class ModelTier(str, Enum):
    HAIKU = "haiku"
    SONNET = "sonnet"
    OPUS = "opus"


# --- PRD Artifact ---

class Requirement(BaseModel):
    id: str = Field(pattern=r"^REQ-\d+$")
    description: str = Field(min_length=1)
    priority: Priority


class PRD(BaseModel):
    title: str = Field(min_length=1)
    overview: str = Field(min_length=50)
    goals: list[str] = Field(min_length=1)
    requirements: list[Requirement] = Field(min_length=1)
    constraints: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(min_length=1)


# --- Architecture Artifact ---

class Component(BaseModel):
    name: str = Field(min_length=1)
    responsibility: str = Field(min_length=1)
    interfaces: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)


class TechDecision(BaseModel):
    decision: str
    rationale: str
    alternatives_considered: list[str] = Field(default_factory=list)


class Architecture(BaseModel):
    components: list[Component] = Field(min_length=1)
    data_flow: str = Field(min_length=20)
    tech_decisions: list[TechDecision] = Field(min_length=1)
    constraints: list[str] = Field(default_factory=list)


# --- Tasks Artifact ---

class Task(BaseModel):
    task_id: str = Field(pattern=r"^TASK-\d+$")
    title: str = Field(min_length=1)
    description: str = Field(min_length=10)
    assigned_role: str = Field(pattern=r"^(engineer|qa)$")
    dependencies: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(min_length=1)
    files_to_modify: list[str] = Field(default_factory=list)
    estimated_complexity: Complexity


class TaskList(BaseModel):
    tasks: list[Task] = Field(min_length=1)


# --- QA Report Artifact ---

class TestResults(BaseModel):
    passed: int = Field(ge=0)
    failed: int = Field(ge=0)
    skipped: int = Field(ge=0)


class QAIssue(BaseModel):
    severity: IssueSeverity
    file: str | None = None
    line: int | None = Field(default=None, ge=1)
    description: str = Field(min_length=1)
    suggestion: str | None = None


class QAReport(BaseModel):
    test_results: TestResults
    lint_clean: bool
    type_check_clean: bool
    issues: list[QAIssue] = Field(default_factory=list)
    verdict: QAVerdict


# --- Review Artifact ---

class ReviewIssue(BaseModel):
    severity: IssueSeverity
    file: str = Field(min_length=1)
    line: int | None = Field(default=None, ge=1)
    description: str = Field(min_length=1)
    suggestion: str | None = None


class Review(BaseModel):
    verdict: ReviewVerdict
    issues: list[ReviewIssue] = Field(default_factory=list)
    summary: str = Field(min_length=20)


# --- Orchestrator State ---

class PhaseState(BaseModel):
    status: PhaseStatus = PhaseStatus.PENDING
    retry_count: int = 0
    model_tier: ModelTier = ModelTier.SONNET
    cost_usd: float = 0.0
    error: str | None = None


class EngTaskState(BaseModel):
    task_id: str
    status: TaskStatus = TaskStatus.PENDING
    worktree_path: str | None = None
    retry_count: int = 0


class RunState(BaseModel):
    run_id: str
    feature_request: str
    workspace_dir: str
    phases: dict[str, PhaseState] = Field(default_factory=dict)
    engineering_tasks: list[EngTaskState] = Field(default_factory=list)
    total_cost_usd: float = 0.0
    review_cycles: int = 0
    max_review_cycles: int = 3


# --- Agent Configuration ---

class AgentConfig(BaseModel):
    name: str
    model: ModelTier
    max_turns: int
    escalation_model: ModelTier | None = None
    input_artifacts: list[str] = Field(default_factory=list)
    output_artifacts: list[str] = Field(default_factory=list)


# --- Orchestrator Configuration ---

class PhaseConfig(BaseModel):
    agent: str
    parallel: bool = False
    max_retries: int = 2
    timeout_minutes: int = 30


class OrchestratorConfig(BaseModel):
    workspace_dir: str = "workspace"
    max_review_cycles: int = 3
    max_budget_usd: float = 50.0
    phases: dict[str, PhaseConfig] = Field(default_factory=dict)
    agents: dict[str, AgentConfig] = Field(default_factory=dict)


# Maps artifact names to their Pydantic models for validation
ARTIFACT_MODELS: dict[str, type[BaseModel]] = {
    "prd": PRD,
    "architecture": Architecture,
    "tasks": TaskList,
    "qa_report": QAReport,
    "review": Review,
}
