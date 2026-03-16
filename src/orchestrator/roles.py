"""Role registry — maps AgentRole enums to definitions, access levels, and agent files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from orchestrator.models import AgentRole, RoleAccess, WorkflowDefinition


AGENTS_DIR = Path(__file__).resolve().parents[2] / ".claude" / "agents"


@dataclass(frozen=True)
class RoleDefinition:
    """Static definition of an agent role."""
    role: AgentRole
    title: str
    responsibility: str
    access: RoleAccess
    agent_file: str | None  # filename in .claude/agents/, or None for dynamic prompt


ROLE_REGISTRY: dict[AgentRole, RoleDefinition] = {
    AgentRole.PRODUCT_MANAGER: RoleDefinition(
        role=AgentRole.PRODUCT_MANAGER,
        title="Product Manager",
        responsibility="Requirements, PRD, user value, acceptance criteria",
        access=RoleAccess.READ_ONLY,
        agent_file="pm.md",
    ),
    AgentRole.SOFTWARE_ARCHITECT: RoleDefinition(
        role=AgentRole.SOFTWARE_ARCHITECT,
        title="Software Architect",
        responsibility="System design, service boundaries, tech decisions, component interfaces",
        access=RoleAccess.READ_ONLY,
        agent_file="architect.md",
    ),
    AgentRole.PRINCIPAL_ENGINEER: RoleDefinition(
        role=AgentRole.PRINCIPAL_ENGINEER,
        title="Principal Engineer",
        responsibility="Translates architecture into engineering strategy and implementation plan",
        access=RoleAccess.READ_ONLY,
        agent_file="principal_engineer.md",
    ),
    AgentRole.TECHNICAL_PROJECT_MANAGER: RoleDefinition(
        role=AgentRole.TECHNICAL_PROJECT_MANAGER,
        title="Technical Project Manager",
        responsibility="Breaks work into tiny, independent, precisely-scoped tasks",
        access=RoleAccess.READ_ONLY,
        agent_file="tpm.md",
    ),
    AgentRole.FRONTEND_ENGINEER: RoleDefinition(
        role=AgentRole.FRONTEND_ENGINEER,
        title="Frontend Engineer",
        responsibility="Implements frontend tasks. One task at a time. Does not design or plan.",
        access=RoleAccess.READ_WRITE,
        agent_file="frontend_engineer.md",
    ),
    AgentRole.BACKEND_ENGINEER: RoleDefinition(
        role=AgentRole.BACKEND_ENGINEER,
        title="Backend Engineer",
        responsibility="Implements backend tasks. One task at a time. Does not design or plan.",
        access=RoleAccess.READ_WRITE,
        agent_file="backend_engineer.md",
    ),
    AgentRole.DATABASE_ENGINEER: RoleDefinition(
        role=AgentRole.DATABASE_ENGINEER,
        title="Database Engineer",
        responsibility="Schema design, migrations, indexing, query optimization",
        access=RoleAccess.READ_WRITE,
        agent_file="database_engineer.md",
    ),
    AgentRole.CACHING_PERFORMANCE_ENGINEER: RoleDefinition(
        role=AgentRole.CACHING_PERFORMANCE_ENGINEER,
        title="Caching & Performance Engineer",
        responsibility="Caching strategy, performance optimization",
        access=RoleAccess.READ_WRITE,
        agent_file="caching_engineer.md",
    ),
    AgentRole.BACKEND_CODE_REVIEWER: RoleDefinition(
        role=AgentRole.BACKEND_CODE_REVIEWER,
        title="Backend Code Reviewer",
        responsibility="Reviews backend code for correctness, security, performance",
        access=RoleAccess.READ_ONLY,
        agent_file="backend_reviewer.md",
    ),
    AgentRole.FRONTEND_CODE_REVIEWER: RoleDefinition(
        role=AgentRole.FRONTEND_CODE_REVIEWER,
        title="Frontend Code Reviewer",
        responsibility="Reviews frontend code for UI correctness, accessibility, component architecture",
        access=RoleAccess.READ_ONLY,
        agent_file="frontend_reviewer.md",
    ),
    AgentRole.QA_PLANNER: RoleDefinition(
        role=AgentRole.QA_PLANNER,
        title="QA Engineer (Planner)",
        responsibility="Designs test strategy, edge cases, coverage plan",
        access=RoleAccess.READ_ONLY,
        agent_file="qa_planner.md",
    ),
    AgentRole.QA_EXECUTOR: RoleDefinition(
        role=AgentRole.QA_EXECUTOR,
        title="QA Engineer (Executor)",
        responsibility="Executes tests, reports failures",
        access=RoleAccess.READ_ONLY,
        agent_file="qa_executor.md",
    ),
    AgentRole.AUTOMATION_ENGINEER: RoleDefinition(
        role=AgentRole.AUTOMATION_ENGINEER,
        title="Automation Engineer",
        responsibility="Builds automated test suites and CI pipelines",
        access=RoleAccess.READ_WRITE,
        agent_file="automation_engineer.md",
    ),
    AgentRole.DEVOPS_ENGINEER: RoleDefinition(
        role=AgentRole.DEVOPS_ENGINEER,
        title="DevOps Engineer",
        responsibility="CI/CD, containerization, deployment",
        access=RoleAccess.READ_WRITE,
        agent_file="devops_engineer.md",
    ),
    AgentRole.SECURITY_ENGINEER: RoleDefinition(
        role=AgentRole.SECURITY_ENGINEER,
        title="Security Engineer",
        responsibility="Security review of architecture and code",
        access=RoleAccess.READ_ONLY,
        agent_file="security_engineer.md",
    ),
    AgentRole.OBSERVABILITY_ENGINEER: RoleDefinition(
        role=AgentRole.OBSERVABILITY_ENGINEER,
        title="Observability Engineer",
        responsibility="Logging, monitoring, metrics",
        access=RoleAccess.READ_WRITE,
        agent_file="observability_engineer.md",
    ),
    AgentRole.DOCUMENTATION_ENGINEER: RoleDefinition(
        role=AgentRole.DOCUMENTATION_ENGINEER,
        title="Documentation Engineer",
        responsibility="Technical docs and developer guides",
        access=RoleAccess.READ_WRITE,
        agent_file="documentation_engineer.md",
    ),
    AgentRole.GIT_MANAGER: RoleDefinition(
        role=AgentRole.GIT_MANAGER,
        title="Git Manager",
        responsibility="Branch management, staging, committing, merging worktree results",
        access=RoleAccess.READ_WRITE,
        agent_file="git_manager.md",
    ),
    # --- New specialist roles ---
    AgentRole.API_CONTRACT_DESIGNER: RoleDefinition(
        role=AgentRole.API_CONTRACT_DESIGNER,
        title="API Contract Designer",
        responsibility="Produces formal API specifications (OpenAPI/GraphQL) as the contract between frontend and backend",
        access=RoleAccess.READ_ONLY,
        agent_file="api_contract_designer.md",
    ),
    AgentRole.MIGRATION_ENGINEER: RoleDefinition(
        role=AgentRole.MIGRATION_ENGINEER,
        title="Migration Engineer",
        responsibility="Safe data/schema migration strategies, expand-contract patterns, rollback plans",
        access=RoleAccess.READ_WRITE,
        agent_file="migration_engineer.md",
    ),
    AgentRole.UX_SPECIFIER: RoleDefinition(
        role=AgentRole.UX_SPECIFIER,
        title="UX Specifier",
        responsibility="Translates requirements into UI specs: flows, states, components, interactions",
        access=RoleAccess.READ_ONLY,
        agent_file="ux_specifier.md",
    ),
    AgentRole.TECH_DEBT_ASSESSOR: RoleDefinition(
        role=AgentRole.TECH_DEBT_ASSESSOR,
        title="Tech Debt Assessor",
        responsibility="Analyzes codebase health, quantifies technical debt, prioritizes remediation",
        access=RoleAccess.READ_ONLY,
        agent_file="tech_debt_assessor.md",
    ),
    AgentRole.RELEASE_ENGINEER: RoleDefinition(
        role=AgentRole.RELEASE_ENGINEER,
        title="Release Engineer",
        responsibility="Versioning, changelogs, release notes, staged rollout plans, feature flags",
        access=RoleAccess.READ_WRITE,
        agent_file="release_engineer.md",
    ),
    AgentRole.INCIDENT_ANALYST: RoleDefinition(
        role=AgentRole.INCIDENT_ANALYST,
        title="Incident Analyst",
        responsibility="Root cause analysis, bug reproduction, incident reports for bugfix workflow",
        access=RoleAccess.READ_ONLY,
        agent_file="incident_analyst.md",
    ),
    AgentRole.LOAD_TEST_ENGINEER: RoleDefinition(
        role=AgentRole.LOAD_TEST_ENGINEER,
        title="Load Test Engineer",
        responsibility="Load/stress/soak testing, capacity planning, concurrency bug detection",
        access=RoleAccess.READ_ONLY,
        agent_file="load_test_engineer.md",
    ),
    AgentRole.COMPLIANCE_AUDITOR: RoleDefinition(
        role=AgentRole.COMPLIANCE_AUDITOR,
        title="Compliance Auditor",
        responsibility="GDPR, CCPA, HIPAA, SOC 2 compliance evaluation and gap analysis",
        access=RoleAccess.READ_ONLY,
        agent_file="compliance_auditor.md",
    ),
    AgentRole.DEPENDENCY_AUDITOR: RoleDefinition(
        role=AgentRole.DEPENDENCY_AUDITOR,
        title="Dependency Auditor",
        responsibility="Dependency CVE scanning, license analysis, maintenance risk assessment",
        access=RoleAccess.READ_ONLY,
        agent_file="dependency_auditor.md",
    ),
    AgentRole.ACCESSIBILITY_AUDITOR: RoleDefinition(
        role=AgentRole.ACCESSIBILITY_AUDITOR,
        title="Accessibility Auditor",
        responsibility="Deep WCAG 2.1 AA/AAA audit, ARIA patterns, screen reader, keyboard navigation",
        access=RoleAccess.READ_ONLY,
        agent_file="accessibility_auditor.md",
    ),
    AgentRole.INTEGRATION_TEST_ENGINEER: RoleDefinition(
        role=AgentRole.INTEGRATION_TEST_ENGINEER,
        title="Integration Test Engineer",
        responsibility="Contract tests, boundary tests, end-to-end scenario tests across components",
        access=RoleAccess.READ_WRITE,
        agent_file="integration_test_engineer.md",
    ),
    AgentRole.LEGAL_ADVISOR: RoleDefinition(
        role=AgentRole.LEGAL_ADVISOR,
        title="Legal Advisor",
        responsibility="Legal risk identification: privacy, IP, licensing, liability, jurisdiction",
        access=RoleAccess.READ_ONLY,
        agent_file="legal_advisor.md",
    ),
    AgentRole.USER_BEHAVIOR_PSYCHOLOGIST: RoleDefinition(
        role=AgentRole.USER_BEHAVIOR_PSYCHOLOGIST,
        title="User Behavior Psychologist",
        responsibility="Cognitive load analysis, dark pattern detection, UX friction and motivation review",
        access=RoleAccess.READ_ONLY,
        agent_file="user_behavior_psychologist.md",
    ),
    # --- Cloud & infrastructure specialists ---
    AgentRole.CICD_SPECIALIST: RoleDefinition(
        role=AgentRole.CICD_SPECIALIST,
        title="CI/CD Pipeline Specialist",
        responsibility="Pipeline architecture, parallelization, caching, matrix builds, quality gates",
        access=RoleAccess.READ_WRITE,
        agent_file="cicd_specialist.md",
    ),
    AgentRole.AWS_SPECIALIST: RoleDefinition(
        role=AgentRole.AWS_SPECIALIST,
        title="AWS Specialist",
        responsibility="AWS service selection, Well-Architected design, IaC (CDK/CloudFormation)",
        access=RoleAccess.READ_WRITE,
        agent_file="aws_specialist.md",
    ),
    AgentRole.AZURE_SPECIALIST: RoleDefinition(
        role=AgentRole.AZURE_SPECIALIST,
        title="Azure Specialist",
        responsibility="Azure service selection, Well-Architected design, IaC (Bicep/ARM)",
        access=RoleAccess.READ_WRITE,
        agent_file="azure_specialist.md",
    ),
    AgentRole.GCP_SPECIALIST: RoleDefinition(
        role=AgentRole.GCP_SPECIALIST,
        title="GCP Specialist",
        responsibility="GCP service selection, architecture design, IaC (Terraform/Deployment Manager)",
        access=RoleAccess.READ_WRITE,
        agent_file="gcp_specialist.md",
    ),
    AgentRole.RUNPOD_SPECIALIST: RoleDefinition(
        role=AgentRole.RUNPOD_SPECIALIST,
        title="RunPod Specialist",
        responsibility="GPU compute provisioning, serverless endpoints, ML training/inference infrastructure",
        access=RoleAccess.READ_WRITE,
        agent_file="runpod_specialist.md",
    ),
    # --- AI/ML specialists ---
    AgentRole.LLM_SPECIALIST: RoleDefinition(
        role=AgentRole.LLM_SPECIALIST,
        title="LLM Specialist",
        responsibility="LLM integration design: prompt engineering, RAG, model selection, evaluation, guardrails",
        access=RoleAccess.READ_ONLY,
        agent_file="llm_specialist.md",
    ),
    AgentRole.AGENTIC_AI_SPECIALIST: RoleDefinition(
        role=AgentRole.AGENTIC_AI_SPECIALIST,
        title="Agentic AI Specialist",
        responsibility="AI agent architecture: multi-agent orchestration, tool use, memory, guardrails, autonomy levels",
        access=RoleAccess.READ_ONLY,
        agent_file="agentic_ai_specialist.md",
    ),
    AgentRole.ML_SPECIALIST: RoleDefinition(
        role=AgentRole.ML_SPECIALIST,
        title="ML Algorithm Specialist",
        responsibility="ML pipeline design: data preprocessing, model selection, training, evaluation, production serving",
        access=RoleAccess.READ_ONLY,
        agent_file="ml_specialist.md",
    ),
}


def get_role(role: AgentRole) -> RoleDefinition:
    """Get the definition for a role."""
    return ROLE_REGISTRY[role]


def get_role_agent_file(role: AgentRole) -> Path | None:
    """Get the agent .md file path for a role, or None if it doesn't exist."""
    defn = ROLE_REGISTRY[role]
    if defn.agent_file is None:
        return None
    path = AGENTS_DIR / defn.agent_file
    return path if path.exists() else None


def validate_role_access(role: AgentRole, is_write: bool) -> bool:
    """Check if a role is allowed to perform the given action.

    Returns True if the action is permitted, False if it violates role boundaries.
    """
    defn = ROLE_REGISTRY[role]
    if is_write and defn.access == RoleAccess.READ_ONLY:
        return False
    return True


def get_roles_for_workflow(workflow: WorkflowDefinition) -> set[AgentRole]:
    """Return only the roles needed for a given workflow."""
    return {step.agent_role for step in workflow.steps}


def role_to_legacy_agent_name(role: AgentRole) -> str:
    """Map an AgentRole to the legacy agent name used in config and agent files.

    This bridges the old 5-agent system to the new 17-role system.
    """
    mapping = {
        AgentRole.PRODUCT_MANAGER: "pm",
        AgentRole.SOFTWARE_ARCHITECT: "architect",
        AgentRole.PRINCIPAL_ENGINEER: "principal_engineer",
        AgentRole.TECHNICAL_PROJECT_MANAGER: "tpm",
        AgentRole.FRONTEND_ENGINEER: "frontend_engineer",
        AgentRole.BACKEND_ENGINEER: "backend_engineer",
        AgentRole.DATABASE_ENGINEER: "database_engineer",
        AgentRole.CACHING_PERFORMANCE_ENGINEER: "caching_engineer",
        AgentRole.BACKEND_CODE_REVIEWER: "backend_reviewer",
        AgentRole.FRONTEND_CODE_REVIEWER: "frontend_reviewer",
        AgentRole.QA_PLANNER: "qa_planner",
        AgentRole.QA_EXECUTOR: "qa_executor",
        AgentRole.AUTOMATION_ENGINEER: "automation_engineer",
        AgentRole.DEVOPS_ENGINEER: "devops_engineer",
        AgentRole.SECURITY_ENGINEER: "security_engineer",
        AgentRole.OBSERVABILITY_ENGINEER: "observability_engineer",
        AgentRole.DOCUMENTATION_ENGINEER: "documentation_engineer",
        AgentRole.GIT_MANAGER: "git_manager",
        # New specialist roles
        AgentRole.API_CONTRACT_DESIGNER: "api_contract_designer",
        AgentRole.MIGRATION_ENGINEER: "migration_engineer",
        AgentRole.UX_SPECIFIER: "ux_specifier",
        AgentRole.TECH_DEBT_ASSESSOR: "tech_debt_assessor",
        AgentRole.RELEASE_ENGINEER: "release_engineer",
        AgentRole.INCIDENT_ANALYST: "incident_analyst",
        AgentRole.LOAD_TEST_ENGINEER: "load_test_engineer",
        AgentRole.COMPLIANCE_AUDITOR: "compliance_auditor",
        AgentRole.DEPENDENCY_AUDITOR: "dependency_auditor",
        AgentRole.ACCESSIBILITY_AUDITOR: "accessibility_auditor",
        AgentRole.INTEGRATION_TEST_ENGINEER: "integration_test_engineer",
        AgentRole.LEGAL_ADVISOR: "legal_advisor",
        AgentRole.USER_BEHAVIOR_PSYCHOLOGIST: "user_behavior_psychologist",
        # Cloud & infrastructure specialists
        AgentRole.CICD_SPECIALIST: "cicd_specialist",
        AgentRole.AWS_SPECIALIST: "aws_specialist",
        AgentRole.AZURE_SPECIALIST: "azure_specialist",
        AgentRole.GCP_SPECIALIST: "gcp_specialist",
        AgentRole.RUNPOD_SPECIALIST: "runpod_specialist",
        # AI/ML specialists
        AgentRole.LLM_SPECIALIST: "llm_specialist",
        AgentRole.AGENTIC_AI_SPECIALIST: "agentic_ai_specialist",
        AgentRole.ML_SPECIALIST: "ml_specialist",
    }
    return mapping[role]
