"""Core orchestration engine — runs the SDLC pipeline."""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any

from orchestrator.agents import AgentInvocation, AgentResult, invoke_agent, invoke_agents_parallel
from orchestrator.models import (
    EngTaskState,
    ModelTier,
    OrchestratorConfig,
    PhaseState,
    PhaseStatus,
    ReviewVerdict,
    RunState,
    TaskStatus,
)
from orchestrator.observability import RunLogger
from orchestrator.phases import (
    PHASE_DEFINITIONS,
    build_engineer_prompt,
    build_reviewer_prompt,
    get_engineer_tasks,
)
from orchestrator.validation import validate_artifact_file

logger = logging.getLogger(__name__)

PHASE_ORDER = ["pm", "architect", "engineer", "qa", "reviewer"]


class OrchestratorEngine:
    """Runs the full SDLC orchestration pipeline."""

    def __init__(self, config: OrchestratorConfig, dry_run: bool = False) -> None:
        self.config = config
        self.dry_run = dry_run
        self.run_logger: RunLogger | None = None

    async def run(
        self,
        feature_request: str,
        single_phase: str | None = None,
        from_phase: str | None = None,
        resume: bool = False,
    ) -> RunState:
        """Execute the orchestration pipeline."""
        self.project_root = Path.cwd()  # where orchestrate was invoked — the actual codebase
        workspace = Path(self.config.workspace_dir).resolve()
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "artifacts").mkdir(exist_ok=True)

        # Resume from saved state, or start fresh
        state = self._load_state(workspace) if resume else None
        if state:
            logger.info(f"Resuming run {state.run_id} — skipping completed phases: "
                        f"{[p for p, s in state.phases.items() if s.status == PhaseStatus.COMPLETED]}")
        else:
            run_id = uuid.uuid4().hex[:12]
            state = RunState(
                run_id=run_id,
                feature_request=feature_request,
                workspace_dir=str(workspace),
                max_review_cycles=self.config.max_review_cycles,
            )

        self.run_logger = RunLogger(workspace / "logs", state.run_id)
        self.run_logger.log_event("run_start", {
            "feature_request": feature_request,
            "config": self.config.model_dump(),
            "resume": resume,
        })

        if single_phase:
            phases = [single_phase]
        elif from_phase:
            if from_phase not in PHASE_ORDER:
                raise ValueError(f"Unknown phase: {from_phase}. Choose from: {PHASE_ORDER}")
            phases = PHASE_ORDER[PHASE_ORDER.index(from_phase):]
        else:
            phases = PHASE_ORDER

        for phase_name in phases:
            if phase_name not in PHASE_DEFINITIONS:
                logger.error(f"Unknown phase: {phase_name}")
                continue

            # Skip phases already completed in a prior run
            if resume and state.phases.get(phase_name, PhaseState()).status == PhaseStatus.COMPLETED:
                logger.info(f"Skipping {phase_name} (already completed)")
                continue

            state.phases[phase_name] = PhaseState()
            await self._run_phase(phase_name, state, workspace, feature_request)

            # Checkpoint after every phase
            self._save_state(state, workspace)

            if state.phases[phase_name].status == PhaseStatus.FAILED:
                logger.error(f"Phase {phase_name} failed, stopping pipeline")
                break

            # Handle review cycle loop
            if phase_name == "reviewer" and not single_phase:
                await self._handle_review_cycle(state, workspace, feature_request)

        self.run_logger.log_event("run_complete", {
            "total_cost_usd": state.total_cost_usd,
            "phases": {k: v.model_dump() for k, v in state.phases.items()},
        })

        self._save_state(state, workspace)
        return state

    async def _run_phase(
        self,
        phase_name: str,
        state: RunState,
        workspace: Path,
        feature_request: str,
    ) -> None:
        """Run a single phase of the pipeline."""
        phase_def = PHASE_DEFINITIONS[phase_name]
        phase_config = self.config.phases.get(phase_name)
        agent_config = self.config.agents.get(phase_def.agent_name)
        phase_state = state.phases[phase_name]

        phase_state.status = PhaseStatus.RUNNING
        if agent_config:
            phase_state.model_tier = agent_config.model

        logger.info(f"=== Phase: {phase_name} (model: {phase_state.model_tier.value}) ===")

        if phase_name == "engineer":
            await self._run_engineer_phase(state, workspace, feature_request)
            return

        prompt = phase_def.build_prompt(feature_request, workspace, self.config)

        if self.dry_run:
            logger.info(f"[DRY RUN] Would invoke {phase_def.agent_name} with prompt:\n{prompt[:500]}...")
            phase_state.status = PhaseStatus.COMPLETED
            return

        max_retries = phase_config.max_retries if phase_config else 2
        result = await self._invoke_with_retry(
            agent_name=phase_def.agent_name,
            prompt=prompt,
            model=phase_state.model_tier,
            max_turns=agent_config.max_turns if agent_config else 40,
            max_retries=max_retries,
            escalation_model=agent_config.escalation_model if agent_config else None,
            workspace=workspace,
        )

        phase_state.cost_usd = result.cost_usd
        state.total_cost_usd += result.cost_usd

        if not result.success:
            phase_state.status = PhaseStatus.FAILED
            phase_state.error = result.error
            return

        # Validate output artifacts
        for artifact_name in phase_def.output_artifacts:
            artifact_path = workspace / "artifacts" / f"{artifact_name}.json"
            validation = validate_artifact_file(artifact_path, artifact_name)
            if not validation.valid:
                logger.warning(
                    f"Artifact {artifact_name} validation failed: {validation.errors}"
                )
                phase_state.status = PhaseStatus.FAILED
                phase_state.error = f"Artifact validation failed: {validation.errors}"
                return

        phase_state.status = PhaseStatus.COMPLETED

    async def _run_engineer_phase(
        self,
        state: RunState,
        workspace: Path,
        feature_request: str,
    ) -> None:
        """Run the engineer phase — potentially parallel tasks."""
        phase_state = state.phases["engineer"]
        agent_config = self.config.agents.get("engineer")
        tasks = get_engineer_tasks(workspace)

        if not tasks:
            logger.warning("No engineer tasks found, skipping engineer phase")
            phase_state.status = PhaseStatus.COMPLETED
            return

        state.engineering_tasks = [
            EngTaskState(task_id=t["task_id"]) for t in tasks
        ]

        if self.dry_run:
            for task in tasks:
                prompt = build_engineer_prompt(feature_request, workspace, self.config, task)
                logger.info(f"[DRY RUN] Would invoke engineer for {task['task_id']}:\n{prompt[:300]}...")
            phase_state.status = PhaseStatus.COMPLETED
            return

        # Check for file conflicts to decide parallel vs sequential
        parallel_ok = self.config.phases.get("engineer", None)
        use_parallel = parallel_ok and parallel_ok.parallel and not _tasks_have_file_conflicts(tasks)

        if use_parallel:
            await self._run_engineers_parallel(state, workspace, feature_request, tasks)
        else:
            await self._run_engineers_sequential(state, workspace, feature_request, tasks)

        failed = [t for t in state.engineering_tasks if t.status == TaskStatus.FAILED]
        if failed:
            phase_state.status = PhaseStatus.FAILED
            phase_state.error = f"Tasks failed: {[t.task_id for t in failed]}"
        else:
            phase_state.status = PhaseStatus.COMPLETED

    async def _run_engineers_parallel(
        self,
        state: RunState,
        workspace: Path,
        feature_request: str,
        tasks: list[dict[str, Any]],
    ) -> None:
        """Run engineer tasks in parallel with isolated worktrees."""
        invocations = []
        for task in tasks:
            prompt = build_engineer_prompt(feature_request, workspace, self.config, task)
            agent_config = self.config.agents.get("engineer")
            invocations.append(AgentInvocation(
                agent_name="engineer",
                prompt=prompt,
                model=agent_config.model if agent_config else ModelTier.SONNET,
                max_turns=agent_config.max_turns if agent_config else 80,
                workspace_dir=str(workspace),
                project_root=str(self.project_root),
                isolation="worktree",
            ))

        results = await invoke_agents_parallel(invocations)

        for task_state, result in zip(state.engineering_tasks, results):
            task_state.status = TaskStatus.COMPLETED if result.success else TaskStatus.FAILED
            state.total_cost_usd += result.cost_usd

    async def _run_engineers_sequential(
        self,
        state: RunState,
        workspace: Path,
        feature_request: str,
        tasks: list[dict[str, Any]],
    ) -> None:
        """Run engineer tasks sequentially."""
        agent_config = self.config.agents.get("engineer")
        for i, task in enumerate(tasks):
            task_state = state.engineering_tasks[i]
            task_state.status = TaskStatus.IN_PROGRESS

            prompt = build_engineer_prompt(feature_request, workspace, self.config, task)
            result = await self._invoke_with_retry(
                agent_name="engineer",
                prompt=prompt,
                model=agent_config.model if agent_config else ModelTier.SONNET,
                max_turns=agent_config.max_turns if agent_config else 80,
                max_retries=self.config.phases.get("engineer", None).max_retries if self.config.phases.get("engineer") else 2,
                escalation_model=agent_config.escalation_model if agent_config else None,
                workspace=workspace,
            )

            task_state.status = TaskStatus.COMPLETED if result.success else TaskStatus.FAILED
            state.total_cost_usd += result.cost_usd

            if not result.success:
                logger.error(f"Task {task['task_id']} failed: {result.error}")

    async def _handle_review_cycle(
        self,
        state: RunState,
        workspace: Path,
        feature_request: str,
    ) -> None:
        """Handle review feedback loop: reviewer -> engineer -> qa -> reviewer."""
        review_path = workspace / "artifacts" / "review.json"

        while state.review_cycles < state.max_review_cycles:
            if not review_path.exists():
                break

            with open(review_path) as f:
                review_data = json.load(f)

            verdict = review_data.get("verdict", "approve")
            if verdict == ReviewVerdict.APPROVE.value:
                logger.info("Review approved!")
                break

            state.review_cycles += 1
            logger.info(f"Review cycle {state.review_cycles}/{state.max_review_cycles}: {verdict}")

            if state.review_cycles >= state.max_review_cycles:
                logger.warning("Max review cycles reached, escalating to human")
                break

            # Re-run engineer phase with review feedback
            state.phases["engineer"] = PhaseState()
            engineer_prompt = build_engineer_prompt(
                feature_request, workspace, self.config
            ) + f"\n\n## Review Feedback\n\n```json\n{json.dumps(review_data, indent=2)}\n```\n\nAddress the issues raised in the review."

            if not self.dry_run:
                agent_config = self.config.agents.get("engineer")
                result = await self._invoke_with_retry(
                    agent_name="engineer",
                    prompt=engineer_prompt,
                    model=agent_config.model if agent_config else ModelTier.SONNET,
                    max_turns=agent_config.max_turns if agent_config else 80,
                    max_retries=2,
                    escalation_model=agent_config.escalation_model if agent_config else None,
                    workspace=workspace,
                )
                state.total_cost_usd += result.cost_usd
                if not result.success:
                    state.phases["engineer"].status = PhaseStatus.FAILED
                    break

            # Re-run QA
            state.phases["qa"] = PhaseState()
            await self._run_phase("qa", state, workspace, feature_request)
            if state.phases["qa"].status == PhaseStatus.FAILED:
                break

            # Re-run reviewer with previous review context
            state.phases["reviewer"] = PhaseState()
            reviewer_prompt = build_reviewer_prompt(
                feature_request, workspace, self.config,
                review_cycle=state.review_cycles + 1,
                previous_review=review_data,
            )

            if not self.dry_run:
                agent_config = self.config.agents.get("reviewer")
                result = await self._invoke_with_retry(
                    agent_name="reviewer",
                    prompt=reviewer_prompt,
                    model=agent_config.model if agent_config else ModelTier.OPUS,
                    max_turns=agent_config.max_turns if agent_config else 30,
                    max_retries=1,
                    escalation_model=None,
                    workspace=workspace,
                )
                state.total_cost_usd += result.cost_usd

    async def _invoke_with_retry(
        self,
        agent_name: str,
        prompt: str,
        model: ModelTier,
        max_turns: int,
        max_retries: int,
        escalation_model: ModelTier | None,
        workspace: Path,
    ) -> AgentResult:
        """Invoke an agent with retry and model escalation."""
        current_model = model

        for attempt in range(max_retries + 1):
            if self.run_logger:
                self.run_logger.log_event("agent_invoke", {
                    "agent": agent_name,
                    "model": current_model.value,
                    "attempt": attempt + 1,
                })

            # Budget check
            if self.config.max_budget_usd and self.run_logger:
                status = self.run_logger.check_budget(self.config.max_budget_usd)
                if status == "exceeded":
                    return AgentResult(
                        success=False,
                        error=f"Budget exceeded: ${self.run_logger.cumulative_cost_usd:.2f} >= ${self.config.max_budget_usd:.2f}",
                    )
                if status == "warning":
                    self.run_logger.log_event("budget_warning", {
                        "cumulative_cost_usd": self.run_logger.cumulative_cost_usd,
                        "max_budget_usd": self.config.max_budget_usd,
                    })

            result = await invoke_agent(AgentInvocation(
                agent_name=agent_name,
                prompt=prompt,
                model=current_model,
                max_turns=max_turns,
                workspace_dir=str(workspace),
                project_root=str(self.project_root),
            ))

            if self.run_logger:
                self.run_logger.log_event("agent_result", {
                    "agent": agent_name,
                    "success": result.success,
                    "cost_usd": result.cost_usd,
                    "attempt": attempt + 1,
                })

            if result.success:
                return result

            logger.warning(f"Agent {agent_name} failed (attempt {attempt + 1}): {result.error}")

            # Escalate model on retry
            if escalation_model and current_model != escalation_model:
                logger.info(f"Escalating {agent_name} from {current_model.value} to {escalation_model.value}")
                current_model = escalation_model

        return result

    def _save_state(self, state: RunState, workspace: Path) -> None:
        """Save the run state to disk."""
        state_path = workspace / "state.json"
        with open(state_path, "w") as f:
            json.dump(state.model_dump(), f, indent=2)

    def _load_state(self, workspace: Path) -> RunState | None:
        """Load a prior run state from disk, if it exists."""
        state_path = workspace / "state.json"
        if not state_path.exists():
            return None
        with open(state_path) as f:
            data = json.load(f)
        return RunState.model_validate(data)


def _tasks_have_file_conflicts(tasks: list[dict[str, Any]]) -> bool:
    """Check if any tasks modify the same files."""
    seen_files: set[str] = set()
    for task in tasks:
        files = set(task.get("files_to_modify", []))
        if files & seen_files:
            return True
        seen_files |= files
    return False
