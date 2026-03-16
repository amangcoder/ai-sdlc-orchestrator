"""Pydantic models for artifacts, orchestrator state, and configuration."""

from __future__ import annotations

from datetime import datetime
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
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    REVIEW = "review"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"


class ModelTier(str, Enum):
    HAIKU = "haiku"
    SONNET = "sonnet"
    sonnet = "sonnet"


class WorkflowType(str, Enum):
    FEATURE_DEVELOPMENT = "feature_development"
    BUGFIX = "bugfix"
    REFACTOR = "refactor"
    PERFORMANCE_OPTIMIZATION = "performance_optimization"
    SECURITY_AUDIT = "security_audit"
    CUSTOM = "custom"


class AgentRole(str, Enum):
    PRODUCT_MANAGER = "product_manager"
    SOFTWARE_ARCHITECT = "software_architect"
    PRINCIPAL_ENGINEER = "principal_engineer"
    TECHNICAL_PROJECT_MANAGER = "technical_project_manager"
    FRONTEND_ENGINEER = "frontend_engineer"
    BACKEND_ENGINEER = "backend_engineer"
    DATABASE_ENGINEER = "database_engineer"
    CACHING_PERFORMANCE_ENGINEER = "caching_performance_engineer"
    BACKEND_CODE_REVIEWER = "backend_code_reviewer"
    FRONTEND_CODE_REVIEWER = "frontend_code_reviewer"
    QA_PLANNER = "qa_planner"
    QA_EXECUTOR = "qa_executor"
    AUTOMATION_ENGINEER = "automation_engineer"
    DEVOPS_ENGINEER = "devops_engineer"
    SECURITY_ENGINEER = "security_engineer"
    OBSERVABILITY_ENGINEER = "observability_engineer"
    DOCUMENTATION_ENGINEER = "documentation_engineer"


class RoleAccess(str, Enum):
    READ_ONLY = "read_only"
    READ_WRITE = "read_write"


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
    assigned_role: str = Field(pattern=r"^(engineer|qa|frontend_engineer|backend_engineer|database_engineer|caching_performance_engineer|automation_engineer|devops_engineer|observability_engineer|documentation_engineer)$")
    dependencies: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(min_length=1)
    files_to_modify: list[str] = Field(default_factory=list)
    estimated_complexity: Complexity


class TaskList(BaseModel):
    tasks: list[Task] = Field(min_length=1)


# --- Engineering Plan Artifact ---

class EngineeringPlan(BaseModel):
    strategy: str = Field(min_length=20)
    implementation_order: list[str] = Field(min_length=1)
    risk_areas: list[str] = Field(default_factory=list)
    testing_strategy: str = Field(min_length=10)


# --- Threat Model Artifact ---

class Threat(BaseModel):
    id: str = Field(pattern=r"^THREAT-\d+$")
    description: str = Field(min_length=10)
    severity: IssueSeverity
    mitigation: str = Field(min_length=1)


class ThreatModel(BaseModel):
    threats: list[Threat] = Field(min_length=1)
    attack_surface: str = Field(min_length=20)
    recommendations: list[str] = Field(min_length=1)


# --- Benchmark Report Artifact ---

class BenchmarkResult(BaseModel):
    metric: str = Field(min_length=1)
    before: float | None = None
    after: float | None = None
    unit: str = Field(min_length=1)
    improvement_pct: float | None = None


class BenchmarkReport(BaseModel):
    results: list[BenchmarkResult] = Field(min_length=1)
    bottlenecks: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)


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
    lint_clean: bool | None = None
    type_check_clean: bool | None = None
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


# --- Vulnerability Report Artifact ---

class Vulnerability(BaseModel):
    id: str
    severity: IssueSeverity
    file: str = Field(min_length=1)
    description: str = Field(min_length=10)
    fix: str = Field(min_length=1)


class VulnerabilityReport(BaseModel):
    vulnerabilities: list[Vulnerability] = Field(default_factory=list)
    scan_tools_used: list[str] = Field(default_factory=list)
    summary: str = Field(min_length=20)


# --- Workflow Definition Models ---

class WorkflowStepDefinition(BaseModel):
    """A single step in a workflow definition."""
    name: str
    agent_role: AgentRole
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    next: str | None = None
    parallel: bool = False
    on_fail: str = "escalate"
    gate: str | None = None
    max_retries: int = 3


class WorkflowDefinition(BaseModel):
    """Complete workflow definition with ordered steps."""
    name: str
    workflow_type: WorkflowType
    steps: list[WorkflowStepDefinition] = Field(min_length=1)


# --- Task State (replaces EngTaskState for universal tracking) ---

class WorkflowTaskState(BaseModel):
    """State of a single task within a workflow step."""
    task_id: str
    workflow_step: str
    assigned_role: AgentRole
    description: str = ""
    required_inputs: list[str] = Field(default_factory=list)
    expected_outputs: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    status: TaskStatus = TaskStatus.PENDING
    retry_count: int = 0
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None


# --- Orchestrator State ---

class PhaseState(BaseModel):
    status: PhaseStatus = PhaseStatus.PENDING
    retry_count: int = 0
    model_tier: ModelTier = ModelTier.SONNET
    cost_usd: float = 0.0
    error: str | None = None


class EngTaskState(BaseModel):
    """Legacy task state — kept for backward compatibility."""
    task_id: str
    status: TaskStatus = TaskStatus.PENDING
    worktree_path: str | None = None
    retry_count: int = 0


class RunState(BaseModel):
    run_id: str
    feature_request: str
    workspace_dir: str
    workflow_type: WorkflowType = WorkflowType.FEATURE_DEVELOPMENT
    current_step: str | None = None
    completed_steps: list[str] = Field(default_factory=list)
    phases: dict[str, PhaseState] = Field(default_factory=dict)
    engineering_tasks: list[EngTaskState] = Field(default_factory=list)
    workflow_tasks: list[WorkflowTaskState] = Field(default_factory=list)
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
    role: AgentRole | None = None
    access: RoleAccess = RoleAccess.READ_WRITE


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
    default_workflow: WorkflowType = WorkflowType.FEATURE_DEVELOPMENT
    phases: dict[str, PhaseConfig] = Field(default_factory=dict)
    agents: dict[str, AgentConfig] = Field(default_factory=dict)


# Maps artifact names to their Pydantic models for validation
ARTIFACT_MODELS: dict[str, type[BaseModel]] = {
    "prd": PRD,
    "architecture": Architecture,
    "tasks": TaskList,
    "engineering_plan": EngineeringPlan,
    "qa_report": QAReport,
    "review": Review,
    "threat_model": ThreatModel,
    "benchmark_report": BenchmarkReport,
    "vulnerability_report": VulnerabilityReport,
}
