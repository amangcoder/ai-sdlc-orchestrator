"""Pydantic models for artifacts, orchestrator state, and configuration."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


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
    # AICoder-compatible aliases
    PASS = "pass"
    FAIL = "fail"
    PASS_WITH_WARNINGS = "pass_with_warnings"


# Maps AICoder verdict values to Orchestrator equivalents
_VERDICT_NORMALIZE: dict[str, str] = {
    "pass": "approve",
    "fail": "reject",
    "pass_with_warnings": "request_changes",
}


def normalize_verdict(verdict: str) -> str:
    """Normalize AICoder verdict values to Orchestrator equivalents."""
    return _VERDICT_NORMALIZE.get(verdict, verdict)


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


class SpeedMode(str, Enum):
    TURBO = "turbo"
    STANDARD = "standard"
    THOROUGH = "thorough"
    PARANOID = "paranoid"
    AUTO = "auto"


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
    # --- MCP & integration specialists ---
    MCP_TOOL_DESIGNER = "mcp_tool_designer"
    MCP_SERVER_ENGINEER = "mcp_server_engineer"
    MCP_PROTOCOL_REVIEWER = "mcp_protocol_reviewer"
    MCP_INTEGRATION_TEST_ENGINEER = "mcp_integration_test_engineer"
    CHATBOT_ENGINEER = "chatbot_engineer"
    SOCIAL_MEDIA_INTEGRATION_ENGINEER = "social_media_integration_engineer"
    # --- Impact analysis & operations ---
    CHANGE_IMPACT_ANALYZER = "change_impact_analyzer"
    DATA_ENGINEER = "data_engineer"
    RESILIENCE_TESTER = "resilience_tester"
    FINOPS_ESTIMATOR = "finops_estimator"
    RUNBOOK_AUTHOR = "runbook_author"
    REFACTORING_PLANNER = "refactoring_planner"
    # --- Research & strategy ---
    MARKET_RESEARCHER = "market_researcher"
    COMPETITOR_RESEARCHER = "competitor_researcher"
    # --- Domain specialist ---
    FIELD_SPECIALIST = "field_specialist"
    # --- User validation ---
    END_USER_SIMULATOR = "end_user_simulator"
    # --- Debate roles ---
    DEEP_RESEARCHER = "deep_researcher"
    BRAINSTORMER = "brainstormer"
    MEDIATOR = "mediator"


class RoleAccess(str, Enum):
    READ_ONLY = "read_only"
    READ_WRITE = "read_write"


# --- PRD Artifact ---

class Requirement(BaseModel):
    id: str = Field(pattern=r"^REQ-\d+$")
    description: str = Field(min_length=1)
    priority: Priority


class AcceptanceCriterion(BaseModel):
    id: str = Field(pattern=r"^AC-\d+$")
    criteria: str = Field(min_length=1)


class PRD(BaseModel):
    title: str = Field(min_length=1)
    overview: str = Field(min_length=50)
    goals: list[str] = Field(min_length=1)
    requirements: list[Requirement] = Field(min_length=1)
    constraints: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str | AcceptanceCriterion] = Field(min_length=1)


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


class DataFlowEntry(BaseModel):
    from_component: str = Field(alias="from", min_length=1)
    to: str = Field(min_length=1)
    data: str = Field(min_length=1)
    trigger: str | None = None

    model_config = {"populate_by_name": True}


class Architecture(BaseModel):
    components: list[Component] = Field(min_length=1)
    data_flow: str | list[DataFlowEntry | dict[str, Any]] = Field()
    tech_decisions: list[TechDecision] = Field(min_length=1)
    constraints: list[str] = Field(default_factory=list)
    directory_structure: dict[str, Any] | list[str] | None = None

    @field_validator("data_flow", mode="after")
    @classmethod
    def _validate_data_flow(cls, v: Any) -> Any:
        if isinstance(v, str) and len(v) < 20:
            raise ValueError("String data_flow must be at least 20 characters")
        if isinstance(v, list) and len(v) < 1:
            raise ValueError("List data_flow must have at least 1 entry")
        return v


# --- Tasks Artifact ---

class Task(BaseModel):
    task_id: str = Field(pattern=r"^TASK-\d+$")
    title: str = Field(min_length=1)
    description: str = Field(min_length=10)
    assigned_role: str = Field(min_length=1)

    @field_validator("assigned_role")
    @classmethod
    def validate_assigned_role(cls, v: str) -> str:
        valid_roles = {role.value for role in AgentRole}
        # Also accept legacy "engineer" and "qa" aliases
        valid_roles.update({"engineer", "qa"})
        if v not in valid_roles:
            raise ValueError(f"Invalid role '{v}'. Valid roles: {sorted(valid_roles)}")
        return v

    dependencies: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(min_length=1)
    files_to_modify: list[str] = Field(default_factory=list)
    estimated_complexity: Complexity


class TaskList(BaseModel):
    tasks: list[Task] = Field(min_length=1)


# --- Engineering Plan Artifact ---

class ImplementationPhase(BaseModel):
    phase: str = Field(min_length=1)
    description: str = Field(min_length=1)
    tasks: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)


class RiskArea(BaseModel):
    area: str = Field(min_length=1)
    risk: str = Field(min_length=1)
    mitigation: str = Field(min_length=1)


class TestingStrategy(BaseModel):
    unit: str = ""
    integration: str = ""
    e2e: str = ""
    manual: str = ""


class EngineeringPlan(BaseModel):
    strategy: str = Field(min_length=20)
    implementation_order: list[str | ImplementationPhase] = Field(min_length=1)
    risk_areas: list[str | RiskArea] = Field(default_factory=list)
    testing_strategy: str | TestingStrategy

    @field_validator("testing_strategy", mode="before")
    @classmethod
    def _validate_testing_strategy(cls, v: Any) -> Any:
        if isinstance(v, str) and len(v) < 10:
            raise ValueError("testing_strategy string must be >= 10 chars")
        return v


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
    file: str | None = Field(default=None, min_length=1)
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


# --- End User Evaluation Artifact ---

class FrictionPoint(BaseModel):
    id: str = Field(pattern=r"^UE-\d+$")
    severity: str = Field(pattern=r"^(blocker|major|minor)$")
    location: str = Field(min_length=1)
    description: str = Field(min_length=10)
    user_quote: str = Field(min_length=1)
    suggestion: str = Field(min_length=1)


class JourneyStep(BaseModel):
    step: int = Field(ge=1)
    action: str = Field(min_length=1)
    expectation: str = Field(min_length=1)
    actual: str = Field(min_length=1)
    reaction: str = Field(pattern=r"^(confused|frustrated|neutral|satisfied|delighted)$")
    notes: str = Field(default="")


class EndUserEvaluation(BaseModel):
    personas_evaluated: list[dict[str, Any]] = Field(min_length=1)
    discovery: dict[str, Any] = Field(default_factory=dict)
    journey_walkthrough: list[JourneyStep] = Field(min_length=1)
    friction_points: list[FrictionPoint] = Field(default_factory=list)
    confusion_points: list[dict[str, Any]] = Field(default_factory=list)
    delight_moments: list[str] = Field(default_factory=list)
    unmet_expectations: list[str] = Field(default_factory=list)
    task_completion: dict[str, Any] = Field(default_factory=dict)
    verdict: str = Field(pattern=r"^(ready|needs_work|not_usable)$")
    summary: str = Field(min_length=30)


# --- Debate Models ---

class DebatePositionCritique(BaseModel):
    target_agent_id: str = Field(min_length=1)
    critique: str = Field(min_length=10)
    severity: str = Field(pattern=r"^(fundamental|significant|minor)$")


class DebatePositionAgreement(BaseModel):
    target_agent_id: str = Field(min_length=1)
    point_of_agreement: str = Field(min_length=10)


class DebatePosition(BaseModel):
    """A single agent's position in one round of debate."""
    agent_id: str = Field(min_length=1)
    agent_role: str = Field(pattern=r"^(deep_researcher|brainstormer)$")
    round_number: int = Field(ge=1)
    thesis: str = Field(min_length=50)
    evidence: list[str] = Field(min_length=1)
    risks_identified: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(min_length=1)
    critiques_of_others: list[DebatePositionCritique] = Field(default_factory=list)
    agreements_with_others: list[DebatePositionAgreement] = Field(default_factory=list)
    confidence: int = Field(ge=0, le=100)
    evolved_from_previous: bool = False
    evolution_summary: str | None = None


class DebateRound(BaseModel):
    """All positions from a single round."""
    round_number: int = Field(ge=1)
    positions: list[DebatePosition] = Field(min_length=1)
    convergence_score: float = Field(ge=0.0, le=1.0, default=0.0)


class DebateConclusionRequirement(BaseModel):
    requirement: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    source_agents: list[str] = Field(default_factory=list)
    confidence: int = Field(ge=0, le=100)


class DebateConclusionTension(BaseModel):
    tension: str = Field(min_length=1)
    side_a: str = Field(min_length=1)
    side_b: str = Field(min_length=1)
    mediator_recommendation: str = Field(min_length=1)


class DebateConclusionRisk(BaseModel):
    risk: str = Field(min_length=1)
    severity: str = Field(pattern=r"^(critical|high|medium|low)$")
    mitigation: str = Field(min_length=1)
    raised_by: list[str] = Field(default_factory=list)


class DebateConclusionDissent(BaseModel):
    agent_id: str = Field(min_length=1)
    dissent: str = Field(min_length=1)
    mediator_note: str = Field(min_length=1)


class DebateConclusion(BaseModel):
    """Mediator's final synthesis of the debate."""
    resolved_requirements: list[DebateConclusionRequirement] = Field(min_length=1)
    unresolved_tensions: list[DebateConclusionTension] = Field(default_factory=list)
    risk_assessment: list[DebateConclusionRisk] = Field(default_factory=list)
    recommended_scope: str = Field(min_length=20)
    recommended_priorities: list[str] = Field(min_length=1)
    dissenting_opinions: list[DebateConclusionDissent] = Field(default_factory=list)
    overall_confidence: int = Field(ge=0, le=100)
    rounds_conducted: int = Field(ge=1)
    total_positions_evaluated: int = Field(ge=1)


class DebateState(BaseModel):
    """Tracks the full debate lifecycle."""
    debate_id: str
    feature_request: str
    researcher_count: int = Field(ge=1, default=2)
    brainstormer_count: int = Field(ge=1, default=2)
    max_rounds: int = Field(ge=1, default=3)
    rounds: list[DebateRound] = Field(default_factory=list)
    conclusion: DebateConclusion | None = None
    status: str = Field(
        pattern=r"^(pending|in_progress|converged|max_rounds_reached|completed|interrupted)$",
        default="pending",
    )
    total_cost_usd: float = 0.0


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
    max_retries: int = 1


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
    error_code: str | None = None


# --- Orchestrator State ---

class PhaseState(BaseModel):
    status: PhaseStatus = PhaseStatus.PENDING
    retry_count: int = 0
    model_tier: ModelTier = ModelTier.SONNET
    cost_usd: float = 0.0
    error: str | None = None
    error_code: str | None = None
    artifact_retry_exhausted: bool = False


class EngTaskState(BaseModel):
    """Legacy task state — kept for backward compatibility."""
    task_id: str
    status: TaskStatus = TaskStatus.PENDING
    worktree_path: str | None = None
    retry_count: int = 0


class InterruptEvent(BaseModel):
    """Records a pipeline interruption and optional ad-hoc task injection."""
    timestamp: datetime
    step_paused_at: str
    reason: str = "user_request"
    injected_prompt: str | None = None
    injection_cost_usd: float = 0.0
    resumed: bool = False


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
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    review_cycles: int = 0
    max_review_cycles: int = 3
    interrupted: bool = False
    interrupt_history: list[InterruptEvent] = Field(default_factory=list)
    config_hash: str = ""
    spawn_history: list[SpawnRecord] = Field(default_factory=list)
    custom_workflow_definition: str | None = None


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


class KnowledgeConfig(BaseModel):
    """Configuration for AICoder knowledge integration."""
    enabled: bool = True
    aicoder_path: str = ""           # empty = auto-detect ../AICoder relative to project root
    richness: str = "rich"           # minimal | standard | rich — controls depth of knowledge extraction
    build_timeout_seconds: int = 60
    skip_if_fresh_minutes: int = 5
    inject_brief: bool = True
    inject_artifact_digests: bool = True
    cumulative_context: bool = True
    mcp_tools: bool = True
    brief_max_files: int = 30
    brief_max_symbols: int = 15
    cleanup_mcp_config: bool = True
    skip_vectors: bool = False
    skip_features: bool = False
    watcher_enabled: bool = True
    watcher_debounce_seconds: float = 5.0


class KnowledgeContext(BaseModel):
    """Runtime knowledge context populated after knowledge build."""
    brief: str = ""
    knowledge_root: str = ""
    mcp_configured: bool = False
    mcp_server_config: dict[str, Any] | None = None  # MCP server dict for direct SDK injection
    build_time_ms: float = 0.0
    file_count: int = 0


class ExplorationConfig(BaseModel):
    """Configuration for codebase exploration depth."""
    exploration_depth: str = "normal"  # none | minimal | normal | deep
    max_explore_calls: int = 0  # 0 = unlimited


class SpawnConfig(BaseModel):
    """Configuration for dynamic agent spawning."""
    enabled: bool = False
    max_spawn_rounds: int = Field(ge=1, le=5, default=2)
    max_spawns_per_round: int = Field(ge=1, le=10, default=5)
    spawned_agent_max_turns: int = Field(ge=5, le=60, default=25)
    extra_permissions: dict[str, list[str]] = Field(default_factory=dict)


class SpawnRecord(BaseModel):
    """Records a single spawn event in the run state."""
    parent_step: str
    parent_role: str
    round_number: int
    spawned_role: str
    reason: str = ""
    success: bool = False
    cost_usd: float = 0.0


class DebateConfig(BaseModel):
    enabled: bool = False
    researcher_count: int = Field(ge=1, default=2)
    brainstormer_count: int = Field(ge=1, default=2)
    max_rounds: int = Field(ge=1, default=3)
    convergence_threshold: float = Field(ge=0.0, le=1.0, default=0.8)
    researcher_model: ModelTier = ModelTier.SONNET
    brainstormer_model: ModelTier = ModelTier.SONNET
    mediator_model: ModelTier = ModelTier.OPUS
    researcher_max_turns: int = 30
    brainstormer_max_turns: int = 30
    mediator_max_turns: int = 40


class OrchestratorConfig(BaseModel):
    workspace_dir: str = "workspace"
    max_review_cycles: int = 3
    max_budget_usd: float = 50.0
    default_workflow: WorkflowType = WorkflowType.FEATURE_DEVELOPMENT
    enhanced_perception: bool = False
    confirm: bool = False
    checklist_verify: bool = True
    tech_stack_confirmation: bool = True
    max_concurrent_agents: int = 10
    phases: dict[str, PhaseConfig] = Field(default_factory=dict)
    agents: dict[str, AgentConfig] = Field(default_factory=dict)
    exploration: ExplorationConfig = Field(default_factory=ExplorationConfig)
    spawn: SpawnConfig = Field(default_factory=SpawnConfig)
    debate: DebateConfig = Field(default_factory=DebateConfig)
    knowledge: KnowledgeConfig = Field(default_factory=KnowledgeConfig)
    knowledge_context: KnowledgeContext | None = None
    monitoring: dict[str, Any] = Field(default_factory=dict)
    routing_mode: str | None = None
    speed_mode: SpeedMode | None = None

    @field_validator("max_budget_usd")
    @classmethod
    def validate_max_budget_usd(cls, v: float) -> float:
        if v <= 0:
            raise ValueError(
                f"max_budget_usd must be > 0 (a positive dollar amount); got {v}"
            )
        return v

    @field_validator("max_review_cycles")
    @classmethod
    def validate_max_review_cycles(cls, v: int) -> int:
        if v < 1:
            raise ValueError(
                f"max_review_cycles must be >= 1 (at least one review pass is required); got {v}"
            )
        return v

    @field_validator("max_concurrent_agents")
    @classmethod
    def validate_max_concurrent_agents(cls, v: int) -> int:
        if v < 0:
            raise ValueError(
                f"max_concurrent_agents must be >= 0 (0 means unlimited); got {v}"
            )
        return v


# --- Market Research Artifact ---

class MarketInsight(BaseModel):
    area: str = Field(min_length=1)
    finding: str = Field(min_length=10)
    confidence: str = Field(pattern=r"^(low|medium|high)$")
    sources: list[str] = Field(default_factory=list)


class MarketResearch(BaseModel):
    market_size: str = Field(min_length=1)
    target_segments: list[str] = Field(min_length=1)
    insights: list[MarketInsight] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    summary: str = Field(min_length=20)


# --- Competitor Research Artifact ---

class CompetitorEntry(BaseModel):
    name: str = Field(min_length=1)
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    differentiators: list[str] = Field(default_factory=list)


class CompetitorResearch(BaseModel):
    competitors: list[CompetitorEntry] = Field(min_length=1)
    competitive_advantages: list[str] = Field(default_factory=list)
    market_gaps: list[str] = Field(default_factory=list)
    summary: str = Field(min_length=20)


# --- Field Specialist Review Artifact ---

class DomainFinding(BaseModel):
    area: str = Field(min_length=1)
    assessment: str = Field(min_length=10)
    severity: IssueSeverity
    recommendation: str = Field(min_length=10)


class FieldSpecialistReview(BaseModel):
    domain: str = Field(min_length=1)
    findings: list[DomainFinding] = Field(default_factory=list)
    compliance_notes: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    summary: str = Field(min_length=20)


# --- Change Impact Analysis Artifact ---

class ChangeSurface(BaseModel):
    module: str = Field(min_length=1)
    change_type: str = Field(pattern=r"^(breaking_api|schema_migration|interface_change|behavioral_change|additive|config_change)$")
    description: str = Field(min_length=1)


class DownstreamImpact(BaseModel):
    id: str = Field(pattern=r"^IMPACT-\d+$")
    affected_module: str = Field(min_length=1)
    risk_level: str = Field(pattern=r"^(high|medium|low)$")
    impact_type: str = Field(pattern=r"^(compile_error|runtime_error|behavioral_change|performance|data_integrity)$")
    description: str = Field(min_length=10)
    requires_update: bool = True
    migration_steps: list[str] = Field(default_factory=list)


class APIConsumerImpact(BaseModel):
    consumer: str = Field(min_length=1)
    endpoint: str = Field(min_length=1)
    breaking: bool = False
    mitigation: str = Field(min_length=1)


class ChangeImpactAnalysis(BaseModel):
    summary: str = Field(min_length=50)
    overall_risk: str = Field(pattern=r"^(high|medium|low)$")
    change_surface: list[ChangeSurface] = Field(min_length=1)
    downstream_impacts: list[DownstreamImpact] = Field(default_factory=list)
    api_consumers_affected: list[APIConsumerImpact] = Field(default_factory=list)
    deployment_constraints: dict[str, Any] = Field(default_factory=dict)
    coordination_needed: list[dict[str, Any]] = Field(default_factory=list)
    recommendations: list[str] = Field(min_length=1)


# --- Data Pipeline Design Artifact ---

class PipelineTransformation(BaseModel):
    step: int = Field(ge=1)
    operation: str = Field(pattern=r"^(validate|filter|map|join|aggregate|enrich|deduplicate)$")
    description: str = Field(min_length=1)
    input_schema: str = ""
    output_schema: str = ""


class DataPipeline(BaseModel):
    id: str = Field(pattern=r"^PIPE-\d+$")
    name: str = Field(min_length=1)
    type: str = Field(pattern=r"^(streaming|batch|hybrid)$")
    source: dict[str, Any] = Field(default_factory=dict)
    transformations: list[PipelineTransformation] = Field(default_factory=list)
    destination: dict[str, Any] = Field(default_factory=dict)
    freshness_requirement: str = Field(pattern=r"^(real_time|near_real_time|hourly|daily)$")
    volume_estimate: str = ""
    error_handling: dict[str, Any] = Field(default_factory=dict)


class DataQualityRule(BaseModel):
    id: str = Field(pattern=r"^DQ-\d+$")
    pipeline: str = Field(min_length=1)
    rule: str = Field(min_length=1)
    enforcement: str = Field(pattern=r"^(block|warn|log)$")
    threshold: str = ""


class DataPipelineDesign(BaseModel):
    summary: str = Field(min_length=50)
    pipelines: list[DataPipeline] = Field(min_length=1)
    data_quality_rules: list[DataQualityRule] = Field(default_factory=list)
    infrastructure: dict[str, Any] = Field(default_factory=dict)
    data_lineage: list[dict[str, Any]] = Field(default_factory=list)
    recommendations: list[str] = Field(min_length=1)


# --- Resilience Test Plan Artifact ---

class FailureDomain(BaseModel):
    domain: str = Field(min_length=1)
    type: str = Field(pattern=r"^(database|cache|queue|api|network|disk|compute)$")
    single_point_of_failure: bool = False
    current_protections: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)


class ChaosTestScenario(BaseModel):
    id: str = Field(pattern=r"^CHAOS-\d+$")
    name: str = Field(min_length=1)
    category: str = Field(pattern=r"^(dependency_down|slow_dependency|partial_failure|resource_exhaustion|data_corruption|cascading_failure|network_partition|clock_skew)$")
    failure_injected: str = Field(min_length=10)
    affected_components: list[str] = Field(min_length=1)
    expected_behavior: str = Field(min_length=10)
    actual_behavior: str = ""
    severity_if_unhandled: IssueSeverity = IssueSeverity.MAJOR
    blast_radius: str = ""
    remediation: str = Field(min_length=1)


class ResilienceTestPlan(BaseModel):
    summary: str = Field(min_length=50)
    overall_resilience: str = Field(pattern=r"^(robust|adequate|fragile|untested)$")
    failure_domains: list[FailureDomain] = Field(min_length=1)
    test_scenarios: list[ChaosTestScenario] = Field(min_length=1)
    missing_patterns: list[dict[str, Any]] = Field(default_factory=list)
    recovery_tests: list[dict[str, Any]] = Field(default_factory=list)
    recommendations: list[str] = Field(min_length=1)


# --- Cost Estimate Artifact ---

class CostLineItem(BaseModel):
    category: str = Field(pattern=r"^(compute|storage|network|api|managed_service|third_party)$")
    service: str = Field(min_length=1)
    usage: str = Field(min_length=1)
    unit_cost: str = ""
    monthly_cost: float = Field(ge=0)
    notes: str = ""


class CostTier(BaseModel):
    tier: str = Field(pattern=r"^(launch|growth|scale)$")
    monthly_users: int = Field(ge=0)
    monthly_requests: int = Field(ge=0)
    estimated_monthly_cost: float = Field(ge=0)
    breakdown: list[CostLineItem] = Field(min_length=1)


class CostRisk(BaseModel):
    id: str = Field(pattern=r"^RISK-\d+$")
    component: str = Field(min_length=1)
    risk: str = Field(min_length=10)
    worst_case_monthly: float = Field(ge=0)
    mitigation: str = Field(min_length=1)


class CostEstimate(BaseModel):
    summary: str = Field(min_length=50)
    currency: str = "USD"
    cost_tiers: list[CostTier] = Field(min_length=1)
    cost_risks: list[CostRisk] = Field(default_factory=list)
    cost_optimizations: list[dict[str, Any]] = Field(default_factory=list)
    free_tier_dependencies: list[dict[str, Any]] = Field(default_factory=list)
    recommendations: list[str] = Field(min_length=1)


# --- Runbook Artifact ---

class DeploymentStep(BaseModel):
    step: int = Field(ge=1)
    action: str = Field(min_length=1)
    command: str = ""
    verification: str = Field(min_length=1)


class RollbackStep(BaseModel):
    step: int = Field(ge=1)
    action: str = Field(min_length=1)
    command: str = ""


class IncidentProcedure(BaseModel):
    id: str = Field(pattern=r"^INC-\d+$")
    alert_name: str = Field(min_length=1)
    severity: IssueSeverity
    diagnosis: list[dict[str, Any]] = Field(min_length=1)
    resolution: list[dict[str, Any]] = Field(min_length=1)
    escalation: str = Field(min_length=1)


class Runbook(BaseModel):
    summary: str = Field(min_length=50)
    system_name: str = Field(min_length=1)
    deployment: dict[str, Any] = Field(default_factory=dict)
    incident_procedures: list[IncidentProcedure] = Field(min_length=1)
    maintenance_tasks: list[dict[str, Any]] = Field(default_factory=list)
    key_contacts: dict[str, Any] = Field(default_factory=dict)
    dashboards_and_logs: dict[str, Any] = Field(default_factory=dict)


# --- Refactoring Plan Artifact ---

class RefactoringStep(BaseModel):
    id: str = Field(pattern=r"^REFACTOR-\d+$")
    title: str = Field(min_length=1)
    addresses_debt: list[str] = Field(min_length=1)
    sequence_order: int = Field(ge=1)
    parallelizable_with: list[str] = Field(default_factory=list)
    files_to_modify: list[str] = Field(min_length=1)
    preconditions: list[str] = Field(min_length=1)
    changes: str = Field(min_length=10)
    postconditions: list[str] = Field(min_length=1)
    verification: list[str] = Field(min_length=1)
    rollback: str = Field(min_length=1)
    risk: str = Field(pattern=r"^(low|medium|high)$")
    effort: str = Field(pattern=r"^(hours|days|week)$")


class SafeStoppingPoint(BaseModel):
    after_step: str = Field(min_length=1)
    system_state: str = Field(min_length=1)
    value_delivered: str = Field(min_length=1)


class RefactoringPlan(BaseModel):
    summary: str = Field(min_length=50)
    source_debt_items: list[str] = Field(min_length=1)
    total_steps: int = Field(ge=1)
    estimated_effort: str = Field(min_length=1)
    refactoring_steps: list[RefactoringStep] = Field(min_length=1)
    dependency_graph: dict[str, list[str]] = Field(default_factory=dict)
    safe_stopping_points: list[SafeStoppingPoint] = Field(min_length=1)
    risks: list[dict[str, Any]] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)


# --- QA Plan Artifact ---

class TestCase(BaseModel):
    id: str = Field(pattern=r"^TC-\d+$")
    title: str = Field(min_length=1)
    category: str = Field(pattern=r"^(unit|integration|e2e|security|performance|accessibility)$")
    priority: Priority
    steps: list[str] = Field(min_length=1)
    expected_result: str = Field(min_length=1)


class QAPlan(BaseModel):
    test_strategy: str = Field(min_length=20)
    test_cases: list[TestCase] = Field(min_length=1)
    coverage_targets: dict[str, Any] = Field(default_factory=dict)
    risk_areas: list[str] = Field(default_factory=list)
    summary: str = Field(min_length=20)


# --- MCP Tool Spec Artifact ---

class MCPToolParam(BaseModel):
    name: str = Field(min_length=1)
    type: str = Field(min_length=1)
    description: str = Field(min_length=1)
    required: bool = True


class MCPToolDef(BaseModel):
    id: str = Field(pattern=r"^TOOL-\d+$")
    name: str = Field(min_length=1)
    description: str = Field(min_length=10)
    parameters: list[MCPToolParam] = Field(default_factory=list)
    returns: str = Field(min_length=1)
    side_effects: str = Field(pattern=r"^(none|read|write|external)$")
    requires_auth: bool = False
    rate_limit: str | None = None


class MCPResourceDef(BaseModel):
    id: str = Field(pattern=r"^RES-\d+$")
    uri_template: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = Field(min_length=10)
    mime_type: str = Field(min_length=1)


class MCPPromptDef(BaseModel):
    id: str = Field(pattern=r"^PROMPT-\d+$")
    name: str = Field(min_length=1)
    description: str = Field(min_length=10)
    arguments: list[MCPToolParam] = Field(default_factory=list)


class MCPTransportConfig(BaseModel):
    type: str = Field(pattern=r"^(stdio|sse|streamable_http)$")
    auth_method: str = Field(pattern=r"^(none|bearer|oauth2|api_key)$")
    port: int | None = None


class MCPToolSpec(BaseModel):
    server_name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    description: str = Field(min_length=20)
    sdk: str = Field(pattern=r"^(typescript|python)$")
    transport: MCPTransportConfig
    tools: list[MCPToolDef] = Field(default_factory=list)
    resources: list[MCPResourceDef] = Field(default_factory=list)
    prompts: list[MCPPromptDef] = Field(default_factory=list)
    capabilities: list[str] = Field(min_length=1)
    error_handling: dict[str, Any] = Field(default_factory=dict)


# --- MCP Test Report Artifact ---

class MCPTestCase(BaseModel):
    id: str = Field(pattern=r"^MCP-TC-\d+$")
    category: str = Field(pattern=r"^(tool_call|resource_read|prompt_get|transport|auth|capability_negotiation|error_handling|concurrency)$")
    description: str = Field(min_length=10)
    result: str = Field(pattern=r"^(pass|fail|skip)$")
    details: str = Field(default="")


class MCPTestReport(BaseModel):
    server_name: str = Field(min_length=1)
    transport_tested: str = Field(min_length=1)
    test_cases: list[MCPTestCase] = Field(min_length=1)
    tools_tested: int = Field(ge=0)
    resources_tested: int = Field(ge=0)
    protocol_compliance: str = Field(pattern=r"^(full|partial|non_compliant)$")
    findings: list[str] = Field(default_factory=list)
    verdict: QAVerdict


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
    "end_user_evaluation": EndUserEvaluation,
    # Research artifacts
    "market_research": MarketResearch,
    "competitor_research": CompetitorResearch,
    "field_specialist_review": FieldSpecialistReview,
    # QA planning
    "qa_plan": QAPlan,
    # Debate artifacts
    "debate_position": DebatePosition,
    "debate_conclusion": DebateConclusion,
    # MCP artifacts
    "mcp_tool_spec": MCPToolSpec,
    "mcp_test_report": MCPTestReport,
    # Impact analysis & operations artifacts
    "change_impact_analysis": ChangeImpactAnalysis,
    "data_pipeline_design": DataPipelineDesign,
    "resilience_test_plan": ResilienceTestPlan,
    "cost_estimate": CostEstimate,
    "runbook": Runbook,
    "refactoring_plan": RefactoringPlan,
}
