"""Phase definitions and prompt builders for all agent roles."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from orchestrator.models import AgentRole, OrchestratorConfig, TaskList


@dataclass
class PhaseDefinition:
    """Definition of an orchestration phase (legacy — kept for backward compat)."""

    name: str
    agent_name: str
    build_prompt: Any  # Callable
    output_artifacts: list[str]
    parallel: bool = False


# ---------------------------------------------------------------------------
# Prompt builders — one per AgentRole
# ---------------------------------------------------------------------------

def build_pm_prompt(
    feature_request: str, workspace: Path, config: OrchestratorConfig,
    task_data: dict[str, Any] | None = None,
) -> str:
    artifacts_dir = workspace / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    return f"""You are the Product Manager for this project.

## Feature Request

{feature_request}

## Instructions

Analyze this feature request and produce a comprehensive Product Requirements Document.

Write your output as valid JSON to: {artifacts_dir}/prd.json

The JSON must include:
- "title": Feature title
- "overview": Detailed overview (at least 50 characters)
- "goals": Array of goals
- "requirements": Array of {{"id": "REQ-001", "description": "...", "priority": "must|should|could"}}
- "constraints": Array of constraints
- "acceptance_criteria": Array of acceptance criteria

First, explore the existing codebase to understand the project context, then write the PRD."""


def build_architect_prompt(
    feature_request: str, workspace: Path, config: OrchestratorConfig,
    task_data: dict[str, Any] | None = None,
) -> str:
    artifacts_dir = workspace / "artifacts"
    prd_path = artifacts_dir / "prd.json"

    return f"""You are the System Architect for this project.

## Feature Request

{feature_request}

## PRD

Read the PRD from: {prd_path}

## Instructions

Based on the PRD, design the system architecture and break the work into implementable tasks.

Write TWO output files:

1. {artifacts_dir}/architecture.json — System architecture with components, data_flow, tech_decisions, constraints
2. {artifacts_dir}/tasks.json — Task breakdown with task_id (TASK-NNN), title, description, assigned_role, dependencies, acceptance_criteria, files_to_modify, estimated_complexity

First explore the codebase, then read the PRD, then produce both artifacts."""


def build_principal_engineer_prompt(
    feature_request: str, workspace: Path, config: OrchestratorConfig,
    task_data: dict[str, Any] | None = None,
) -> str:
    artifacts_dir = workspace / "artifacts"

    return f"""You are the Principal Engineer for this project.

## Feature Request

{feature_request}

## Input Artifacts

- PRD: {artifacts_dir}/prd.json
- Architecture: {artifacts_dir}/architecture.json

## Instructions

Translate the architecture into a concrete engineering strategy and implementation plan.

1. Read the PRD and architecture documents
2. Explore the existing codebase to understand current patterns and constraints
3. Define the engineering strategy: implementation order, risk areas, testing approach
4. Break the architecture into an ordered implementation plan

Write your output as valid JSON to: {artifacts_dir}/engineering_plan.json

The JSON must include:
- "strategy": Overall engineering approach (at least 20 characters)
- "implementation_order": Array of ordered implementation steps
- "risk_areas": Array of identified risks
- "testing_strategy": How to test the implementation (at least 10 characters)

Also update: {artifacts_dir}/tasks.json with refined task breakdown if needed.

IMPORTANT: Do NOT modify any code files. You are read-only."""


def build_tpm_prompt(
    feature_request: str, workspace: Path, config: OrchestratorConfig,
    task_data: dict[str, Any] | None = None,
) -> str:
    artifacts_dir = workspace / "artifacts"

    return f"""You are the Technical Project Manager for this project.

## Feature Request

{feature_request}

## Input Artifacts

- PRD: {artifacts_dir}/prd.json
- Architecture: {artifacts_dir}/architecture.json
- Engineering Plan: {artifacts_dir}/engineering_plan.json

## Instructions

Break the work into tiny, independent, precisely-scoped tasks.

1. Read all input artifacts
2. Decompose each engineering plan item into the smallest possible independent tasks
3. Each task must have clear acceptance criteria, file targets, and dependencies
4. Assign roles: frontend_engineer, backend_engineer, database_engineer, etc.
5. Order tasks by dependency — no task should start before its dependencies complete

Write your output as valid JSON to: {artifacts_dir}/tasks.json

Each task must have:
- "task_id": "TASK-NNN"
- "title": Short descriptive title
- "description": What needs to be done (at least 10 characters)
- "assigned_role": One of engineer, frontend_engineer, backend_engineer, database_engineer, etc.
- "dependencies": Array of task_ids this depends on
- "acceptance_criteria": Array of testable conditions (at least 1)
- "files_to_modify": Array of file paths
- "estimated_complexity": "low" | "medium" | "high"

IMPORTANT: Do NOT modify any code files. You are read-only."""


def build_frontend_engineer_prompt(
    feature_request: str, workspace: Path, config: OrchestratorConfig,
    task_data: dict[str, Any] | None = None,
) -> str:
    artifacts_dir = workspace / "artifacts"
    task_section = _build_task_section(task_data, artifacts_dir, "frontend_engineer")

    return f"""You are a Frontend Engineer for this project.

## Feature Request

{feature_request}

{task_section}

## Context

- PRD: {artifacts_dir}/prd.json
- Architecture: {artifacts_dir}/architecture.json
- Full task list: {artifacts_dir}/tasks.json

## Instructions

1. Read the PRD, architecture, and your assigned task(s)
2. Explore the existing codebase for UI patterns, component conventions, and styling
3. Implement the frontend task according to the architecture design
4. Write tests for your components (unit + integration)
5. Ensure accessibility (ARIA labels, keyboard navigation)
6. Follow existing component patterns and styling conventions

Focus only on your assigned task. Do not scope-creep."""


def build_backend_engineer_prompt(
    feature_request: str, workspace: Path, config: OrchestratorConfig,
    task_data: dict[str, Any] | None = None,
) -> str:
    artifacts_dir = workspace / "artifacts"
    task_section = _build_task_section(task_data, artifacts_dir, "backend_engineer")

    return f"""You are a Backend Engineer for this project.

## Feature Request

{feature_request}

{task_section}

## Context

- PRD: {artifacts_dir}/prd.json
- Architecture: {artifacts_dir}/architecture.json
- Full task list: {artifacts_dir}/tasks.json

## Instructions

1. Read the PRD, architecture, and your assigned task(s)
2. Explore the existing codebase for patterns and conventions
3. Implement the backend task according to the architecture design
4. Write tests for your changes (unit + integration)
5. Ensure proper error handling and input validation
6. Follow existing code patterns and conventions

Focus only on your assigned task. Do not scope-creep."""


def build_engineer_prompt(
    feature_request: str, workspace: Path, config: OrchestratorConfig,
    task_data: dict[str, Any] | None = None,
) -> str:
    """Build the prompt for a generic Engineer agent (legacy + fallback)."""
    artifacts_dir = workspace / "artifacts"
    task_section = _build_task_section(task_data, artifacts_dir, "engineer")

    return f"""You are a Software Engineer for this project.

## Feature Request

{feature_request}

{task_section}

## Context

- PRD: {artifacts_dir}/prd.json
- Architecture: {artifacts_dir}/architecture.json
- Full task list: {artifacts_dir}/tasks.json

## Instructions

1. Read the PRD, architecture, and your assigned task(s)
2. Explore the existing codebase for patterns and conventions
3. Implement the task according to the architecture design
4. Write tests for your changes
5. Ensure code quality (formatting, naming, no obvious bugs)

Focus only on your assigned task. Do not scope-creep."""


def build_database_engineer_prompt(
    feature_request: str, workspace: Path, config: OrchestratorConfig,
    task_data: dict[str, Any] | None = None,
) -> str:
    artifacts_dir = workspace / "artifacts"
    task_section = _build_task_section(task_data, artifacts_dir, "database_engineer")

    return f"""You are the Database Engineer for this project.

## Feature Request

{feature_request}

{task_section}

## Context

- PRD: {artifacts_dir}/prd.json
- Architecture: {artifacts_dir}/architecture.json
- Full task list: {artifacts_dir}/tasks.json

## Instructions

1. Read the PRD, architecture, and your assigned task(s)
2. Explore the existing database schema, migrations, and query patterns
3. Design/modify schemas following normalization best practices
4. Create migrations that are safe to run and rollback
5. Optimize indexes for query patterns identified in the architecture
6. Write tests for data integrity constraints

Focus only on your assigned task. Do not scope-creep."""


def build_caching_engineer_prompt(
    feature_request: str, workspace: Path, config: OrchestratorConfig,
    task_data: dict[str, Any] | None = None,
) -> str:
    artifacts_dir = workspace / "artifacts"
    task_section = _build_task_section(task_data, artifacts_dir, "caching_performance_engineer")

    return f"""You are the Caching & Performance Engineer for this project.

## Feature Request

{feature_request}

{task_section}

## Context

- PRD: {artifacts_dir}/prd.json
- Architecture: {artifacts_dir}/architecture.json
- Full task list: {artifacts_dir}/tasks.json

## Instructions

1. Read the PRD, architecture, and your assigned task(s)
2. Profile the application to identify performance bottlenecks
3. Design caching strategies (cache keys, TTL, invalidation)
4. Implement performance optimizations
5. Write benchmarks to validate improvements
6. Document cache invalidation patterns

Focus only on your assigned task. Do not scope-creep."""


def build_backend_reviewer_prompt(
    feature_request: str, workspace: Path, config: OrchestratorConfig,
    task_data: dict[str, Any] | None = None,
    review_cycle: int = 1,
    previous_review: dict[str, Any] | None = None,
) -> str:
    artifacts_dir = workspace / "artifacts"
    cycle_context = _build_review_cycle_context(review_cycle, previous_review)

    return f"""You are the Backend Code Reviewer for this project.

## Feature Request

{feature_request}

{cycle_context}

## Instructions

Review the backend implementation for quality, correctness, and architecture adherence:

1. Read all artifacts:
   - PRD: {artifacts_dir}/prd.json
   - Architecture: {artifacts_dir}/architecture.json
   - Tasks: {artifacts_dir}/tasks.json
   - QA Report: {artifacts_dir}/qa_report.json (if exists)
2. Review all backend code changes
3. Evaluate: correctness, architecture adherence, code quality, security (OWASP), performance, test coverage
4. Check error handling, input validation, SQL injection, auth boundaries

Write your review as valid JSON to: {artifacts_dir}/review.json

The review must include:
- "verdict": "approve" | "reject" | "request_changes"
- "issues": array of {{"severity": "critical|major|minor|nit", "file": "...", "description": "...", "suggestion": "..."}}
- "summary": Overall assessment (at least 20 characters)

IMPORTANT: Do NOT modify any code files. You are read-only."""


def build_frontend_reviewer_prompt(
    feature_request: str, workspace: Path, config: OrchestratorConfig,
    task_data: dict[str, Any] | None = None,
    review_cycle: int = 1,
    previous_review: dict[str, Any] | None = None,
) -> str:
    artifacts_dir = workspace / "artifacts"
    cycle_context = _build_review_cycle_context(review_cycle, previous_review)

    return f"""You are the Frontend Code Reviewer for this project.

## Feature Request

{feature_request}

{cycle_context}

## Instructions

Review the frontend implementation for UI correctness, accessibility, and component architecture:

1. Read all artifacts:
   - PRD: {artifacts_dir}/prd.json
   - Architecture: {artifacts_dir}/architecture.json
   - Tasks: {artifacts_dir}/tasks.json
2. Review all frontend code changes
3. Evaluate: component architecture, accessibility (WCAG), responsive design, state management, performance
4. Check for XSS vulnerabilities, proper input sanitization

Write your review as valid JSON to: {artifacts_dir}/review.json

The review must include:
- "verdict": "approve" | "reject" | "request_changes"
- "issues": array of {{"severity": "critical|major|minor|nit", "file": "...", "description": "...", "suggestion": "..."}}
- "summary": Overall assessment (at least 20 characters)

IMPORTANT: Do NOT modify any code files. You are read-only."""


def build_qa_planner_prompt(
    feature_request: str, workspace: Path, config: OrchestratorConfig,
    task_data: dict[str, Any] | None = None,
) -> str:
    artifacts_dir = workspace / "artifacts"

    return f"""You are the QA Engineer (Planner) for this project.

## Feature Request

{feature_request}

## Input Artifacts

- PRD: {artifacts_dir}/prd.json
- Architecture: {artifacts_dir}/architecture.json
- Tasks: {artifacts_dir}/tasks.json

## Instructions

Design a comprehensive test strategy:

1. Read the PRD and task list
2. Identify all testable requirements and acceptance criteria
3. Design test cases covering happy paths, edge cases, and error scenarios
4. Plan integration test scenarios
5. Identify areas needing security testing
6. Define coverage targets

Write your output as valid JSON to: {artifacts_dir}/prd.json (update with test criteria)

Also produce bug analysis output for bugfix workflows if applicable.

IMPORTANT: Do NOT modify any code files. You are read-only."""


def build_qa_executor_prompt(
    feature_request: str, workspace: Path, config: OrchestratorConfig,
    task_data: dict[str, Any] | None = None,
) -> str:
    artifacts_dir = workspace / "artifacts"

    return f"""You are the QA Engineer (Executor) for this project.

## Feature Request

{feature_request}

## Instructions

Validate the implementation against the requirements:

1. Read the PRD: {artifacts_dir}/prd.json
2. Read the task list: {artifacts_dir}/tasks.json
3. Run the test suite (find and execute the appropriate test command)
4. Run linters if configured
5. Run type checkers if configured
6. Review code for bugs, security issues, missing edge cases
7. Check every acceptance criterion from the PRD

Write your report as valid JSON to: {artifacts_dir}/qa_report.json

The JSON must have EXACTLY these fields — no extras:
```json
{{
  "test_results": {{"passed": 0, "failed": 0, "skipped": 0}},
  "lint_clean": true,
  "type_check_clean": true,
  "issues": [
    {{"severity": "critical|major|minor", "file": "path/to/file.py", "line": 42, "description": "...", "suggestion": "..."}}
  ],
  "verdict": "pass"
}}
```

Rules:
- `test_results` has ONLY "passed", "failed", "skipped" (all integers) — no other keys
- `lint_clean` / `type_check_clean`: use true/false; use null ONLY if the tool is not configured
- `issues[].line`: integer or omit — never null
- `verdict`: exactly "pass" or "fail"
- Do NOT add any extra top-level fields

IMPORTANT: Do NOT modify any code files. You are read-only."""


def build_qa_prompt(
    feature_request: str, workspace: Path, config: OrchestratorConfig,
    task_data: dict[str, Any] | None = None,
) -> str:
    """Legacy QA prompt — delegates to qa_executor."""
    return build_qa_executor_prompt(feature_request, workspace, config, task_data)


def build_reviewer_prompt(
    feature_request: str, workspace: Path, config: OrchestratorConfig,
    task_data: dict[str, Any] | None = None,
    review_cycle: int = 1,
    previous_review: dict[str, Any] | None = None,
) -> str:
    """Legacy reviewer prompt — delegates to backend_reviewer."""
    return build_backend_reviewer_prompt(
        feature_request, workspace, config, task_data, review_cycle, previous_review
    )


def build_automation_engineer_prompt(
    feature_request: str, workspace: Path, config: OrchestratorConfig,
    task_data: dict[str, Any] | None = None,
) -> str:
    artifacts_dir = workspace / "artifacts"
    task_section = _build_task_section(task_data, artifacts_dir, "automation_engineer")

    return f"""You are the Automation Engineer for this project.

## Feature Request

{feature_request}

{task_section}

## Context

- PRD: {artifacts_dir}/prd.json
- Architecture: {artifacts_dir}/architecture.json
- Tasks: {artifacts_dir}/tasks.json

## Instructions

1. Read the PRD and architecture to understand testing requirements
2. Build automated test suites (unit, integration, e2e as appropriate)
3. Configure CI pipeline stages
4. Set up test data fixtures and factories
5. Ensure tests are deterministic and parallelizable

Focus only on your assigned task. Do not scope-creep."""


def build_devops_prompt(
    feature_request: str, workspace: Path, config: OrchestratorConfig,
    task_data: dict[str, Any] | None = None,
) -> str:
    artifacts_dir = workspace / "artifacts"

    return f"""You are the DevOps Engineer for this project.

## Feature Request

{feature_request}

## Context

- PRD: {artifacts_dir}/prd.json
- Architecture: {artifacts_dir}/architecture.json
- QA Report: {artifacts_dir}/qa_report.json
- Review: {artifacts_dir}/review.json

## Instructions

1. Read all artifacts to understand the deployment requirements
2. Configure CI/CD pipeline if not present
3. Set up containerization (Dockerfile, docker-compose) if needed
4. Configure deployment scripts
5. Ensure health checks and rollback procedures are in place

Focus only on deployment and infrastructure. Do not modify application code."""


def build_security_engineer_prompt(
    feature_request: str, workspace: Path, config: OrchestratorConfig,
    task_data: dict[str, Any] | None = None,
) -> str:
    artifacts_dir = workspace / "artifacts"

    return f"""You are the Security Engineer for this project.

## Feature Request

{feature_request}

## Context

- PRD: {artifacts_dir}/prd.json
- Architecture: {artifacts_dir}/architecture.json (if exists)
- Threat Model: {artifacts_dir}/threat_model.json (if exists)

## Instructions

1. Read available artifacts
2. Perform a security review of the architecture and code
3. Identify threats (STRIDE model), vulnerabilities (OWASP Top 10), and attack surface
4. For threat modeling: output to {artifacts_dir}/threat_model.json
5. For vulnerability scanning: output to {artifacts_dir}/vulnerability_report.json

Threat model JSON format:
- "threats": [{{"id": "THREAT-NNN", "description": "...", "severity": "critical|major|minor", "mitigation": "..."}}]
- "attack_surface": Description of attack surface (at least 20 chars)
- "recommendations": Array of recommendations

Vulnerability report JSON format:
- "vulnerabilities": [{{"id": "...", "severity": "critical|major|minor", "file": "...", "description": "...", "fix": "..."}}]
- "scan_tools_used": Array of tool names
- "summary": Overall assessment (at least 20 chars)

IMPORTANT: Do NOT modify any code files. You are read-only."""


def build_observability_prompt(
    feature_request: str, workspace: Path, config: OrchestratorConfig,
    task_data: dict[str, Any] | None = None,
) -> str:
    artifacts_dir = workspace / "artifacts"
    task_section = _build_task_section(task_data, artifacts_dir, "observability_engineer")

    return f"""You are the Observability Engineer for this project.

## Feature Request

{feature_request}

{task_section}

## Context

- PRD: {artifacts_dir}/prd.json
- Architecture: {artifacts_dir}/architecture.json

## Instructions

1. Read the PRD and architecture
2. Add structured logging to key code paths
3. Set up metrics collection (counters, gauges, histograms)
4. Configure health check endpoints
5. Add distributed tracing if applicable
6. Follow the project's existing logging conventions

Focus only on observability. Do not modify application logic."""


def build_documentation_prompt(
    feature_request: str, workspace: Path, config: OrchestratorConfig,
    task_data: dict[str, Any] | None = None,
) -> str:
    artifacts_dir = workspace / "artifacts"

    return f"""You are the Documentation Engineer for this project.

## Feature Request

{feature_request}

## Context

- PRD: {artifacts_dir}/prd.json
- Architecture: {artifacts_dir}/architecture.json
- Tasks: {artifacts_dir}/tasks.json

## Instructions

1. Read all artifacts to understand what was built
2. Write/update technical documentation:
   - API docs (endpoints, parameters, responses)
   - Architecture decision records
   - Developer setup guide
   - Deployment instructions
3. Follow existing documentation patterns and conventions
4. Keep docs close to the code they describe

Focus only on documentation. Do not modify application code."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_task_section(task_data: dict[str, Any] | None, artifacts_dir: Path, role: str) -> str:
    """Build the task assignment section of a prompt."""
    if task_data:
        return f"""## Your Assigned Task

```json
{json.dumps(task_data, indent=2)}
```"""
    return f"""## Tasks

Read the task breakdown from: {artifacts_dir}/tasks.json
Implement all tasks assigned to the {role} role."""


def _build_review_cycle_context(review_cycle: int, previous_review: dict[str, Any] | None) -> str:
    """Build review cycle context for reviewer prompts."""
    if review_cycle > 1 and previous_review:
        return f"""## Previous Review (Cycle {review_cycle - 1})

The previous review requested changes. Here was the feedback:

```json
{json.dumps(previous_review, indent=2)}
```

Focus on whether the issues from the previous review have been addressed."""
    return ""


def get_engineer_tasks(workspace: Path) -> list[dict[str, Any]]:
    """Load and return individual engineer tasks from the tasks artifact."""
    tasks_path = workspace / "artifacts" / "tasks.json"
    if not tasks_path.exists():
        return []

    with open(tasks_path) as f:
        data = json.load(f)

    engineer_roles = {"engineer", "frontend_engineer", "backend_engineer",
                      "database_engineer", "caching_performance_engineer"}
    return [t for t in data.get("tasks", []) if t.get("assigned_role") in engineer_roles]


# ---------------------------------------------------------------------------
# Prompt builder registry — maps AgentRole to prompt builder function
# ---------------------------------------------------------------------------

PROMPT_BUILDERS: dict[AgentRole, Callable[..., str]] = {
    AgentRole.PRODUCT_MANAGER: build_pm_prompt,
    AgentRole.SOFTWARE_ARCHITECT: build_architect_prompt,
    AgentRole.PRINCIPAL_ENGINEER: build_principal_engineer_prompt,
    AgentRole.TECHNICAL_PROJECT_MANAGER: build_tpm_prompt,
    AgentRole.FRONTEND_ENGINEER: build_frontend_engineer_prompt,
    AgentRole.BACKEND_ENGINEER: build_backend_engineer_prompt,
    AgentRole.DATABASE_ENGINEER: build_database_engineer_prompt,
    AgentRole.CACHING_PERFORMANCE_ENGINEER: build_caching_engineer_prompt,
    AgentRole.BACKEND_CODE_REVIEWER: build_backend_reviewer_prompt,
    AgentRole.FRONTEND_CODE_REVIEWER: build_frontend_reviewer_prompt,
    AgentRole.QA_PLANNER: build_qa_planner_prompt,
    AgentRole.QA_EXECUTOR: build_qa_executor_prompt,
    AgentRole.AUTOMATION_ENGINEER: build_automation_engineer_prompt,
    AgentRole.DEVOPS_ENGINEER: build_devops_prompt,
    AgentRole.SECURITY_ENGINEER: build_security_engineer_prompt,
    AgentRole.OBSERVABILITY_ENGINEER: build_observability_prompt,
    AgentRole.DOCUMENTATION_ENGINEER: build_documentation_prompt,
}


# ---------------------------------------------------------------------------
# Legacy phase registry (backward compat)
# ---------------------------------------------------------------------------

PHASE_DEFINITIONS: dict[str, PhaseDefinition] = {
    "pm": PhaseDefinition(
        name="pm",
        agent_name="pm",
        build_prompt=build_pm_prompt,
        output_artifacts=["prd"],
    ),
    "architect": PhaseDefinition(
        name="architect",
        agent_name="architect",
        build_prompt=build_architect_prompt,
        output_artifacts=["architecture", "tasks"],
    ),
    "engineer": PhaseDefinition(
        name="engineer",
        agent_name="engineer",
        build_prompt=build_engineer_prompt,
        output_artifacts=[],
        parallel=True,
    ),
    "qa": PhaseDefinition(
        name="qa",
        agent_name="qa",
        build_prompt=build_qa_prompt,
        output_artifacts=["qa_report"],
    ),
    "reviewer": PhaseDefinition(
        name="reviewer",
        agent_name="reviewer",
        build_prompt=build_reviewer_prompt,
        output_artifacts=["review"],
    ),
}
