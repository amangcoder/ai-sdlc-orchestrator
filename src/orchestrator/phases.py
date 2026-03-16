"""Phase definitions and prompt builders for the orchestration pipeline."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from orchestrator.models import AgentConfig, ModelTier, OrchestratorConfig, TaskList


@dataclass
class PhaseDefinition:
    """Definition of an orchestration phase."""

    name: str
    agent_name: str
    build_prompt: Any  # Callable
    output_artifacts: list[str]
    parallel: bool = False


def build_pm_prompt(feature_request: str, workspace: Path, config: OrchestratorConfig) -> str:
    """Build the prompt for the PM agent."""
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


def build_architect_prompt(feature_request: str, workspace: Path, config: OrchestratorConfig) -> str:
    """Build the prompt for the Architect agent."""
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


def build_engineer_prompt(
    feature_request: str,
    workspace: Path,
    config: OrchestratorConfig,
    task_data: dict[str, Any] | None = None,
) -> str:
    """Build the prompt for an Engineer agent working on a specific task."""
    artifacts_dir = workspace / "artifacts"

    task_section = ""
    if task_data:
        task_section = f"""## Your Assigned Task

```json
{json.dumps(task_data, indent=2)}
```"""
    else:
        task_section = f"""## Tasks

Read the task breakdown from: {artifacts_dir}/tasks.json
Implement all tasks assigned to the engineer role."""

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


def build_qa_prompt(feature_request: str, workspace: Path, config: OrchestratorConfig) -> str:
    """Build the prompt for the QA agent."""
    artifacts_dir = workspace / "artifacts"

    return f"""You are the QA Engineer for this project.

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

The report must include:
- "test_results": {{"passed": N, "failed": N, "skipped": N}}
- "lint_clean": true/false
- "type_check_clean": true/false
- "issues": array of {{"severity": "critical|major|minor", "file": "...", "description": "...", "suggestion": "..."}}
- "verdict": "pass" or "fail"

IMPORTANT: Do NOT modify any code files. You are read-only."""


def build_reviewer_prompt(
    feature_request: str,
    workspace: Path,
    config: OrchestratorConfig,
    review_cycle: int = 1,
    previous_review: dict[str, Any] | None = None,
) -> str:
    """Build the prompt for the Reviewer agent."""
    artifacts_dir = workspace / "artifacts"

    cycle_context = ""
    if review_cycle > 1 and previous_review:
        cycle_context = f"""## Previous Review (Cycle {review_cycle - 1})

The previous review requested changes. Here was the feedback:

```json
{json.dumps(previous_review, indent=2)}
```

Focus on whether the issues from the previous review have been addressed."""

    return f"""You are the Code Reviewer for this project.

## Feature Request

{feature_request}

{cycle_context}

## Instructions

Review the implementation for quality, correctness, and architecture adherence:

1. Read all artifacts:
   - PRD: {artifacts_dir}/prd.json
   - Architecture: {artifacts_dir}/architecture.json
   - Tasks: {artifacts_dir}/tasks.json
   - QA Report: {artifacts_dir}/qa_report.json
2. Review all changed/new code files
3. Evaluate: correctness, architecture adherence, code quality, security, performance, test coverage

Write your review as valid JSON to: {artifacts_dir}/review.json

The review must include:
- "verdict": "approve" | "reject" | "request_changes"
- "issues": array of {{"severity": "critical|major|minor|nit", "file": "...", "description": "...", "suggestion": "..."}}
- "summary": Overall assessment (at least 20 characters)

IMPORTANT: Do NOT modify any code files. You are read-only."""


def get_engineer_tasks(workspace: Path) -> list[dict[str, Any]]:
    """Load and return individual engineer tasks from the tasks artifact."""
    tasks_path = workspace / "artifacts" / "tasks.json"
    if not tasks_path.exists():
        return []

    with open(tasks_path) as f:
        data = json.load(f)

    return [t for t in data.get("tasks", []) if t.get("assigned_role") == "engineer"]


# Phase registry
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
