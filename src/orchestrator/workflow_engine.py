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
from orchestrator.knowledge import build_knowledge, synthesize_brief, update_cumulative_context
from orchestrator.knowledge_watcher import KnowledgeWatcher
from orchestrator.models import (
    AgentRole,
    ModelTier,
    OrchestratorConfig,
    PhaseState,
    PhaseStatus,
    RunState,
    SpawnRecord,
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
    # Specialist implementation roles
    "api_contract_designer", "migration_engineer", "ux_specifier",
    "release_engineer", "integration_test_engineer",
    "accessibility_auditor",
    # QA roles (tests are implementation work)
    "qa_engineer", "qa_planner", "qa_executor",
    # Cloud/infra specialists
    "cicd_specialist", "aws_specialist", "azure_specialist",
    "gcp_specialist", "runpod_specialist",
    # Security implementation
    "security_engineer",
})

# Maps assigned_role strings from tasks.json to AgentRole enums
_ROLE_STRING_TO_ENUM: dict[str, AgentRole] = {
    # Legacy alias — "engineer" in old tasks.json maps to backend_engineer
    "engineer": AgentRole.BACKEND_ENGINEER,
    # Auto-map every AgentRole.value to its enum (covers all 40 roles)
    **{role.value: role for role in AgentRole},
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
        interrupt_manager: Any | None = None,
        confirm_callback: Any | None = None,
    ) -> None:
        self.workflow = workflow
        self.state = state
        self.config = config
        self.run_logger = run_logger
        self.project_root = project_root or Path.cwd()
        self.dry_run = dry_run
        self.approval_callback = approval_callback
        self.interrupt_manager = interrupt_manager
        self.confirm_callback = confirm_callback
        self.progress = ProgressTracker(workflow, state)
        self._step_start_times: dict[str, float] = {}
        self._knowledge_watcher: KnowledgeWatcher | None = None

    @property
    def _mcp_servers(self) -> dict[str, Any] | None:
        """Return MCP server config for agent invocations, if available."""
        kc = self.config.knowledge_context
        return kc.mcp_server_config if kc else None

    def _checkpoint_state(self) -> None:
        """Persist RunState to disk after each task completes (sub-task-level checkpointing).

        This ensures that if the process is killed mid-step, already-completed
        tasks within that step are recorded and can be skipped on resume.
        """
        from orchestrator.persistence import save_run_state
        save_run_state(self.state, Path(self.state.workspace_dir))

    async def execute(self) -> RunState:
        """Execute the entire workflow from start to finish."""
        logger.info(f"Starting workflow: {self.workflow.name} ({len(self.workflow.steps)} steps)")

        # Seed the feature_request artifact so steps that depend on it can find it
        self._seed_feature_request_artifact()

        self.progress.show("workflow_start")

        for i, step in enumerate(self.workflow.steps):
            # Skip already-completed steps (for resume)
            if step.name in self.state.completed_steps:
                logger.info(f"Skipping {step.name} (already completed)")
                continue

            self.state.current_step = step.name

            # Interrupt checkpoint: between steps
            if await self._check_interrupt("between_steps"):
                break

            result = await self._execute_step(step)

            if result == "escalate":
                logger.error(f"Step '{step.name}' escalated to human — stopping workflow")
                break
            elif result == "failed":
                routed = await self._handle_step_failure(step)
                if not routed:
                    break

            # Tech stack confirmation: pause after any step that produces
            # the architecture artifact so the user can review tech decisions.
            if (
                result == "completed"
                and "architecture" in step.outputs
                and self.config.tech_stack_confirmation
            ):
                try:
                    from orchestrator.tech_stack import confirm_tech_stack
                    workspace = Path(self.state.workspace_dir)
                    confirm_tech_stack(workspace, dry_run=self.dry_run)
                except KeyboardInterrupt:
                    logger.info("User aborted at tech stack confirmation")
                    self._checkpoint_state()
                    break

            self.progress.show("step_complete")

        self.state.current_step = None
        self.progress.show("workflow_complete")
        return self.state

    async def _execute_step(self, step: WorkflowStepDefinition) -> str:
        """Execute a single workflow step. Returns 'completed', 'failed', or 'escalate'."""
        step_idx = next((i for i, s in enumerate(self.workflow.steps) if s.name == step.name), 0) + 1
        total_steps = len(self.workflow.steps)
        completed = len(self.state.completed_steps)
        role_title = get_role(step.agent_role).title

        print(
            f"\n\033[1;34m{'━'*60}\033[0m\n"
            f"\033[1;34m  STEP {step_idx}/{total_steps}\033[0m  "
            f"\033[1m{step.name}\033[0m\n"
            f"  \033[2mAgent: {role_title}  │  "
            f"Progress: {completed}/{total_steps} steps complete  │  "
            f"Cost so far: ${self.state.total_cost_usd:.4f}\033[0m\n"
            f"\033[1;34m{'━'*60}\033[0m",
            flush=True,
        )

        phase_key = self._step_to_phase_key(step)
        self.state.phases[phase_key] = PhaseState(status=PhaseStatus.RUNNING)
        self._step_start_times[step.name] = time.time()

        missing_inputs = self._validate_step_inputs(step)
        if missing_inputs:
            # Auto-generate PRD if it's the only (or one of the) missing artifact(s)
            if "prd" in missing_inputs:
                prd_ok = await self._auto_generate_prd()
                if prd_ok:
                    missing_inputs.remove("prd")

            if missing_inputs:
                self.state.phases[phase_key].status = PhaseStatus.FAILED
                self.state.phases[phase_key].error = f"Missing required inputs: {missing_inputs}"
                return "failed"

        if step.gate == "approval":
            approved = await self._wait_for_approval(step)
            if not approved:
                self.state.phases[phase_key].status = PhaseStatus.SKIPPED
                return "escalate"

        tasks = self._recover_or_create_tasks(step)

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

        # Start knowledge watcher for parallel implementation steps
        if (
            step.parallel
            and self.config.knowledge.enabled
            and self.config.knowledge.watcher_enabled
        ):
            await self._start_knowledge_watcher()

        try:
            success = await self._execute_step_tasks(step, tasks)
        finally:
            # Always stop watcher when step completes (success or failure)
            await self._stop_knowledge_watcher()

        workspace = Path(self.state.workspace_dir)

        if success:
            # Validate output artifacts; retry once if missing (agent may have
            # forgotten to use the Write tool — a common failure mode).
            missing_artifacts = self._check_missing_artifacts(step, workspace)
            if missing_artifacts and self.state.phases[phase_key].retry_count == 0:
                self.state.phases[phase_key].retry_count += 1
                missing_files = ", ".join(f"{a}.json" for a in missing_artifacts)
                logger.warning(
                    f"Artifact(s) not found after step '{step.name}' — "
                    f"retrying with explicit Write tool reminder: {missing_files}"
                )
                retry_tasks = self._create_artifact_retry_tasks(step, tasks, missing_artifacts, workspace)
                try:
                    retry_success = await self._execute_step_tasks(step, retry_tasks)
                finally:
                    await self._stop_knowledge_watcher()
                if retry_success:
                    missing_artifacts = self._check_missing_artifacts(step, workspace)

            if missing_artifacts:
                missing_files = ", ".join(f"{a}.json" for a in missing_artifacts)
                self.state.phases[phase_key].status = PhaseStatus.FAILED
                self.state.phases[phase_key].error = f"Artifact(s) not written to disk: {missing_files}"
                return "failed"

            # Full schema validation on all output artifacts
            for artifact_name in step.outputs:
                artifact_path = workspace / "artifacts" / f"{artifact_name}.json"
                validation = validate_artifact_file(artifact_path, artifact_name)
                if not validation.valid:
                    logger.warning(f"Artifact {artifact_name} validation failed: {validation.errors}")
                    self.state.phases[phase_key].status = PhaseStatus.FAILED
                    self.state.phases[phase_key].error = f"Artifact validation: {validation.errors}"
                    return "failed"

            # If an implementation step wrote a review.json that isn't in its
            # declared outputs, rename it so it doesn't collide with the
            # downstream Code Review step's artifact.
            if "review" not in step.outputs and step.parallel:
                stale_review = workspace / "artifacts" / "review.json"
                if stale_review.exists():
                    dest = workspace / "artifacts" / f"review-impl-{step.name.lower().replace(' ', '_')}.json"
                    stale_review.rename(dest)
                    logger.info(
                        "Renamed stale review.json written by implementation step '%s' → %s",
                        step.name, dest.name,
                    )

            self.state.phases[phase_key].status = PhaseStatus.COMPLETED
            self.state.completed_steps.append(step.name)
            # Update cumulative context so downstream steps see this step's decisions
            update_cumulative_context(
                workspace=workspace,
                phase_name=step.name,
                artifacts=list(step.outputs),
            )
            # Re-index knowledge after implementation steps so QA/review see fresh code
            await self._refresh_knowledge(step.name)
            return "completed"
        else:
            self.state.phases[phase_key].status = PhaseStatus.FAILED
            return "failed"

    def _check_missing_artifacts(
        self, step: WorkflowStepDefinition, workspace: Path,
    ) -> list[str]:
        """Return names of output artifacts that don't exist on disk."""
        missing: list[str] = []
        for artifact_name in step.outputs:
            artifact_path = workspace / "artifacts" / f"{artifact_name}.json"
            if not artifact_path.exists():
                missing.append(artifact_name)
        return missing

    def _create_artifact_retry_tasks(
        self,
        step: WorkflowStepDefinition,
        original_tasks: list[WorkflowTaskState],
        missing_artifacts: list[str],
        workspace: Path,
    ) -> list[WorkflowTaskState]:
        """Create retry tasks with an explicit Write tool reminder for missing artifacts."""
        artifacts_dir = workspace / "artifacts"
        missing_files = ", ".join(f"{artifacts_dir}/{a}.json" for a in missing_artifacts)

        retry_suffix = (
            f"\n\nRETRY — PREVIOUS ATTEMPT FAILED: The following artifact files were NOT created on disk: "
            f"{missing_files}. "
            f"You MUST use the Write tool to create each file. "
            f"Do NOT just output JSON in your response text — call the Write tool with the file path and content."
        )

        retry_tasks: list[WorkflowTaskState] = []
        for task in original_tasks:
            retry_task = WorkflowTaskState(
                task_id=f"{task.task_id}-retry",
                workflow_step=task.workflow_step,
                description=task.description + retry_suffix,
                assigned_role=task.assigned_role,
                dependencies=[],
                expected_outputs=task.expected_outputs,
            )
            retry_tasks.append(retry_task)
        return retry_tasks

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
            # Interrupt checkpoint: between dependency waves
            if await self._check_interrupt("between_waves"):
                return False

            logger.info(f"\n--- Wave {wave_num}/{len(waves)} ({len(wave)} tasks) ---")

            # Filter out already-completed tasks (partial step resume)
            pending_in_wave = [t for t in wave if t.status != TaskStatus.COMPLETED]
            already_done = len(wave) - len(pending_in_wave)
            if already_done:
                logger.info(f"  Skipping {already_done} already-completed task(s) in wave {wave_num}")
            if not pending_in_wave:
                continue

            if len(pending_in_wave) == 1:
                # Single task — no need for parallel machinery
                await self._execute_single_task(step, pending_in_wave[0], workspace)
                self._checkpoint_state()
                self.progress.on_task_complete()
            else:
                # Multiple tasks — partition by file conflicts
                no_conflict, conflict = self._partition_by_file_conflicts(pending_in_wave)

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
                            mcp_servers=self._mcp_servers,
                        ))

                    # Assign unique codenames so parallel agents are distinguishable
                    if len(invocations) > 1:
                        from orchestrator.codenames import generate_codename
                        used_names: set[str] = set()
                        for inv, task in zip(invocations, no_conflict):
                            codename = generate_codename(exclude=used_names)
                            used_names.add(codename)
                            inv.display_name = f"{codename} ({inv.agent_name} · {task.task_id})"

                    logger.info(f"  Launching {len(invocations)} agents in parallel:")
                    for inv in invocations:
                        logger.info(f"    - {inv.display_name or inv.agent_name} (model: {inv.model.value})")

                    if self.confirm_callback:
                        confirmed = []
                        for inv in invocations:
                            result = self.confirm_callback(inv)
                            if result is None:
                                raise KeyboardInterrupt("User aborted at confirmation")
                            confirmed.append(result)
                        invocations = confirmed

                    # REV-102: Check budget before launching parallel wave
                    if self.run_logger and self.config.max_budget_usd:
                        budget_status = self.run_logger.check_budget(self.config.max_budget_usd)
                        if budget_status == "exceeded":
                            logger.error("Budget exceeded before launching parallel wave — aborting")
                            for task in no_conflict:
                                task.status = TaskStatus.FAILED
                                task.error = "Budget exceeded"
                            return False

                    results = await invoke_agents_parallel(
                        invocations, max_concurrent=self.config.max_concurrent_agents,
                    )
                    # REV-103: Accumulate cost from parallel results into run state
                    for task, result in zip(no_conflict, results):
                        self.state.total_cost_usd += result.cost_usd
                        self.state.total_input_tokens += result.input_tokens
                        self.state.total_output_tokens += result.output_tokens
                        self._apply_result(task, result)
                        self._checkpoint_state()
                        self.progress.on_task_complete()

                    # Retry failed tasks from the parallel batch individually
                    # The parallel invocation counts as attempt 1; _execute_single_task
                    # will honour the remaining retries based on step.max_retries and
                    # the task's retry_count.
                    failed_parallel = [t for t in no_conflict if t.status == TaskStatus.FAILED]
                    if failed_parallel and step.max_retries > 0:
                        logger.info(f"  Retrying {len(failed_parallel)} failed task(s) from parallel batch")
                        for task in failed_parallel:
                            # Record the parallel attempt so _execute_single_task
                            # knows how many retries remain.
                            task.retry_count = max(task.retry_count, 1)
                            task.status = TaskStatus.PENDING
                            task.error = None
                            await self._execute_single_task(step, task, workspace)
                            self._checkpoint_state()

                # Run file-conflicting tasks sequentially
                for task in conflict:
                    logger.info(f"  Serializing {task.task_id} (file conflict)")
                    await self._execute_single_task(step, task, workspace)
                    self._checkpoint_state()
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
            # Interrupt checkpoint: between sequential tasks
            if await self._check_interrupt("between_tasks"):
                return False

            await self._execute_single_task(step, task, workspace)
            self._checkpoint_state()
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
        # Skip already-completed tasks (partial step resume)
        if task.status == TaskStatus.COMPLETED:
            logger.info(f"Skipping {task.task_id} (already completed in prior run)")
            return

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
        remaining_attempts = max(1, step.max_retries + 1 - task.retry_count)
        for attempt in range(remaining_attempts):
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

            invocation = AgentInvocation(
                agent_name=agent_name,
                prompt=prompt,
                model=model,
                max_turns=max_turns,
                workspace_dir=str(workspace),
                project_root=str(self.project_root),
                enhanced_perception=self.config.enhanced_perception,
                mcp_servers=self._mcp_servers,
            )

            if self.confirm_callback:
                confirmed = self.confirm_callback(invocation)
                if confirmed is None:
                    raise KeyboardInterrupt("User aborted at confirmation")
                invocation = confirmed

            result = await invoke_agent(invocation)

            if self.run_logger:
                self.run_logger.log_event("task_result", {
                    "task_id": task.task_id,
                    "success": result.success,
                    "cost_usd": result.cost_usd,
                    "attempt": attempt + 1,
                })

            self.state.total_cost_usd += result.cost_usd

            if result.success:
                # Run dynamic spawn loop if enabled
                result = await self._run_spawn_loop_for_task(
                    task, step, result, agent_name, model, max_turns,
                    str(workspace),
                )
                self._apply_result(task, result)
                return

            # If interrupted by SIGINT, don't retry — mark as interrupted and bail
            if result.error and "SIGINT" in result.error:
                task.status = TaskStatus.FAILED
                task.error = "Interrupted by user"
                logger.info(f"Task {task.task_id} interrupted by user — will not retry")
                return

            task.retry_count += 1
            logger.warning(f"Task {task.task_id} failed (attempt {attempt + 1}): {result.error}")

            # Append validation/failure errors to the retry prompt so the
            # agent can address them on the next attempt (REV-205).
            error_detail = result.error or "Unknown error"
            prompt = (
                f"{prompt}\n\n"
                f"--- RETRY (attempt {task.retry_count + 1}) ---\n"
                f"Your previous attempt failed with the following error:\n"
                f"{error_detail}\n"
                f"Please fix the issues described above and try again."
            )

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

    async def _run_spawn_loop_for_task(
        self,
        task: WorkflowTaskState,
        step: WorkflowStepDefinition,
        result: AgentResult,
        agent_name: str,
        model: ModelTier,
        max_turns: int,
        workspace_dir: str,
    ) -> AgentResult:
        """Run the dynamic spawn loop for a task if spawn requests are detected."""
        spawn_config = self.config.spawn
        if not spawn_config.enabled:
            return result

        from orchestrator.spawning import run_spawn_loop

        parent_role = task.assigned_role.value
        final_result, rounds = await run_spawn_loop(
            initial_result=result,
            agent_name=agent_name,
            parent_role=parent_role,
            original_prompt="",  # not needed for continuation
            config=spawn_config,
            model=model,
            max_turns=max_turns,
            workspace_dir=workspace_dir,
            project_root=str(self.project_root),
            max_concurrent=self.config.max_concurrent_agents,
            agents_config=self.config.agents,
            run_logger=self.run_logger,
        )

        # Record spawn history in run state
        for round_summary in rounds:
            for req, sr in zip(round_summary.requests, round_summary.results):
                self.state.spawn_history.append(SpawnRecord(
                    parent_step=step.name,
                    parent_role=parent_role,
                    round_number=round_summary.round_number,
                    spawned_role=sr.role,
                    reason=sr.reason,
                    success=sr.success,
                    cost_usd=sr.cost_usd,
                ))

        # Update state cost tracking (spawn costs are already in final_result)
        return final_result

    async def _check_interrupt(self, context: str) -> bool:
        """Check for interrupt at a safe boundary. Returns True if pipeline should stop."""
        if not self.interrupt_manager or not self.interrupt_manager.should_interrupt():
            return False

        from orchestrator.interruption import handle_interruption

        result = await handle_interruption(
            state=self.state,
            config=self.config,
            interrupt_manager=self.interrupt_manager,
            run_logger=self.run_logger,
            project_root=self.project_root,
            context=context,
        )
        return result == "abort"

    async def _start_knowledge_watcher(self) -> None:
        """Start the file-change knowledge watcher for implementation steps."""
        if self._knowledge_watcher is not None:
            return  # Already running

        self._knowledge_watcher = KnowledgeWatcher(
            project_root=self.project_root,
            aicoder_path=self.config.knowledge.aicoder_path,
            build_timeout_seconds=self.config.knowledge.build_timeout_seconds,
            debounce_seconds=self.config.knowledge.watcher_debounce_seconds,
            brief_max_files=self.config.knowledge.brief_max_files,
            brief_max_symbols=self.config.knowledge.brief_max_symbols,
            inject_brief=self.config.knowledge.inject_brief,
            knowledge_context=self.config.knowledge_context,
        )
        await self._knowledge_watcher.start()

    async def _stop_knowledge_watcher(self) -> None:
        """Stop the file-change knowledge watcher if running."""
        if self._knowledge_watcher is None:
            return
        await self._knowledge_watcher.stop()
        if self._knowledge_watcher.rebuild_count > 0:
            logger.info(
                f"Knowledge watcher performed {self._knowledge_watcher.rebuild_count} "
                f"incremental rebuild(s) during step execution"
            )
        self._knowledge_watcher = None

    async def _refresh_knowledge(self, step_name: str) -> None:
        """Re-index knowledge base after code-modifying steps.

        Triggered after Implementation or similar steps so that QA, review,
        and downstream agents see the freshly written code in the knowledge base.
        """
        if not self.config.knowledge.enabled:
            return

        # Steps after which re-indexing is valuable
        _REINDEX_AFTER = {"implementation", "engineer"}
        if step_name.lower() not in _REINDEX_AFTER:
            return

        logger.info(f"Re-indexing knowledge base after '{step_name}' step...")
        result = await build_knowledge(
            project_root=self.project_root,
            aicoder_path=self.config.knowledge.aicoder_path,
            timeout_seconds=self.config.knowledge.build_timeout_seconds,
            skip_if_fresh_minutes=0,  # Force rebuild — code just changed
        )
        if result.success:
            if self.config.knowledge.inject_brief:
                brief = synthesize_brief(
                    result.knowledge_root,
                    max_files=self.config.knowledge.brief_max_files,
                    max_symbols=self.config.knowledge.brief_max_symbols,
                )
                if self.config.knowledge_context:
                    self.config.knowledge_context.brief = brief
                    self.config.knowledge_context.file_count = result.file_count
            logger.info(
                f"Knowledge re-indexed: {result.file_count} files in {result.build_time_ms:.0f}ms"
            )
        else:
            logger.warning(f"Knowledge re-index failed: {result.error}")

    def _seed_feature_request_artifact(self) -> None:
        """Write the feature_request string as a JSON artifact so steps can reference it."""
        workspace = Path(self.state.workspace_dir)
        artifacts_dir = workspace / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        fr_path = artifacts_dir / "feature_request.json"
        if not fr_path.exists():
            fr_path.write_text(json.dumps({
                "feature_request": self.state.feature_request,
            }, indent=2))

    def _validate_step_inputs(self, step: WorkflowStepDefinition) -> list[str]:
        """Check that all required input artifacts exist.

        Returns a list of missing artifact names (empty if all present).
        """
        workspace = Path(self.state.workspace_dir)
        missing: list[str] = []
        for artifact_name in step.inputs:
            artifact_path = workspace / "artifacts" / f"{artifact_name}.json"
            if not artifact_path.exists():
                logger.warning(f"Missing required input: {artifact_path}")
                missing.append(artifact_name)
        return missing

    async def _auto_generate_prd(self) -> bool:
        """Auto-generate a PRD via the PM agent when it's missing.

        Returns True if the PRD was successfully created.
        """
        from orchestrator.phases import PROMPT_BUILDERS
        from orchestrator.roles import role_to_legacy_agent_name

        workspace = Path(self.state.workspace_dir)
        logger.info("PRD artifact missing — auto-generating via PM agent...")

        phase_key = "auto_prd"
        self.state.phases[phase_key] = PhaseState(status=PhaseStatus.RUNNING)

        builder = PROMPT_BUILDERS.get(AgentRole.PRODUCT_MANAGER)
        if builder:
            prompt = builder(
                feature_request=self.state.feature_request,
                workspace=workspace,
                config=self.config,
            )
        else:
            artifacts_dir = workspace / "artifacts"
            prompt = (
                f"You are the Product Manager.\n\n"
                f"## Feature Request\n\n{self.state.feature_request}\n\n"
                f"## Output\n\nWrite your PRD to {artifacts_dir}/prd.json\n"
            )

        agent_name = role_to_legacy_agent_name(AgentRole.PRODUCT_MANAGER)
        agent_config = self.config.agents.get(agent_name)
        model = agent_config.model if agent_config else ModelTier.SONNET

        invocation = AgentInvocation(
            agent_name=agent_name,
            prompt=prompt,
            model=model,
            max_turns=agent_config.max_turns if agent_config else 40,
            workspace_dir=str(workspace),
            project_root=str(self.project_root),
            enhanced_perception=self.config.enhanced_perception,
            mcp_servers=self._mcp_servers,
        )

        if self.dry_run:
            logger.info("[DRY RUN] Would auto-generate PRD via PM agent")
            self.state.phases[phase_key].status = PhaseStatus.COMPLETED
            return True

        result = await invoke_agent(invocation)
        self.state.total_cost_usd += result.cost_usd
        self.state.total_input_tokens += result.input_tokens
        self.state.total_output_tokens += result.output_tokens

        if self.run_logger:
            self.run_logger.log_event("auto_prd", {
                "success": result.success,
                "cost_usd": result.cost_usd,
            })

        prd_path = workspace / "artifacts" / "prd.json"
        if result.success and prd_path.exists():
            # Validate the generated PRD
            validation = validate_artifact_file(prd_path, "prd")
            if validation.valid:
                self.state.phases[phase_key].status = PhaseStatus.COMPLETED
                self.state.phases[phase_key].cost_usd = result.cost_usd
                logger.info("PRD auto-generated successfully")
                return True
            else:
                logger.warning(f"Auto-generated PRD failed validation: {validation.errors}")

        self.state.phases[phase_key].status = PhaseStatus.FAILED
        self.state.phases[phase_key].error = result.error or "PRD not created"
        logger.error("Failed to auto-generate PRD")
        return False

    def _create_tasks_for_step(self, step: WorkflowStepDefinition) -> list[WorkflowTaskState]:
        """Convert a workflow step into one or more tasks.

        For parallel steps, loads tasks.json and creates one WorkflowTaskState
        per entry whose assigned_role is in _IMPLEMENTATION_ROLES. Each task
        gets its own agent invocation, enabling true parallelism.

        This is NOT hardcoded to a specific step name — any step with
        parallel=True will attempt to load and split tasks from tasks.json.
        """
        workspace = Path(self.state.workspace_dir)
        existing_count = len(self.state.workflow_tasks)

        if step.parallel:
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

    def _recover_or_create_tasks(self, step: WorkflowStepDefinition) -> list[WorkflowTaskState]:
        """Recover persisted tasks for a step (resume) or create new ones.

        On resume, state.workflow_tasks may already contain tasks from a prior
        run that was interrupted mid-step. If we find tasks for this step, we
        reuse them (preserving their COMPLETED/FAILED statuses) so that
        _execute_step_tasks can skip already-completed work.

        Before resetting a failed/in-progress task to PENDING, we check whether
        the agent already wrote its expected output artifacts to disk. If all
        outputs exist and pass schema validation, the task is promoted to
        COMPLETED — avoiding expensive re-execution of work already done.

        If no prior tasks exist for this step, we create fresh ones and append
        them to the state.
        """
        existing = [t for t in self.state.workflow_tasks if t.workflow_step == step.name]
        if existing:
            promoted = 0
            reset = 0
            for t in existing:
                if t.status in (TaskStatus.FAILED, TaskStatus.IN_PROGRESS, TaskStatus.BLOCKED):
                    if self._task_outputs_already_exist(t, step):
                        t.status = TaskStatus.COMPLETED
                        t.error = None
                        promoted += 1
                        logger.info(
                            f"Task {t.task_id} promoted to COMPLETED — "
                            f"output artifacts already exist on disk"
                        )
                    else:
                        t.status = TaskStatus.PENDING
                        t.error = None
                        reset += 1
                    # Do NOT reset t.retry_count — preserve history from prior run

            completed = sum(1 for t in existing if t.status == TaskStatus.COMPLETED)
            pending = sum(1 for t in existing if t.status == TaskStatus.PENDING)
            logger.info(
                f"Recovered {len(existing)} task(s) for step '{step.name}' from prior run "
                f"({completed} completed [{promoted} promoted], "
                f"{pending} pending [{reset} reset])"
            )
            return existing

        tasks = self._create_tasks_for_step(step)
        self.state.workflow_tasks.extend(tasks)
        return tasks

    def _task_outputs_already_exist(
        self, task: WorkflowTaskState, step: WorkflowStepDefinition,
    ) -> bool:
        """Check whether a task's expected output artifacts already exist and are valid.

        For implementation tasks (code changes), this always returns False since
        we can't easily verify partial code work. For artifact-producing tasks
        (PRD, Architecture, etc.), we check if the step's output artifacts exist
        on disk and pass schema validation.
        """
        workspace = Path(self.state.workspace_dir)

        # Implementation roles write code, not artifacts — can't verify completion
        if task.assigned_role in _IMPLEMENTATION_ROLES:
            # But check if this is the ONLY task in the step and the step's
            # output artifacts exist (e.g., a single engineer writing tests
            # that also produces an artifact)
            if not step.outputs:
                return False

        # Check step-level output artifacts
        artifacts_to_check = step.outputs if step.outputs else []
        if not artifacts_to_check:
            return False

        for artifact_name in artifacts_to_check:
            artifact_path = workspace / "artifacts" / f"{artifact_name}.json"
            if not artifact_path.exists():
                return False
            validation = validate_artifact_file(artifact_path, artifact_name)
            if not validation.valid:
                return False

        return True

    def _build_task_prompt(
        self,
        step: WorkflowStepDefinition,
        task: WorkflowTaskState,
        workspace: Path,
    ) -> str:
        """Build the prompt for executing a task within a step."""
        from orchestrator.phases import (
            PROMPT_BUILDERS,
            _inject_knowledge_context,
            _inject_spawn_instructions,
            _exploration_instruction,
            _inject_cumulative_context,
            _inject_artifact_digests,
        )

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

        knowledge_section = _inject_knowledge_context(self.config)
        cumulative_section = _inject_cumulative_context(workspace)
        artifact_digest_section = _inject_artifact_digests(
            workspace, list(step.inputs), self.config,
        )
        exploration = _exploration_instruction(self.config)

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

        spawn_section = _inject_spawn_instructions(self.config, task.assigned_role.value)

        return f"""You are the {role_def.title} for this project.

{knowledge_section}## Task

{self.state.feature_request}

## Step: {step.name}

{task.description}

## Your Role

{role_def.responsibility}
{input_section}{output_section}{cumulative_section}{artifact_digest_section}
## Instructions

1. Read all input artifacts listed above
2. {exploration}
3. Perform your role's responsibilities
4. Write any required output artifacts as valid JSON{access_note}
{spawn_section}"""

    async def _handle_step_failure(self, step: WorkflowStepDefinition, *, _routing_depth: int = 0) -> bool:
        """Handle a failed step according to on_fail routing."""
        if _routing_depth >= 3:
            logger.error(f"Max on_fail routing depth (3) reached for step '{step.name}' — stopping")
            return False

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
            from orchestrator.main import _ring_alarm
            _ring_alarm()
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
