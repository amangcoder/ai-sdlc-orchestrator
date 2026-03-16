"""Workflow engine — executes workflow steps with task management, gates, and failure handling."""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from orchestrator.agents import AgentInvocation, AgentResult, invoke_agent, invoke_agents_parallel
from orchestrator.models import (
    AgentRole,
    ModelTier,
    OrchestratorConfig,
    PhaseState,
    PhaseStatus,
    RunState,
    TaskStatus,
    WorkflowDefinition,
    WorkflowStepDefinition,
    WorkflowTaskState,
)
from orchestrator.observability import RunLogger
from orchestrator.progress import ProgressTracker
from orchestrator.roles import get_role, role_to_legacy_agent_name, validate_role_access
from orchestrator.validation import validate_artifact_file

logger = logging.getLogger(__name__)

# Roles that count as "implementation" roles for task loading.
# Tasks with these assigned_role values are loaded from tasks.json
# and dispatched to specialist agents during the Implementation step.
_IMPLEMENTATION_ROLES = frozenset({
    "engineer", "frontend_engineer", "backend_engineer",
    "database_engineer", "caching_performance_engineer",
    "automation_engineer", "devops_engineer", "observability_engineer",
    "documentation_engineer",
    # New specialist implementation roles
    "api_contract_designer", "migration_engineer", "ux_specifier",
    "release_engineer", "integration_test_engineer",
    "accessibility_auditor",
})

# Maps assigned_role strings from tasks.json to AgentRole enums
_ROLE_STRING_TO_ENUM: dict[str, AgentRole] = {
    "engineer": AgentRole.BACKEND_ENGINEER,
    "frontend_engineer": AgentRole.FRONTEND_ENGINEER,
    "backend_engineer": AgentRole.BACKEND_ENGINEER,
    "database_engineer": AgentRole.DATABASE_ENGINEER,
    "caching_performance_engineer": AgentRole.CACHING_PERFORMANCE_ENGINEER,
    "automation_engineer": AgentRole.AUTOMATION_ENGINEER,
    "devops_engineer": AgentRole.DEVOPS_ENGINEER,
    "observability_engineer": AgentRole.OBSERVABILITY_ENGINEER,
    "documentation_engineer": AgentRole.DOCUMENTATION_ENGINEER,
    # New specialist roles
    "api_contract_designer": AgentRole.API_CONTRACT_DESIGNER,
    "migration_engineer": AgentRole.MIGRATION_ENGINEER,
    "ux_specifier": AgentRole.UX_SPECIFIER,
    "tech_debt_assessor": AgentRole.TECH_DEBT_ASSESSOR,
    "release_engineer": AgentRole.RELEASE_ENGINEER,
    "incident_analyst": AgentRole.INCIDENT_ANALYST,
    "load_test_engineer": AgentRole.LOAD_TEST_ENGINEER,
    "compliance_auditor": AgentRole.COMPLIANCE_AUDITOR,
    "dependency_auditor": AgentRole.DEPENDENCY_AUDITOR,
    "accessibility_auditor": AgentRole.ACCESSIBILITY_AUDITOR,
    "integration_test_engineer": AgentRole.INTEGRATION_TEST_ENGINEER,
    "legal_advisor": AgentRole.LEGAL_ADVISOR,
    "user_behavior_psychologist": AgentRole.USER_BEHAVIOR_PSYCHOLOGIST,
}


class WorkflowEngine:
    """Executes a workflow definition step-by-step with full task lifecycle management.

    Supports dependency-aware parallel execution: tasks are scheduled in waves
    based on their dependency graph. Within each wave, independent tasks run
    concurrently (each dispatched to its specialist agent in an isolated worktree).
    Tasks with file conflicts within a wave are serialized.
    """

    def __init__(
        self,
        workflow: WorkflowDefinition,
        state: RunState,
        config: OrchestratorConfig,
        run_logger: RunLogger | None = None,
        project_root: Path | None = None,
        dry_run: bool = False,
        approval_callback: Any | None = None,
    ) -> None:
        self.workflow = workflow
        self.state = state
        self.config = config
        self.run_logger = run_logger
        self.project_root = project_root or Path.cwd()
        self.dry_run = dry_run
        self.approval_callback = approval_callback
        self.progress = ProgressTracker(workflow, state)
        self._step_start_times: dict[str, float] = {}

    async def execute(self) -> RunState:
        """Execute the entire workflow from start to finish."""
        logger.info(f"Starting workflow: {self.workflow.name} ({len(self.workflow.steps)} steps)")
        self.progress.show("workflow_start")

        for i, step in enumerate(self.workflow.steps):
            # Skip already-completed steps (for resume)
            if step.name in self.state.completed_steps:
                logger.info(f"Skipping {step.name} (already completed)")
                continue

            self.state.current_step = step.name

            result = await self._execute_step(step)

            if result == "escalate":
                logger.error(f"Step '{step.name}' escalated to human — stopping workflow")
                break
            elif result == "failed":
                routed = await self._handle_step_failure(step)
                if not routed:
                    break

            self.progress.show("step_complete")

        self.state.current_step = None
        self.progress.show("workflow_complete")
        return self.state

    async def _execute_step(self, step: WorkflowStepDefinition) -> str:
        """Execute a single workflow step. Returns 'completed', 'failed', or 'escalate'."""
        logger.info(f"\n{'='*60}")
        logger.info(f"STEP: {step.name} (agent: {get_role(step.agent_role).title})")
        logger.info(f"{'='*60}")

        phase_key = self._step_to_phase_key(step)
        self.state.phases[phase_key] = PhaseState(status=PhaseStatus.RUNNING)
        self._step_start_times[step.name] = time.time()

        if not self._validate_step_inputs(step):
            self.state.phases[phase_key].status = PhaseStatus.FAILED
            self.state.phases[phase_key].error = f"Missing required inputs: {step.inputs}"
            return "failed"

        if step.gate == "approval":
            approved = await self._wait_for_approval(step)
            if not approved:
                self.state.phases[phase_key].status = PhaseStatus.SKIPPED
                return "escalate"

        tasks = self._create_tasks_for_step(step)
        self.state.workflow_tasks.extend(tasks)

        if self.dry_run:
            logger.info(f"[DRY RUN] Would execute step '{step.name}' with {len(tasks)} task(s)")
            waves = _compute_dependency_waves(tasks)
            for wave_num, wave in enumerate(waves, 1):
                wave_ids = [t.task_id for t in wave]
                logger.info(f"  Wave {wave_num}: {wave_ids}")
            for t in tasks:
                role_title = get_role(t.assigned_role).title
                logger.info(f"  - {t.task_id}: {t.description[:60]} [{role_title}]")
                t.status = TaskStatus.COMPLETED
                t.completed_at = datetime.now(timezone.utc)
            self.state.phases[phase_key].status = PhaseStatus.COMPLETED
            self.state.completed_steps.append(step.name)
            return "completed"

        success = await self._execute_step_tasks(step, tasks)

        if success:
            for artifact_name in step.outputs:
                workspace = Path(self.state.workspace_dir)
                artifact_path = workspace / "artifacts" / f"{artifact_name}.json"
                validation = validate_artifact_file(artifact_path, artifact_name)
                if not validation.valid:
                    logger.warning(f"Artifact {artifact_name} validation failed: {validation.errors}")
                    self.state.phases[phase_key].status = PhaseStatus.FAILED
                    self.state.phases[phase_key].error = f"Artifact validation: {validation.errors}"
                    return "failed"

            self.state.phases[phase_key].status = PhaseStatus.COMPLETED
            self.state.completed_steps.append(step.name)
            return "completed"
        else:
            self.state.phases[phase_key].status = PhaseStatus.FAILED
            return "failed"

    async def _execute_step_tasks(
        self,
        step: WorkflowStepDefinition,
        tasks: list[WorkflowTaskState],
    ) -> bool:
        """Execute tasks within a step. Returns True if all succeeded."""
        workspace = Path(self.state.workspace_dir)

        if step.parallel and len(tasks) > 1:
            return await self._execute_tasks_dag(step, tasks, workspace)
        else:
            return await self._execute_tasks_sequential(step, tasks, workspace)

    async def _execute_tasks_dag(
        self,
        step: WorkflowStepDefinition,
        tasks: list[WorkflowTaskState],
        workspace: Path,
    ) -> bool:
        """Execute tasks respecting their dependency graph.

        Algorithm:
        1. Compute dependency waves via topological sort
        2. For each wave, all tasks are independent — run them in parallel
        3. Within a wave, further partition by file conflicts
        4. Non-conflicting tasks in a wave run concurrently via asyncio.gather()
        5. Conflicting tasks within a wave run sequentially after the parallel batch
        6. A wave must fully complete before the next wave starts
        7. If any task in a wave fails, stop execution

        Example with 20 tasks:
            Wave 1: [TASK-001, TASK-002, TASK-003]  → 3 agents in parallel
            Wave 2: [TASK-004, TASK-005, ..., TASK-010]  → 7 agents in parallel
            Wave 3: [TASK-011, ..., TASK-020]  → 10 agents in parallel
        """
        waves = _compute_dependency_waves(tasks)

        logger.info(f"Dependency analysis: {len(tasks)} tasks in {len(waves)} wave(s)")
        for wave_num, wave in enumerate(waves, 1):
            wave_ids = [t.task_id for t in wave]
            logger.info(f"  Wave {wave_num}: {wave_ids} ({len(wave)} parallel)")

        for wave_num, wave in enumerate(waves, 1):
            logger.info(f"\n--- Wave {wave_num}/{len(waves)} ({len(wave)} tasks) ---")

            if len(wave) == 1:
                # Single task — no need for parallel machinery
                await self._execute_single_task(step, wave[0], workspace)
                self.progress.on_task_complete()
            else:
                # Multiple tasks — partition by file conflicts
                no_conflict, conflict = self._partition_by_file_conflicts(wave)

                # Run non-conflicting tasks in parallel
                if no_conflict:
                    invocations: list[AgentInvocation] = []
                    for task in no_conflict:
                        task.status = TaskStatus.IN_PROGRESS
                        task.started_at = datetime.now(timezone.utc)

                        agent_name = role_to_legacy_agent_name(task.assigned_role)
                        agent_config = self.config.agents.get(agent_name)

                        prompt = self._build_task_prompt(step, task, workspace)

                        invocations.append(AgentInvocation(
                            agent_name=agent_name,
                            prompt=prompt,
                            model=agent_config.model if agent_config else ModelTier.SONNET,
                            max_turns=agent_config.max_turns if agent_config else 40,
                            workspace_dir=str(workspace),
                            project_root=str(self.project_root),
                            isolation="worktree",
                            enhanced_perception=self.config.enhanced_perception,
                        ))

                    logger.info(f"  Launching {len(invocations)} agents in parallel:")
                    for inv in invocations:
                        logger.info(f"    - {inv.agent_name} (model: {inv.model.value})")

                    results = await invoke_agents_parallel(
                        invocations, max_concurrent=self.config.max_concurrent_agents,
                    )
                    for task, result in zip(no_conflict, results):
                        self._apply_result(task, result)
                        self.progress.on_task_complete()

                # Run file-conflicting tasks sequentially
                for task in conflict:
                    logger.info(f"  Serializing {task.task_id} (file conflict)")
                    await self._execute_single_task(step, task, workspace)
                    self.progress.on_task_complete()

            # Check if any task in this wave failed
            failed_in_wave = [t for t in wave if t.status == TaskStatus.FAILED]
            if failed_in_wave:
                failed_ids = [t.task_id for t in failed_in_wave]
                logger.error(f"Wave {wave_num} had failures: {failed_ids}")

                # Mark downstream tasks as blocked
                completed_ids = {t.task_id for t in tasks if t.status == TaskStatus.COMPLETED}
                failed_ids_set = {t.task_id for t in failed_in_wave}
                for remaining_wave in waves[wave_num:]:
                    for task in remaining_wave:
                        if set(task.dependencies) & failed_ids_set:
                            task.status = TaskStatus.BLOCKED
                            task.error = f"Blocked by failed dependency"

                return False

        return all(t.status == TaskStatus.COMPLETED for t in tasks)

    async def _execute_tasks_sequential(
        self,
        step: WorkflowStepDefinition,
        tasks: list[WorkflowTaskState],
        workspace: Path,
    ) -> bool:
        """Execute tasks one by one."""
        for task in tasks:
            await self._execute_single_task(step, task, workspace)
            self.progress.on_task_complete()
            if task.status == TaskStatus.FAILED:
                return False
        return True

    async def _execute_single_task(
        self,
        step: WorkflowStepDefinition,
        task: WorkflowTaskState,
        workspace: Path,
    ) -> None:
        """Execute a single task with retry logic."""
        task.status = TaskStatus.IN_PROGRESS
        task.started_at = datetime.now(timezone.utc)

        agent_name = role_to_legacy_agent_name(task.assigned_role)
        agent_config = self.config.agents.get(agent_name)

        if agent_config is None:
            fallback_name = role_to_legacy_agent_name(step.agent_role)
            agent_config = self.config.agents.get(fallback_name)

        model = agent_config.model if agent_config else ModelTier.SONNET
        max_turns = agent_config.max_turns if agent_config else 40
        escalation_model = agent_config.escalation_model if agent_config else None

        prompt = self._build_task_prompt(step, task, workspace)

        result = None
        for attempt in range(step.max_retries + 1):
            if self.run_logger and self.config.max_budget_usd:
                budget_status = self.run_logger.check_budget(self.config.max_budget_usd)
                if budget_status == "exceeded":
                    task.status = TaskStatus.FAILED
                    task.error = "Budget exceeded"
                    logger.error(f"Budget exceeded — stopping task {task.task_id}")
                    return
                if budget_status == "warning":
                    logger.warning(f"Budget at 80%+ (${self.run_logger.cumulative_cost_usd:.2f}/${self.config.max_budget_usd:.2f})")

            if self.run_logger:
                self.run_logger.log_event("task_invoke", {
                    "task_id": task.task_id,
                    "step": step.name,
                    "agent": agent_name,
                    "role": task.assigned_role.value,
                    "model": model.value,
                    "attempt": attempt + 1,
                })

            result = await invoke_agent(AgentInvocation(
                agent_name=agent_name,
                prompt=prompt,
                model=model,
                max_turns=max_turns,
                workspace_dir=str(workspace),
                project_root=str(self.project_root),
                enhanced_perception=self.config.enhanced_perception,
            ))

            if self.run_logger:
                self.run_logger.log_event("task_result", {
                    "task_id": task.task_id,
                    "success": result.success,
                    "cost_usd": result.cost_usd,
                    "attempt": attempt + 1,
                })

            self.state.total_cost_usd += result.cost_usd

            if result.success:
                self._apply_result(task, result)
                return

            task.retry_count += 1
            logger.warning(f"Task {task.task_id} failed (attempt {attempt + 1}): {result.error}")

            if escalation_model and model != escalation_model:
                logger.info(f"Escalating model to {escalation_model.value}")
                model = escalation_model

        task.status = TaskStatus.FAILED
        task.error = result.error if result else "Max retries exceeded"

    def _apply_result(self, task: WorkflowTaskState, result: AgentResult) -> None:
        """Apply an agent result to a task state."""
        if result.success:
            task.status = TaskStatus.COMPLETED
            task.completed_at = datetime.now(timezone.utc)
        else:
            task.status = TaskStatus.FAILED
            task.error = result.error

    def _validate_step_inputs(self, step: WorkflowStepDefinition) -> bool:
        """Check that all required input artifacts exist."""
        workspace = Path(self.state.workspace_dir)
        for artifact_name in step.inputs:
            artifact_path = workspace / "artifacts" / f"{artifact_name}.json"
            if not artifact_path.exists():
                logger.error(f"Missing required input: {artifact_path}")
                return False
        return True

    def _create_tasks_for_step(self, step: WorkflowStepDefinition) -> list[WorkflowTaskState]:
        """Convert a workflow step into one or more tasks."""
        workspace = Path(self.state.workspace_dir)
        existing_count = len(self.state.workflow_tasks)

        if step.parallel and step.name == "Implementation":
            tasks_path = workspace / "artifacts" / "tasks.json"
            if tasks_path.exists():
                with open(tasks_path) as f:
                    data = json.load(f)
                task_list = data.get("tasks", [])
                result = []
                for i, t in enumerate(task_list):
                    role_str = t.get("assigned_role", "engineer")
                    if role_str in _IMPLEMENTATION_ROLES:
                        agent_role = _ROLE_STRING_TO_ENUM.get(role_str, step.agent_role)
                        result.append(WorkflowTaskState(
                            task_id=t.get("task_id", f"TASK-{existing_count + i + 1:03d}"),
                            workflow_step=step.name,
                            assigned_role=agent_role,
                            description=t.get("description", t.get("title", "")),
                            required_inputs=step.inputs,
                            expected_outputs=step.outputs,
                            acceptance_criteria=t.get("acceptance_criteria", []),
                            dependencies=t.get("dependencies", []),
                        ))
                if result:
                    return result

        return [WorkflowTaskState(
            task_id=f"STEP-{existing_count + 1:03d}",
            workflow_step=step.name,
            assigned_role=step.agent_role,
            description=f"Execute step: {step.name}",
            required_inputs=step.inputs,
            expected_outputs=step.outputs,
        )]

    def _build_task_prompt(
        self,
        step: WorkflowStepDefinition,
        task: WorkflowTaskState,
        workspace: Path,
    ) -> str:
        """Build the prompt for executing a task within a step."""
        from orchestrator.phases import PROMPT_BUILDERS

        builder = PROMPT_BUILDERS.get(task.assigned_role)
        if not builder:
            builder = PROMPT_BUILDERS.get(step.agent_role)

        if builder:
            task_data = None
            if step.name == "Implementation" and task.description:
                task_data = {
                    "task_id": task.task_id,
                    "description": task.description,
                    "acceptance_criteria": task.acceptance_criteria,
                    "dependencies": task.dependencies,
                }
            return builder(
                feature_request=self.state.feature_request,
                workspace=workspace,
                config=self.config,
                task_data=task_data,
            )

        role_def = get_role(task.assigned_role)
        artifacts_dir = workspace / "artifacts"

        input_section = ""
        if step.inputs:
            input_files = "\n".join(f"- {artifacts_dir}/{name}.json" for name in step.inputs)
            input_section = f"\n## Input Artifacts\n\n{input_files}\n"

        output_section = ""
        if step.outputs:
            output_files = "\n".join(f"- {artifacts_dir}/{name}.json" for name in step.outputs)
            output_section = f"\n## Expected Outputs\n\nWrite these artifacts:\n{output_files}\n"

        access_note = ""
        if role_def.access.value == "read_only":
            access_note = "\n\nIMPORTANT: Do NOT modify any code files. You are read-only."

        return f"""You are the {role_def.title} for this project.

## Task

{self.state.feature_request}

## Step: {step.name}

{task.description}

## Your Role

{role_def.responsibility}
{input_section}{output_section}
## Instructions

1. Read all input artifacts listed above
2. Explore the existing codebase for context
3. Perform your role's responsibilities
4. Write any required output artifacts as valid JSON{access_note}
"""

    async def _handle_step_failure(self, step: WorkflowStepDefinition) -> bool:
        """Handle a failed step according to on_fail routing."""
        phase_key = self._step_to_phase_key(step)
        phase_state = self.state.phases[phase_key]

        if step.on_fail == "escalate":
            logger.error(f"Step '{step.name}' failed — escalating to human")
            return False

        target_step_name = step.on_fail
        target_step = next((s for s in self.workflow.steps if s.name == target_step_name), None)
        if target_step is None:
            logger.error(f"on_fail target '{target_step_name}' not found — escalating")
            return False

        if phase_state.retry_count >= step.max_retries:
            logger.error(f"Max retries ({step.max_retries}) exhausted for '{step.name}' — escalating")
            return False

        phase_state.retry_count += 1
        logger.info(f"Routing back to step '{target_step_name}' (retry {phase_state.retry_count}/{step.max_retries})")

        if target_step_name in self.state.completed_steps:
            self.state.completed_steps.remove(target_step_name)

        target_idx = next(i for i, s in enumerate(self.workflow.steps) if s.name == target_step_name)
        current_idx = next(i for i, s in enumerate(self.workflow.steps) if s.name == step.name)

        for retry_step in self.workflow.steps[target_idx:current_idx + 1]:
            if retry_step.name in self.state.completed_steps:
                self.state.completed_steps.remove(retry_step.name)
            result = await self._execute_step(retry_step)
            if result != "completed":
                return False

        return True

    async def _wait_for_approval(self, step: WorkflowStepDefinition) -> bool:
        """Wait for human approval at a gate."""
        logger.info(f"\n{'='*60}")
        logger.info(f"APPROVAL GATE: {step.name}")
        logger.info(f"The workflow requires human approval before proceeding.")
        logger.info(f"{'='*60}")

        self.progress.show("approval_gate")

        if self.approval_callback:
            return await self.approval_callback(step)

        try:
            response = input(f"\nApprove step '{step.name}'? [y/N]: ").strip().lower()
            return response in ("y", "yes")
        except (EOFError, KeyboardInterrupt):
            return False

    def _partition_by_file_conflicts(
        self,
        tasks: list[WorkflowTaskState],
    ) -> tuple[list[WorkflowTaskState], list[WorkflowTaskState]]:
        """Split tasks into non-conflicting (safe for parallel) and conflicting groups."""
        workspace = Path(self.state.workspace_dir)
        tasks_path = workspace / "artifacts" / "tasks.json"
        file_map: dict[str, list[str]] = {}

        if tasks_path.exists():
            with open(tasks_path) as f:
                data = json.load(f)
            for t in data.get("tasks", []):
                file_map[t.get("task_id", "")] = t.get("files_to_modify", [])

        seen_files: set[str] = set()
        no_conflict: list[WorkflowTaskState] = []
        conflict: list[WorkflowTaskState] = []

        for task in tasks:
            files = set(file_map.get(task.task_id, []))
            if files & seen_files:
                conflict.append(task)
            else:
                no_conflict.append(task)
                seen_files |= files

        return no_conflict, conflict

    def _step_to_phase_key(self, step: WorkflowStepDefinition) -> str:
        """Convert a step to a phase key for state tracking."""
        return step.name.lower().replace(" ", "_")


# ---------------------------------------------------------------------------
# DAG Scheduler — computes dependency waves for parallel execution
# ---------------------------------------------------------------------------

def _compute_dependency_waves(
    tasks: list[WorkflowTaskState],
) -> list[list[WorkflowTaskState]]:
    """Compute execution waves from a task dependency graph (topological sort).

    Tasks are grouped into waves where:
    - All tasks within a wave have no dependencies on each other
    - A wave only starts after all previous waves complete
    - Tasks with unresolvable dependencies are placed in the last wave

    Example:
        TASK-001: no deps          → Wave 1
        TASK-002: no deps          → Wave 1
        TASK-003: depends on 001   → Wave 2
        TASK-004: depends on 001   → Wave 2
        TASK-005: depends on 003,004 → Wave 3

        Result: 3 waves, spawning [2, 2, 1] agents respectively
    """
    if not tasks:
        return []

    task_map: dict[str, WorkflowTaskState] = {t.task_id: t for t in tasks}
    task_ids = set(task_map.keys())

    # Build adjacency: task_id -> set of task_ids it depends on (within this step)
    deps: dict[str, set[str]] = {}
    for task in tasks:
        # Only consider dependencies that are in this step's task set
        deps[task.task_id] = set(task.dependencies) & task_ids

    # Kahn's algorithm for topological sort into waves
    waves: list[list[WorkflowTaskState]] = []
    completed: set[str] = set()
    remaining = set(task_ids)

    while remaining:
        # Find tasks whose dependencies are all completed
        ready = [
            tid for tid in remaining
            if deps[tid].issubset(completed)
        ]

        if not ready:
            # Circular dependency or unresolvable — dump everything remaining
            logger.warning(
                f"Circular or unresolvable dependencies detected for: "
                f"{remaining}. Running them sequentially."
            )
            waves.append([task_map[tid] for tid in sorted(remaining)])
            break

        wave = [task_map[tid] for tid in ready]
        waves.append(wave)

        completed.update(ready)
        remaining -= set(ready)

    return waves
