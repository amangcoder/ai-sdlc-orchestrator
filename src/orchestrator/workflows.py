"""Built-in workflow templates and custom workflow parsing."""

from __future__ import annotations

import re
from typing import Any

from orchestrator.models import (
    AgentRole,
    WorkflowDefinition,
    WorkflowStepDefinition,
    WorkflowType,
)


# ---------------------------------------------------------------------------
# Built-in workflow templates
# ---------------------------------------------------------------------------

FEATURE_DEVELOPMENT = WorkflowDefinition(
    name="Feature Development",
    workflow_type=WorkflowType.FEATURE_DEVELOPMENT,
    steps=[
        WorkflowStepDefinition(
            name="PRD",
            agent_role=AgentRole.PRODUCT_MANAGER,
            inputs=[],
            outputs=["prd"],
            next="Architecture",
        ),
        WorkflowStepDefinition(
            name="Architecture",
            agent_role=AgentRole.SOFTWARE_ARCHITECT,
            inputs=["prd"],
            outputs=["architecture"],
            next="Engineering Plan",
        ),
        WorkflowStepDefinition(
            name="Engineering Plan",
            agent_role=AgentRole.PRINCIPAL_ENGINEER,
            inputs=["prd", "architecture"],
            outputs=["engineering_plan", "tasks"],
            next="Task Breakdown",
        ),
        WorkflowStepDefinition(
            name="Task Breakdown",
            agent_role=AgentRole.TECHNICAL_PROJECT_MANAGER,
            inputs=["prd", "architecture", "engineering_plan"],
            outputs=["tasks"],
            next="Implementation",
        ),
        WorkflowStepDefinition(
            name="Implementation",
            agent_role=AgentRole.BACKEND_ENGINEER,
            inputs=["prd", "architecture", "tasks"],
            outputs=[],
            next="Code Review",
            parallel=True,
        ),
        WorkflowStepDefinition(
            name="Code Review",
            agent_role=AgentRole.BACKEND_CODE_REVIEWER,
            inputs=["prd", "architecture", "tasks"],
            outputs=["review"],
            next="QA",
            on_fail="Implementation",
        ),
        WorkflowStepDefinition(
            name="QA",
            agent_role=AgentRole.QA_EXECUTOR,
            inputs=["prd", "tasks"],
            outputs=["qa_report"],
            next="Release",
            on_fail="Implementation",
        ),
        WorkflowStepDefinition(
            name="Release",
            agent_role=AgentRole.DEVOPS_ENGINEER,
            inputs=["qa_report", "review"],
            outputs=[],
            gate="approval",
        ),
    ],
)

BUGFIX = WorkflowDefinition(
    name="Bugfix",
    workflow_type=WorkflowType.BUGFIX,
    steps=[
        WorkflowStepDefinition(
            name="Bug Analysis",
            agent_role=AgentRole.QA_PLANNER,
            inputs=[],
            outputs=["prd"],
            next="Root Cause",
        ),
        WorkflowStepDefinition(
            name="Root Cause",
            agent_role=AgentRole.PRINCIPAL_ENGINEER,
            inputs=["prd"],
            outputs=["architecture"],
            next="Fix Plan",
        ),
        WorkflowStepDefinition(
            name="Fix Plan",
            agent_role=AgentRole.SOFTWARE_ARCHITECT,
            inputs=["prd", "architecture"],
            outputs=["tasks"],
            next="Implementation",
        ),
        WorkflowStepDefinition(
            name="Implementation",
            agent_role=AgentRole.BACKEND_ENGINEER,
            inputs=["prd", "architecture", "tasks"],
            outputs=[],
            next="Code Review",
            parallel=True,
        ),
        WorkflowStepDefinition(
            name="Code Review",
            agent_role=AgentRole.BACKEND_CODE_REVIEWER,
            inputs=["prd", "tasks"],
            outputs=["review"],
            next="QA",
            on_fail="Implementation",
        ),
        WorkflowStepDefinition(
            name="QA",
            agent_role=AgentRole.QA_EXECUTOR,
            inputs=["prd", "tasks"],
            outputs=["qa_report"],
            on_fail="Implementation",
        ),
    ],
)

REFACTOR = WorkflowDefinition(
    name="Refactor",
    workflow_type=WorkflowType.REFACTOR,
    steps=[
        WorkflowStepDefinition(
            name="Scope Analysis",
            agent_role=AgentRole.PRINCIPAL_ENGINEER,
            inputs=[],
            outputs=["prd"],
            next="Architecture Review",
        ),
        WorkflowStepDefinition(
            name="Architecture Review",
            agent_role=AgentRole.SOFTWARE_ARCHITECT,
            inputs=["prd"],
            outputs=["architecture"],
            next="Task Breakdown",
        ),
        WorkflowStepDefinition(
            name="Task Breakdown",
            agent_role=AgentRole.TECHNICAL_PROJECT_MANAGER,
            inputs=["prd", "architecture"],
            outputs=["tasks"],
            next="Implementation",
        ),
        WorkflowStepDefinition(
            name="Implementation",
            agent_role=AgentRole.BACKEND_ENGINEER,
            inputs=["prd", "architecture", "tasks"],
            outputs=[],
            next="Code Review",
            parallel=True,
        ),
        WorkflowStepDefinition(
            name="Code Review",
            agent_role=AgentRole.BACKEND_CODE_REVIEWER,
            inputs=["prd", "architecture", "tasks"],
            outputs=["review"],
            next="QA",
            on_fail="Implementation",
        ),
        WorkflowStepDefinition(
            name="QA",
            agent_role=AgentRole.QA_EXECUTOR,
            inputs=["prd", "tasks"],
            outputs=["qa_report"],
            on_fail="Implementation",
        ),
    ],
)

PERFORMANCE_OPTIMIZATION = WorkflowDefinition(
    name="Performance Optimization",
    workflow_type=WorkflowType.PERFORMANCE_OPTIMIZATION,
    steps=[
        WorkflowStepDefinition(
            name="Profiling",
            agent_role=AgentRole.CACHING_PERFORMANCE_ENGINEER,
            inputs=[],
            outputs=["benchmark_report"],
            next="Bottleneck Analysis",
        ),
        WorkflowStepDefinition(
            name="Bottleneck Analysis",
            agent_role=AgentRole.PRINCIPAL_ENGINEER,
            inputs=["benchmark_report"],
            outputs=["prd"],
            next="Optimization Plan",
        ),
        WorkflowStepDefinition(
            name="Optimization Plan",
            agent_role=AgentRole.SOFTWARE_ARCHITECT,
            inputs=["prd", "benchmark_report"],
            outputs=["architecture", "tasks"],
            next="Implementation",
        ),
        WorkflowStepDefinition(
            name="Implementation",
            agent_role=AgentRole.CACHING_PERFORMANCE_ENGINEER,
            inputs=["prd", "architecture", "tasks"],
            outputs=[],
            next="Benchmarking",
            parallel=True,
        ),
        WorkflowStepDefinition(
            name="Benchmarking",
            agent_role=AgentRole.CACHING_PERFORMANCE_ENGINEER,
            inputs=["benchmark_report", "tasks"],
            outputs=["benchmark_report"],
            next="Code Review",
            on_fail="Implementation",
        ),
        WorkflowStepDefinition(
            name="Code Review",
            agent_role=AgentRole.BACKEND_CODE_REVIEWER,
            inputs=["prd", "architecture", "tasks", "benchmark_report"],
            outputs=["review"],
            on_fail="Implementation",
        ),
    ],
)

SECURITY_AUDIT = WorkflowDefinition(
    name="Security Audit",
    workflow_type=WorkflowType.SECURITY_AUDIT,
    steps=[
        WorkflowStepDefinition(
            name="Threat Model",
            agent_role=AgentRole.SECURITY_ENGINEER,
            inputs=[],
            outputs=["threat_model"],
            next="Code Scan",
        ),
        WorkflowStepDefinition(
            name="Code Scan",
            agent_role=AgentRole.SECURITY_ENGINEER,
            inputs=["threat_model"],
            outputs=["vulnerability_report"],
            next="Fix Plan",
        ),
        WorkflowStepDefinition(
            name="Fix Plan",
            agent_role=AgentRole.SOFTWARE_ARCHITECT,
            inputs=["threat_model", "vulnerability_report"],
            outputs=["tasks"],
            next="Implementation",
        ),
        WorkflowStepDefinition(
            name="Implementation",
            agent_role=AgentRole.BACKEND_ENGINEER,
            inputs=["threat_model", "vulnerability_report", "tasks"],
            outputs=[],
            next="Verification",
            parallel=True,
        ),
        WorkflowStepDefinition(
            name="Verification",
            agent_role=AgentRole.SECURITY_ENGINEER,
            inputs=["threat_model", "vulnerability_report", "tasks"],
            outputs=["vulnerability_report"],
            on_fail="Implementation",
        ),
    ],
)


# ---------------------------------------------------------------------------
# Workflow registry
# ---------------------------------------------------------------------------

BUILTIN_WORKFLOWS: dict[WorkflowType, WorkflowDefinition] = {
    WorkflowType.FEATURE_DEVELOPMENT: FEATURE_DEVELOPMENT,
    WorkflowType.BUGFIX: BUGFIX,
    WorkflowType.REFACTOR: REFACTOR,
    WorkflowType.PERFORMANCE_OPTIMIZATION: PERFORMANCE_OPTIMIZATION,
    WorkflowType.SECURITY_AUDIT: SECURITY_AUDIT,
}


def select_workflow(workflow_type: WorkflowType) -> WorkflowDefinition:
    """Return a built-in workflow by type."""
    if workflow_type == WorkflowType.CUSTOM:
        raise ValueError("Custom workflows must be loaded via parse_custom_workflow()")
    wf = BUILTIN_WORKFLOWS.get(workflow_type)
    if wf is None:
        raise ValueError(f"Unknown workflow type: {workflow_type}")
    return wf


# ---------------------------------------------------------------------------
# Custom workflow parser
# ---------------------------------------------------------------------------

# Matches the STEP: block format from the system prompt spec
_STEP_RE = re.compile(
    r"STEP:\s*(?P<name>.+?)$"
    r"(?P<body>.*?)(?=\nSTEP:|\Z)",
    re.MULTILINE | re.DOTALL,
)

_ROLE_MAP: dict[str, AgentRole] = {
    # Core roles
    "product manager": AgentRole.PRODUCT_MANAGER,
    "pm": AgentRole.PRODUCT_MANAGER,
    "software architect": AgentRole.SOFTWARE_ARCHITECT,
    "system architect": AgentRole.SOFTWARE_ARCHITECT,
    "architect": AgentRole.SOFTWARE_ARCHITECT,
    "principal engineer": AgentRole.PRINCIPAL_ENGINEER,
    "technical project manager": AgentRole.TECHNICAL_PROJECT_MANAGER,
    "tpm": AgentRole.TECHNICAL_PROJECT_MANAGER,
    # Implementation roles
    "frontend engineer": AgentRole.FRONTEND_ENGINEER,
    "backend engineer": AgentRole.BACKEND_ENGINEER,
    "database engineer": AgentRole.DATABASE_ENGINEER,
    "caching & performance engineer": AgentRole.CACHING_PERFORMANCE_ENGINEER,
    "caching engineer": AgentRole.CACHING_PERFORMANCE_ENGINEER,
    "performance engineer": AgentRole.CACHING_PERFORMANCE_ENGINEER,
    "automation engineer": AgentRole.AUTOMATION_ENGINEER,
    "devops engineer": AgentRole.DEVOPS_ENGINEER,
    "observability engineer": AgentRole.OBSERVABILITY_ENGINEER,
    # Review roles
    "backend code reviewer": AgentRole.BACKEND_CODE_REVIEWER,
    "backend reviewer": AgentRole.BACKEND_CODE_REVIEWER,
    "frontend code reviewer": AgentRole.FRONTEND_CODE_REVIEWER,
    "frontend reviewer": AgentRole.FRONTEND_CODE_REVIEWER,
    "security engineer": AgentRole.SECURITY_ENGINEER,
    # QA roles
    "qa planner": AgentRole.QA_PLANNER,
    "qa engineer (planner)": AgentRole.QA_PLANNER,
    "qa executor": AgentRole.QA_EXECUTOR,
    "qa engineer (executor)": AgentRole.QA_EXECUTOR,
    # Lightweight roles
    "documentation engineer": AgentRole.DOCUMENTATION_ENGINEER,
    "git manager": AgentRole.GIT_MANAGER,
    # Specialist roles
    "api contract designer": AgentRole.API_CONTRACT_DESIGNER,
    "api designer": AgentRole.API_CONTRACT_DESIGNER,
    "migration engineer": AgentRole.MIGRATION_ENGINEER,
    "ux specifier": AgentRole.UX_SPECIFIER,
    "ux specialist": AgentRole.UX_SPECIFIER,
    "tech debt assessor": AgentRole.TECH_DEBT_ASSESSOR,
    "release engineer": AgentRole.RELEASE_ENGINEER,
    "incident analyst": AgentRole.INCIDENT_ANALYST,
    "load test engineer": AgentRole.LOAD_TEST_ENGINEER,
    "compliance auditor": AgentRole.COMPLIANCE_AUDITOR,
    "dependency auditor": AgentRole.DEPENDENCY_AUDITOR,
    "accessibility auditor": AgentRole.ACCESSIBILITY_AUDITOR,
    "integration test engineer": AgentRole.INTEGRATION_TEST_ENGINEER,
    # Advisory & cloud roles
    "legal advisor": AgentRole.LEGAL_ADVISOR,
    "user behavior psychologist": AgentRole.USER_BEHAVIOR_PSYCHOLOGIST,
    "ci/cd pipeline specialist": AgentRole.CICD_SPECIALIST,
    "cicd specialist": AgentRole.CICD_SPECIALIST,
    "ci/cd specialist": AgentRole.CICD_SPECIALIST,
    "aws specialist": AgentRole.AWS_SPECIALIST,
    "azure specialist": AgentRole.AZURE_SPECIALIST,
    "gcp specialist": AgentRole.GCP_SPECIALIST,
    "runpod specialist": AgentRole.RUNPOD_SPECIALIST,
    "llm specialist": AgentRole.LLM_SPECIALIST,
    "agentic ai specialist": AgentRole.AGENTIC_AI_SPECIALIST,
    "ml specialist": AgentRole.ML_SPECIALIST,
    "ml algorithm specialist": AgentRole.ML_SPECIALIST,
}

# Also map enum values (e.g. "product_manager") so LLMs can use either format
for _role in AgentRole:
    _ROLE_MAP.setdefault(_role.value, _role)


def _resolve_role(agent_str: str, step_name: str) -> AgentRole:
    """Resolve an agent string to an AgentRole, with fuzzy fallback.

    Tries exact match first, then normalized matching (strip punctuation,
    collapse whitespace), then substring matching against known role names.
    """
    # Exact match
    role = _ROLE_MAP.get(agent_str)
    if role is not None:
        return role

    # Normalize: strip punctuation and extra whitespace
    import string
    normalized = agent_str.translate(str.maketrans("", "", string.punctuation)).strip()
    normalized = " ".join(normalized.split())  # collapse whitespace
    role = _ROLE_MAP.get(normalized)
    if role is not None:
        return role

    # Substring match: find the longest key that's contained in agent_str (or vice versa)
    candidates: list[tuple[str, AgentRole]] = []
    for key, r in _ROLE_MAP.items():
        if key in agent_str or agent_str in key:
            candidates.append((key, r))
    if candidates:
        # Prefer the longest matching key (most specific)
        candidates.sort(key=lambda x: len(x[0]), reverse=True)
        return candidates[0][1]

    available = sorted({r.value for r in AgentRole})
    raise ValueError(
        f"Unknown agent role '{agent_str}' in step '{step_name}'. "
        f"Available roles: {', '.join(available)}"
    )


def _parse_field(body: str, key: str) -> str:
    """Extract a key: value field from the body text."""
    m = re.search(rf"^\s*{key}:\s*(.+)$", body, re.MULTILINE | re.IGNORECASE)
    return m.group(1).strip() if m else ""


def _parse_list_field(body: str, key: str) -> list[str]:
    """Extract a comma-separated list field."""
    raw = _parse_field(body, key)
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def _parse_bool_field(body: str, key: str) -> bool:
    raw = _parse_field(body, key).lower()
    return raw in ("true", "yes", "1")


def parse_custom_workflow(definition: str, name: str = "Custom Workflow") -> WorkflowDefinition:
    """Parse a custom workflow definition string into a WorkflowDefinition.

    Expected format (from the system prompt spec):
        STEP: <STEP_NAME>
          agent: <role>
          inputs: <comma-separated list>
          outputs: <comma-separated list>
          next: <step name>
          parallel: true|false
          on_fail: <step name or "escalate">
          gate: <"approval" or empty>
    """
    steps: list[WorkflowStepDefinition] = []

    for match in _STEP_RE.finditer(definition):
        step_name = match.group("name").strip()
        body = match.group("body")

        agent_str = _parse_field(body, "agent").lower().strip()
        role = _resolve_role(agent_str, step_name)

        inputs = _parse_list_field(body, "inputs")
        outputs = _parse_list_field(body, "outputs")
        next_step = _parse_field(body, "next") or None
        parallel = _parse_bool_field(body, "parallel")
        on_fail = _parse_field(body, "on_fail") or "escalate"
        gate = _parse_field(body, "gate") or None
        max_retries_str = _parse_field(body, "max_retries")
        max_retries = int(max_retries_str) if max_retries_str.isdigit() else 3

        steps.append(WorkflowStepDefinition(
            name=step_name,
            agent_role=role,
            inputs=inputs,
            outputs=outputs,
            next=next_step,
            parallel=parallel,
            on_fail=on_fail,
            gate=gate,
            max_retries=max_retries,
        ))

    if not steps:
        raise ValueError("No workflow steps found in definition")

    return WorkflowDefinition(
        name=name,
        workflow_type=WorkflowType.CUSTOM,
        steps=steps,
    )
