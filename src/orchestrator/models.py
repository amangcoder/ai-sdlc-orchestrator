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
    OPUS = "opus"


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
    GIT_MANAGER = "git_manager"
    # --- New specialist roles ---
    API_CONTRACT_DESIGNER = "api_contract_designer"
    MIGRATION_ENGINEER = "migration_engineer"
    UX_SPECIFIER = "ux_specifier"
    TECH_DEBT_ASSESSOR = "tech_debt_assessor"
    RELEASE_ENGINEER = "release_engineer"
    INCIDENT_ANALYST = "incident_analyst"
    LOAD_TEST_ENGINEER = "load_test_engineer"
    COMPLIANCE_AUDITOR = "compliance_auditor"
    DEPENDENCY_AUDITOR = "dependency_auditor"
    ACCESSIBILITY_AUDITOR = "accessibility_auditor"
    INTEGRATION_TEST_ENGINEER = "integration_test_engineer"
    LEGAL_ADVISOR = "legal_advisor"
    USER_BEHAVIOR_PSYCHOLOGIST = "user_behavior_psychologist"
    # --- Cloud & infrastructure specialists ---
    CICD_SPECIALIST = "cicd_specialist"
    AWS_SPECIALIST = "aws_specialist"
    AZURE_SPECIALIST = "azure_specialist"
    GCP_SPECIALIST = "gcp_specialist"
    RUNPOD_SPECIALIST = "runpod_specialist"
    # --- AI/ML specialists ---
    LLM_SPECIALIST = "llm_specialist"
    AGENTIC_AI_SPECIALIST = "agentic_ai_specialist"
    ML_SPECIALIST = "ml_specialist"


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
    assigned_role: str = Field(pattern=r"^(engineer|qa|frontend_engineer|backend_engineer|database_engineer|caching_performance_engineer|automation_engineer|devops_engineer|observability_engineer|documentation_engineer|api_contract_designer|migration_engineer|ux_specifier|tech_debt_assessor|release_engineer|incident_analyst|load_test_engineer|compliance_auditor|dependency_auditor|accessibility_auditor|integration_test_engineer|legal_advisor|user_behavior_psychologist|cicd_specialist|aws_specialist|azure_specialist|gcp_specialist|runpod_specialist|llm_specialist|agentic_ai_specialist|ml_specialist)$")
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


# --- API Contract Artifact ---

class APIEndpointParam(BaseModel):
    type: str = Field(min_length=1)
    required: bool = True
    min_length: int | None = None
    max_length: int | None = None
    default: Any | None = None


class APIEndpointResponse(BaseModel):
    description: str = Field(min_length=1)
    body: dict[str, Any] = Field(default_factory=dict)


class APIEndpoint(BaseModel):
    method: str = Field(pattern=r"^(GET|POST|PUT|PATCH|DELETE)$")
    path: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    request: dict[str, Any] = Field(default_factory=dict)
    responses: dict[str, APIEndpointResponse] = Field(min_length=1)


class APIContract(BaseModel):
    base_url: str = Field(min_length=1)
    auth: dict[str, str] = Field(default_factory=dict)
    endpoints: list[APIEndpoint] = Field(min_length=1)
    schemas: dict[str, Any] = Field(default_factory=dict)


# --- Migration Plan Artifact ---

class MigrationPhase(BaseModel):
    phase: int = Field(ge=1)
    description: str = Field(min_length=10)
    migration_file: str = Field(min_length=1)
    code_changes_required: list[str] = Field(default_factory=list)
    rollback_steps: list[str] = Field(min_length=1)
    verification_queries: list[str] = Field(default_factory=list)
    estimated_duration: str = Field(min_length=1)
    requires_downtime: bool = False


class MigrationPlan(BaseModel):
    risk_level: str = Field(pattern=r"^(safe|moderate|dangerous)$")
    strategy: str = Field(pattern=r"^(single_step|multi_step|expand_contract)$")
    phases: list[MigrationPhase] = Field(min_length=1)
    pre_migration_checks: list[str] = Field(default_factory=list)
    rollback_plan: str = Field(min_length=20)
    data_backup: str = Field(min_length=1)


# --- UX Spec Artifact ---

class UXFlowStep(BaseModel):
    step: int = Field(ge=1)
    action: str = Field(min_length=1)
    ui_response: str = Field(min_length=1)


class UXAlternateFlow(BaseModel):
    trigger: str = Field(min_length=1)
    response: str = Field(min_length=1)


class UXFlow(BaseModel):
    id: str = Field(pattern=r"^FLOW-\d+$")
    name: str = Field(min_length=1)
    entry_point: str = Field(min_length=1)
    requirements: list[str] = Field(default_factory=list)
    steps: list[UXFlowStep] = Field(min_length=1)
    alternate_flows: list[UXAlternateFlow] = Field(default_factory=list)


class UXComponent(BaseModel):
    name: str = Field(min_length=1)
    type: str = Field(pattern=r"^(container|presentational|shared)$")
    responsibility: str = Field(min_length=1)
    states: dict[str, str] = Field(default_factory=dict)
    children: list[str] = Field(default_factory=list)


class UXSpec(BaseModel):
    flows: list[UXFlow] = Field(min_length=1)
    components: list[UXComponent] = Field(default_factory=list)
    responsive_behavior: dict[str, str] = Field(default_factory=dict)


# --- Tech Debt Inventory Artifact ---

class DebtItem(BaseModel):
    id: str = Field(pattern=r"^DEBT-\d+$")
    title: str = Field(min_length=1)
    category: str = Field(pattern=r"^(complexity|coupling|test_gap|dependency|dead_code|inconsistency|security|performance)$")
    quadrant: str = Field(pattern=r"^(reckless_deliberate|reckless_inadvertent|prudent_deliberate|prudent_inadvertent)$")
    location: str = Field(min_length=1)
    description: str = Field(min_length=10)
    impact: int = Field(ge=1, le=5)
    effort: int = Field(ge=1, le=5)
    priority: float = Field(ge=0)
    blast_radius: list[str] = Field(default_factory=list)
    test_coverage: str = Field(pattern=r"^(low|medium|high)$")
    recommendation: str = Field(min_length=1)


class TechDebtInventory(BaseModel):
    summary: str = Field(min_length=50)
    health_score: int = Field(ge=1, le=10)
    debt_items: list[DebtItem] = Field(default_factory=list)
    recommended_order: list[str] = Field(default_factory=list)
    quick_wins: list[str] = Field(default_factory=list)
    do_not_touch: list[str] = Field(default_factory=list)


# --- Release Plan Artifact ---

class RolloutStage(BaseModel):
    stage: int = Field(ge=1)
    target: str = Field(min_length=1)
    percentage: int = Field(ge=0, le=100)
    duration: str = Field(min_length=1)
    success_criteria: list[str] = Field(min_length=1)
    rollback_trigger: str = Field(min_length=1)


class ReleasePlan(BaseModel):
    version: str = Field(min_length=1)
    version_bump: str = Field(pattern=r"^(major|minor|patch)$")
    release_readiness: dict[str, Any] = Field(default_factory=dict)
    changelog: dict[str, list[str]] = Field(default_factory=dict)
    release_notes: str = Field(min_length=20)
    migration_guide: str | None = None
    rollout_plan: dict[str, Any] = Field(default_factory=dict)
    feature_flags: list[dict[str, Any]] = Field(default_factory=list)


# --- Incident Report Artifact ---

class AffectedCode(BaseModel):
    file: str = Field(min_length=1)
    line: int | None = Field(default=None, ge=1)
    description: str = Field(min_length=1)


class IncidentReport(BaseModel):
    title: str = Field(min_length=1)
    severity: IssueSeverity
    symptom: str = Field(min_length=10)
    expected_behavior: str = Field(min_length=10)
    root_cause: str = Field(min_length=20)
    five_whys: list[str] = Field(min_length=1)
    reproduction: dict[str, Any] = Field(default_factory=dict)
    affected_code: list[AffectedCode] = Field(min_length=1)
    blast_radius: dict[str, Any] = Field(default_factory=dict)
    recommended_fix: dict[str, Any] = Field(default_factory=dict)


# --- Load Test Report Artifact ---

class LoadTestProfile(BaseModel):
    name: str = Field(min_length=1)
    concurrent_users: int = Field(ge=1)
    requests_per_second: int = Field(ge=1)
    duration: str = Field(min_length=1)
    result: str = Field(pattern=r"^(pass|degraded|fail)$")


class LoadTestReport(BaseModel):
    test_profiles: list[LoadTestProfile] = Field(min_length=1)
    performance_metrics: dict[str, Any] = Field(default_factory=dict)
    saturation_point: dict[str, Any] = Field(default_factory=dict)
    findings: list[dict[str, Any]] = Field(default_factory=list)
    capacity_recommendation: str = Field(min_length=20)


# --- Compliance Report Artifact ---

class ComplianceGap(BaseModel):
    id: str = Field(pattern=r"^GAP-\d+$")
    regulation: str = Field(min_length=1)
    requirement: str = Field(min_length=10)
    current_state: str = Field(min_length=10)
    risk_level: str = Field(pattern=r"^(high|medium|low)$")
    remediation: str = Field(min_length=10)


class ComplianceReport(BaseModel):
    applicable_regulations: list[str] = Field(min_length=1)
    data_inventory: list[dict[str, Any]] = Field(default_factory=list)
    compliance_gaps: list[ComplianceGap] = Field(default_factory=list)
    controls_verified: list[dict[str, Any]] = Field(default_factory=list)
    summary: str = Field(min_length=20)


# --- Dependency Audit Artifact ---

class DependencyVulnerability(BaseModel):
    package: str = Field(min_length=1)
    current_version: str = Field(min_length=1)
    cve: str = Field(min_length=1)
    severity: str = Field(pattern=r"^(critical|high|medium|low)$")
    description: str = Field(min_length=1)
    fixed_in: str | None = None
    upgrade_breaking: bool = False
    recommendation: str = Field(min_length=1)


class DependencyAudit(BaseModel):
    scan_date: str = Field(min_length=1)
    total_dependencies: dict[str, int] = Field(default_factory=dict)
    vulnerabilities: list[DependencyVulnerability] = Field(default_factory=list)
    license_issues: list[dict[str, Any]] = Field(default_factory=list)
    maintenance_risks: list[dict[str, Any]] = Field(default_factory=list)
    recommended_upgrades: list[dict[str, Any]] = Field(default_factory=list)
    summary: str = Field(min_length=20)


# --- Accessibility Audit Artifact ---

class AccessibilityFinding(BaseModel):
    id: str = Field(pattern=r"^A11Y-\d+$")
    wcag_criterion: str = Field(min_length=1)
    severity: IssueSeverity
    element: str = Field(min_length=1)
    issue: str = Field(min_length=10)
    impact: str = Field(min_length=10)
    remediation: str = Field(min_length=10)


class AccessibilityAudit(BaseModel):
    wcag_level_tested: str = Field(pattern=r"^(A|AA|AAA)$")
    overall_compliance: str = Field(pattern=r"^(pass|partial|fail)$")
    findings: list[AccessibilityFinding] = Field(default_factory=list)
    screen_reader_issues: list[dict[str, Any]] = Field(default_factory=list)
    summary: str = Field(min_length=20)


# --- Legal Review Artifact ---

class LegalFinding(BaseModel):
    id: str = Field(pattern=r"^LEGAL-\d+$")
    category: str = Field(pattern=r"^(data_privacy|intellectual_property|terms_of_service|liability|jurisdiction|accessibility|licensing)$")
    risk_level: str = Field(pattern=r"^(critical|high|medium|low)$")
    title: str = Field(min_length=1)
    description: str = Field(min_length=10)
    affected_requirements: list[str] = Field(default_factory=list)
    recommendation: str = Field(min_length=10)
    requires_legal_counsel: bool = False
    blocking: bool = False


class LegalReview(BaseModel):
    review_scope: str = Field(min_length=1)
    risk_assessment: str = Field(pattern=r"^(low|medium|high|critical)$")
    findings: list[LegalFinding] = Field(default_factory=list)
    third_party_risks: list[dict[str, Any]] = Field(default_factory=list)
    required_user_facing_changes: list[str] = Field(default_factory=list)
    summary: str = Field(min_length=20)


# --- Behavioral Review Artifact ---

class BehavioralFinding(BaseModel):
    id: str = Field(pattern=r"^UBP-\d+$")
    category: str = Field(pattern=r"^(cognitive_load|dark_pattern|friction|motivation|inclusivity)$")
    severity: IssueSeverity
    location: str = Field(min_length=1)
    issue: str = Field(min_length=10)
    psychological_principle: str = Field(min_length=1)
    user_impact: str = Field(min_length=10)
    recommendation: str = Field(min_length=10)


class BehavioralReview(BaseModel):
    overall_assessment: str = Field(pattern=r"^(user_friendly|needs_improvement|has_dark_patterns|hostile)$")
    cognitive_load_score: dict[str, Any] = Field(default_factory=dict)
    findings: list[BehavioralFinding] = Field(default_factory=list)
    dark_patterns_detected: list[dict[str, Any]] = Field(default_factory=list)
    positive_patterns: list[str] = Field(default_factory=list)
    summary: str = Field(min_length=20)


# --- Integration Test Plan Artifact ---

class IntegrationTestPlan(BaseModel):
    boundaries_tested: list[dict[str, Any]] = Field(min_length=1)
    scenarios_covered: list[dict[str, Any]] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    total_tests: int = Field(ge=0)


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
    enhanced_perception: bool = False
    max_concurrent_agents: int = 10
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
    # New artifact types
    "api_contract": APIContract,
    "migration_plan": MigrationPlan,
    "ux_spec": UXSpec,
    "tech_debt_inventory": TechDebtInventory,
    "release_plan": ReleasePlan,
    "incident_report": IncidentReport,
    "load_test_report": LoadTestReport,
    "compliance_report": ComplianceReport,
    "dependency_audit": DependencyAudit,
    "accessibility_audit": AccessibilityAudit,
    "legal_review": LegalReview,
    "behavioral_review": BehavioralReview,
    "integration_test_plan": IntegrationTestPlan,
}
