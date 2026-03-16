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
    }
    return mapping[role]
