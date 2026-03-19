"""Workflow engine — executes workflow steps with task management, gates, and failure handling."""

from __future__ import annotations

import asyncio
import json
import logging
import re
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


# ---------------------------------------------------------------------------
# ArtifactCache - Phase-scoped in-memory cache for artifact files
# ---------------------------------------------------------------------------

class ArtifactCache:
    """In-memory cache for artifact JSON files with phase-scoped lifetime.

    Loads artifact files once at phase start and reuses them across all tasks,
    eliminating redundant disk reads (60+ per phase with 20 tasks × 3 artifacts).

    Performance impact: ~60-300ms savings per phase (redundant I/O eliminated).
    """

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.artifacts_dir = workspace / "artifacts"
        self._cache: dict[str, dict[str, Any]] = {}
        self._load_times: dict[str, float] = {}

    def load_artifact(self, artifact_name: str) -> dict[str, Any] | None:
        """Load an artifact from cache or disk (cache miss → load and store).

        Returns None if the artifact file does not exist or is invalid JSON.
        """
        if artifact_name in self._cache:
            return self._cache[artifact_name]

        artifact_path = self.artifacts_dir / f"{artifact_name}.json"
        if not artifact_path.exists():
            return None

        try:
            start = time.time()
            data = json.loads(artifact_path.read_text())
            self._cache[artifact_name] = data
            self._load_times[artifact_name] = time.time() - start
            return data
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to load artifact '{artifact_name}': {e}")
            return None

    def get(self, artifact_name: str) -> dict[str, Any] | None:
        """Get artifact from cache (does not load from disk)."""
        return self._cache.get(artifact_name)

    def clear(self):
        """Clear the cache (typically called at phase/step end)."""
        self._cache.clear()
        self._load_times.clear()

    def stats(self) -> dict[str, Any]:
        """Return cache statistics for logging."""
        total_time = sum(self._load_times.values())
        return {
            "artifacts_cached": len(self._cache),
            "load_time_ms": round(total_time * 1000, 2),
            "times_per_artifact": {k: round(v * 1000, 2) for k, v in self._load_times.items()},
        }


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
    # MCP & integration specialists
    "mcp_server_engineer", "mcp_integration_test_engineer",
    "chatbot_engineer", "social_media_integration_engineer",
})

# Maps assigned_role strings from tasks.json to AgentRole enums
_ROLE_STRING_TO_ENUM: dict[str, AgentRole] = {
    # Legacy alias — "engineer" in old tasks.json maps to backend_engineer
    "engineer": AgentRole.BACKEND_ENGINEER,
    # Auto-map every AgentRole.value to its enum (covers all 40 roles)
    **{role.value: role for role in AgentRole},
}


# ---------------------------------------------------------------------------
# Artifact rescue helpers (module-level, stateless)
# ---------------------------------------------------------------------------

def _parse_json_blocks(text: str) -> list[str]:
    """Extract JSON blocks from agent output text.

    Handles:
    - Fenced markdown blocks: ```json ... ```
    - Raw top-level JSON objects (balanced braces)
    """
    blocks: list[str] = []

    # 1. Fenced ```json ... ``` blocks
    for match in re.finditer(r"```(?:json)?\s*\n(.*?)\n\s*```", text, re.DOTALL):
        candidate = match.group(1).strip()
        if candidate.startswith("{"):
            blocks.append(candidate)

    # 2. If no fenced blocks, try raw balanced-brace extraction
    if not blocks:
        depth = 0
        start = None
        for i, ch in enumerate(text):
            if ch == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0 and start is not None:
                    candidate = text[start : i + 1]
                    # Skip tiny objects (not real artifacts)
                    if len(candidate) > 100:
                        blocks.append(candidate)
                    start = None

    return blocks


# Manual signature overrides for artifacts that need custom key hints
# beyond what schemas provide.
_MANUAL_SIGNATURE_OVERRIDES: dict[str, set[str]] = {
    "ux_specification": {"screens", "user_flows", "design_tokens"},
    "competitor_analysis": {"competitors", "comparison", "market_position"},
    "user_psychology": {"personas", "user_needs", "behavioral_patterns"},
    "user_psychology_research": {"personas", "user_needs", "behavioral_patterns"},
    "security_review": {"vulnerabilities", "risk_assessment", "recommendations"},
}


def _build_artifact_signatures() -> dict[str, set[str]]:
    """Build artifact signatures from JSON schema 'required' fields.

    Falls back to all 'properties' keys when 'required' is absent.
    Manual overrides are merged on top for edge cases.
    """
    signatures: dict[str, set[str]] = {}
    schemas_dir = Path(__file__).resolve().parents[1] / "schemas"
    if schemas_dir.is_dir():
        for schema_file in schemas_dir.glob("*.schema.json"):
            artifact_name = schema_file.stem.replace(".schema", "")
            try:
                schema = json.loads(schema_file.read_text())
                required = set(schema.get("required", []))
                properties = set(schema.get("properties", {}).keys())
                signatures[artifact_name] = required or properties
            except (json.JSONDecodeError, OSError):
                pass
    signatures.update(_MANUAL_SIGNATURE_OVERRIDES)
    return signatures


_ARTIFACT_SIGNATURES: dict[str, set[str]] = _build_artifact_signatures()


def _normalize_key(key: str) -> str:
    """Normalize a JSON key to snake_case for signature matching."""
    key = key.replace("-", "_")
    s1 = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", key)
    return re.sub(r"([a-z\d])([A-Z])", r"\1_\2", s1).lower()


def _match_json_to_artifact(data: dict, candidates: list[str]) -> str | None:
    """Match a parsed JSON dict to one of the candidate artifact names.

    Uses field-signature heuristics with key normalization (so camelCase
    keys can match snake_case signatures), then falls back to a
    single-candidate match.
    """
    # Normalize incoming keys to snake_case for comparison
    keys = {_normalize_key(k) for k in data.keys()}

    best_match: str | None = None
    best_score = 0

    for candidate in candidates:
        sig = _ARTIFACT_SIGNATURES.get(candidate, set())
        if sig:
            overlap = len(keys & sig)
            if overlap > best_score:
                best_score = overlap
                best_match = candidate

    # If no signature matched but only one candidate, just use it
    if best_match is None and len(candidates) == 1:
        best_match = candidates[0]

    return best_match


# Standalone rescue functions — usable from both WorkflowEngine and legacy engine.


def _normalize_artifact_stem(name: str) -> str:
    """Normalize artifact filename stem for fuzzy matching.

    Strips separators and lowercases so that 'benchmark_report', 'benchmark-report',
    'benchmarkReport', and 'BenchmarkReport' all normalize to 'benchmarkreport'.
    """
    return name.lower().replace("-", "").replace("_", "").replace(" ", "")


def _rescue_misplaced_artifacts(
    missing: list[str], workspace: Path, project_root: Path | None = None,
) -> list[str]:
    """Search for artifacts written to wrong paths and move them.

    Uses two-pass matching:
    1. Exact filename match in common wrong directories
    2. Fuzzy match — normalizes stems to catch kebab-case, camelCase, etc.
    """
    artifacts_dir = workspace / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    rescued: list[str] = []
    search_roots = [workspace, project_root, workspace / "workspace"]

    for artifact_name in list(missing):
        filename = f"{artifact_name}.json"
        canonical = artifacts_dir / filename
        if canonical.exists():
            continue

        found = False
        # Pass 1: exact filename match (fast path)
        for root in search_roots:
            if not root or not root.exists():
                continue
            candidate = root / filename
            if candidate.exists() and candidate != canonical:
                try:
                    candidate.rename(canonical)
                    logger.info(f"Rescued misplaced artifact '{artifact_name}': {candidate} → {canonical}")
                    rescued.append(artifact_name)
                    found = True
                    break
                except OSError:
                    pass
            for depth_glob in [f"*/{filename}", f"*/*/{filename}"]:
                for match in root.glob(depth_glob):
                    if match != canonical and match.is_file():
                        try:
                            match.rename(canonical)
                            logger.info(f"Rescued misplaced artifact '{artifact_name}': {match} → {canonical}")
                            rescued.append(artifact_name)
                            found = True
                            break
                        except OSError:
                            pass
                if found:
                    break
            if found:
                break

        if found:
            continue

        # Pass 2: fuzzy filename match — normalize stems to catch naming variants
        expected_stem = _normalize_artifact_stem(artifact_name)
        for root in search_roots:
            if not root or not root.exists():
                continue
            for json_file in root.rglob("*.json"):
                if json_file == canonical or not json_file.is_file():
                    continue
                if _normalize_artifact_stem(json_file.stem) == expected_stem:
                    try:
                        json_file.rename(canonical)
                        logger.info(
                            f"Fuzzy-rescued artifact '{artifact_name}': "
                            f"found as '{json_file.name}' at {json_file.parent} → {canonical}"
                        )
                        rescued.append(artifact_name)
                        found = True
                        break
                    except OSError:
                        pass
            if found:
                break

    return rescued


def _rescue_artifacts_from_output(
    output: str, missing: list[str], workspace: Path,
) -> list[str]:
    """Extract artifact JSON from agent output text and write to disk.

    Returns artifact names that were rescued.
    """
    if not output or not missing:
        return []
    artifacts_dir = workspace / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    json_blocks = _parse_json_blocks(output)
    if not json_blocks:
        return []

    rescued: list[str] = []
    remaining = list(missing)

    for block in sorted(json_blocks, key=len, reverse=True):
        if not remaining:
            break
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        match = _match_json_to_artifact(data, remaining)
        if match:
            artifact_path = artifacts_dir / f"{match}.json"
            try:
                artifact_path.write_text(json.dumps(data, indent=2))
                logger.info(f"Rescued artifact '{match}' from agent output ({len(block)} bytes)")
                rescued.append(match)
                remaining.remove(match)
            except OSError as e:
                logger.warning(f"Failed to write rescued artifact '{match}': {e}")
    return rescued


# ---------------------------------------------------------------------------
# Phase 3 optimization: Fine-grained dynamic task scheduling
# ---------------------------------------------------------------------------

class TaskReadinessTracker:
    """Track task completion status for dynamic dependency-based scheduling.

    Instead of processing tasks in fixed waves, this tracker enables tasks
    to start as soon as their dependencies complete, reducing idle time.

    Phase 3 optimization: Saves 10-150 seconds per pipeline by eliminating
    wave barriers for independent tasks.
    """

    def __init__(self, tasks: list[WorkflowTaskState]):
        """Initialize tracker with the full task list."""
        self.tasks_by_id = {t.task_id: t for t in tasks}
        self.completed: set[str] = set()
        self.in_progress: set[str] = set()
        self.pending: set[str] = {t.task_id for t in tasks}

    def is_ready(self, task: WorkflowTaskState) -> bool:
        """Check if all dependencies of a task are completed."""
        return all(dep_id in self.completed for dep_id in task.dependencies)

    def get_ready_tasks(self) -> list[WorkflowTaskState]:
        """Return all tasks whose dependencies are satisfied and not yet started."""
        ready = []
        for task_id in self.pending:
            task = self.tasks_by_id[task_id]
            if self.is_ready(task):
                ready.append(task)
        return ready

    def mark_started(self, task_id: str) -> None:
        """Mark a task as in-progress."""
        if task_id in self.pending:
            self.pending.discard(task_id)
            self.in_progress.add(task_id)

    def mark_completed(self, task_id: str) -> None:
        """Mark a task as completed."""
        if task_id in self.in_progress:
            self.in_progress.discard(task_id)
            self.completed.add(task_id)

    def has_pending(self) -> bool:
        """Check if there are any pending or in-progress tasks."""
        return bool(self.pending or self.in_progress)

    def mark_failed(self, task_id: str, blocking_deps: set[str]) -> None:
        """Mark a task as failed and update downstream task statuses.

        Cascades failure to all tasks that directly depend on the failed task.
        Blocked tasks are removed from pending so has_pending() exits cleanly
        instead of triggering a false deadlock detection.
        """
        if task_id in self.in_progress:
            self.in_progress.discard(task_id)
        # Cascade failure: find all downstream tasks that depend on the failed task
        # and mark them as BLOCKED, removing from pending to prevent deadlock detection.
        for other_id, other_task in self.tasks_by_id.items():
            if task_id in other_task.dependencies:
                if other_id in self.pending:
                    other_task.status = TaskStatus.BLOCKED
                    other_task.error = f"Blocked by failed dependency: {task_id}"
                    self.pending.discard(other_id)


class TaskScheduler:
    """Schedule tasks based on dynamic dependency graph (Phase 3 optimization).

    Replaces wave-based execution with fine-grained scheduling that starts
    tasks as soon as their dependencies complete. Still respects file
    conflicts which require serialization.
    """

    def __init__(
        self,
        tasks: list[WorkflowTaskState],
        partition_fn,
        execute_task_fn,
        knowledge_rebuild_fn=None,
    ):
        """Initialize scheduler with task list and callbacks.

        Args:
            tasks: List of workflow tasks to execute
            partition_fn: Function to partition tasks by file conflicts
            execute_task_fn: Async callback to execute a single task
            knowledge_rebuild_fn: Optional async callback to rebuild knowledge index
        """
        self.tasks = tasks
        self.readiness = TaskReadinessTracker(tasks)
        self.partition_fn = partition_fn
        self.execute_task_fn = execute_task_fn
        self.knowledge_rebuild_fn = knowledge_rebuild_fn
        self.results: dict[str, Any] = {}
        self._last_rebuild_count = 0

    async def schedule_all(self) -> dict[str, Any]:
        """Execute all tasks dynamically based on dependencies.

        Returns:
            Dictionary mapping task_id to result
        """
        iteration = 0
        rebuild_frequency = max(1, len(self.tasks) // 3)  # Rebuild every ~3 tasks

        while self.readiness.has_pending():
            iteration += 1
            ready_tasks = self.readiness.get_ready_tasks()

            if not ready_tasks:
                # Deadlock: no tasks ready but some pending
                pending_ids = list(self.readiness.pending | self.readiness.in_progress)
                raise RuntimeError(
                    f"Deadlock: {len(pending_ids)} tasks pending but none ready. "
                    f"Tasks: {pending_ids}. Check for circular dependencies or "
                    f"missing dependency declarations."
                )

            logger.info(
                f"Scheduling iteration {iteration}: {len(ready_tasks)} ready tasks "
                f"({len(self.readiness.completed)} completed, "
                f"{len(self.readiness.in_progress)} in progress)"
            )

            # Partition ready tasks by file conflicts
            no_conflict, conflict = self.partition_fn(ready_tasks)

            # Execute non-conflicting tasks in parallel
            if no_conflict:
                tasks_to_run = []
                for task in no_conflict:
                    self.readiness.mark_started(task.task_id)
                    tasks_to_run.append(self._execute_task(task))

                logger.info(f"  Launching {len(tasks_to_run)} parallel tasks")
                results = await asyncio.gather(*tasks_to_run, return_exceptions=True)

                for task, result in zip(no_conflict, results):
                    if isinstance(result, Exception):
                        logger.error(f"  {task.task_id} failed: {result}")
                        task.status = TaskStatus.FAILED
                        task.error = str(result)
                        # Include failed task in results so callers can inspect outcomes;
                        # downstream tasks are removed from pending by mark_failed (cascade).
                        self.results[task.task_id] = {"error": str(result), "failed": True}
                        self.readiness.mark_failed(task.task_id, {task.task_id})
                    else:
                        self.results[task.task_id] = result
                        self.readiness.mark_completed(task.task_id)

            # Execute conflicting tasks sequentially
            for task in conflict:
                logger.info(f"  Serializing {task.task_id} (file conflict)")
                self.readiness.mark_started(task.task_id)
                try:
                    result = await self.execute_task_fn(task)
                    self.results[task.task_id] = result
                    self.readiness.mark_completed(task.task_id)
                except Exception as e:
                    logger.error(f"  {task.task_id} failed: {e}")
                    task.status = TaskStatus.FAILED
                    task.error = str(e)
                    # Include failed task in results (same contract as parallel path)
                    self.results[task.task_id] = {"error": str(e), "failed": True}
                    self.readiness.mark_failed(task.task_id, {task.task_id})

            # Periodically rebuild knowledge index
            completed_now = len(self.readiness.completed)
            if (
                self.knowledge_rebuild_fn
                and completed_now >= self._last_rebuild_count + rebuild_frequency
            ):
                logger.info(f"  Rebuilding knowledge index after {completed_now} tasks...")
                try:
                    await self.knowledge_rebuild_fn()
                    self._last_rebuild_count = completed_now
                except Exception as e:
                    logger.warning(f"  Knowledge rebuild error (non-fatal): {e}")

        return self.results

    async def _execute_task(self, task: WorkflowTaskState) -> Any:
        """Execute a single task via callback."""
        return await self.execute_task_fn(task)


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
        # Temporary storage for agent outputs — used to rescue artifacts
        # when agents output JSON in response text instead of calling Write tool.
        self._task_outputs: dict[str, str] = {}  # task_id -> result.output
        self._task_written_files: list[str] = []  # file paths from Write tool calls
        # Phase-scoped artifact cache (initialized per step, cleared at step end)
        self._artifact_cache: ArtifactCache | None = None

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

        # Initialize artifact cache for this step (phase-scoped lifetime)
        workspace = Path(self.state.workspace_dir)
        self._artifact_cache = ArtifactCache(workspace)
        # Preload all input artifacts required by this step to avoid per-task disk reads
        for artifact_name in step.inputs:
            self._artifact_cache.load_artifact(artifact_name)
        cache_stats = self._artifact_cache.stats()
        logger.info(f"Artifact cache loaded for step '{step.name}': {cache_stats}")

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
            # Clear artifact cache when step completes
            if self._artifact_cache:
                self._artifact_cache.clear()
                self._artifact_cache = None

        workspace = Path(self.state.workspace_dir)

        if success:
            # Artifact rescue & validation retry loop.
            max_art_retries = step.max_retries
            missing_artifacts: list[str] = []
            invalid_artifacts: dict[str, list[str]] = {}  # name -> errors

            for artifact_attempt in range(max_art_retries + 1):
                # --- Check for missing artifacts ---
                missing_artifacts = self._check_missing_artifacts(step, workspace)

                if missing_artifacts:
                    # Layer 1: Search for misplaced files (agent wrote to wrong path / wrong name)
                    self._rescue_misplaced_artifacts(missing_artifacts, workspace)
                    missing_artifacts = self._check_missing_artifacts(step, workspace)

                if missing_artifacts:
                    # Layer 1b: Check files the agent actually wrote via Write tool
                    rescued = self._rescue_from_written_files(missing_artifacts, workspace)
                    if rescued:
                        logger.info(f"Rescued {len(rescued)} artifact(s) from agent-written files: {rescued}")
                    missing_artifacts = self._check_missing_artifacts(step, workspace)

                if missing_artifacts:
                    # Layer 2: Extract artifact JSON from agent output text
                    extracted = self._rescue_artifacts_from_output(missing_artifacts, workspace)
                    if extracted:
                        logger.info(f"Auto-rescued {len(extracted)} artifact(s) from agent output: {extracted}")
                    missing_artifacts = self._check_missing_artifacts(step, workspace)

                if missing_artifacts:
                    # Layer 3: Artifact writer agent
                    missing_files = ", ".join(f"{a}.json" for a in missing_artifacts)
                    logger.warning(
                        f"Artifact(s) not found after step '{step.name}' (attempt {artifact_attempt + 1}) — "
                        f"running artifact-writer retry: {missing_files}"
                    )
                    retry_tasks = self._create_artifact_writer_tasks(
                        step, tasks, missing_artifacts, workspace,
                        escalate=(artifact_attempt > 0),
                    )
                    try:
                        retry_success = await self._execute_step_tasks(step, retry_tasks)
                    finally:
                        await self._stop_knowledge_watcher()
                    if retry_success:
                        missing_artifacts = self._check_missing_artifacts(step, workspace)

                if missing_artifacts:
                    # Artifacts are truly missing — no point validating
                    break

                # --- All files exist — run schema + Pydantic validation ---
                invalid_artifacts = {}
                for artifact_name in step.outputs:
                    artifact_path = workspace / "artifacts" / f"{artifact_name}.json"
                    validation = validate_artifact_file(artifact_path, artifact_name)
                    if not validation.valid:
                        invalid_artifacts[artifact_name] = validation.errors
                        logger.warning(
                            f"Artifact {artifact_name} validation failed "
                            f"(attempt {artifact_attempt + 1}): {validation.errors}"
                        )

                if not invalid_artifacts:
                    break  # All artifacts present and valid

                # Validation failed — try repair if we have retries left
                if artifact_attempt < max_art_retries:
                    logger.info(
                        f"Running validation repair for {list(invalid_artifacts)} "
                        f"(attempt {artifact_attempt + 1}/{max_art_retries})"
                    )
                    repair_tasks = self._create_validation_repair_tasks(
                        step, invalid_artifacts, workspace,
                        escalate=(artifact_attempt > 0),
                    )
                    try:
                        await self._execute_step_tasks(step, repair_tasks)
                    finally:
                        await self._stop_knowledge_watcher()
                # Loop back to re-check

            # --- Post-loop: full step retry as final fallback ---
            if (missing_artifacts or invalid_artifacts) and not self.state.phases[phase_key].artifact_retry_exhausted:
                # Budget check before expensive full-step retry
                budget_ok = True
                if self.run_logger and self.config.max_budget_usd:
                    budget_ok = self.run_logger.check_budget(self.config.max_budget_usd) != "exceeded"

                if budget_ok:
                    self.state.phases[phase_key].artifact_retry_exhausted = True
                    error_context = self._build_artifact_error_context(missing_artifacts, invalid_artifacts)
                    logger.warning(
                        f"Artifact rescue exhausted for '{step.name}' — "
                        f"performing full step re-execution with error context"
                    )
                    for task in tasks:
                        task.status = TaskStatus.PENDING
                        task.description += f"\n\n{error_context}"
                        task.retry_count = 0
                    try:
                        success = await self._execute_step_tasks(step, tasks)
                    finally:
                        await self._stop_knowledge_watcher()
                    if success:
                        # Re-validate after full retry
                        missing_artifacts = self._check_missing_artifacts(step, workspace)
                        invalid_artifacts = {}
                        if not missing_artifacts:
                            for artifact_name in step.outputs:
                                artifact_path = workspace / "artifacts" / f"{artifact_name}.json"
                                validation = validate_artifact_file(artifact_path, artifact_name)
                                if not validation.valid:
                                    invalid_artifacts[artifact_name] = validation.errors

            # --- Final verdict ---
            if missing_artifacts:
                missing_files = ", ".join(f"{a}.json" for a in missing_artifacts)
                self.state.phases[phase_key].status = PhaseStatus.FAILED
                self.state.phases[phase_key].error = f"Artifact(s) not written to disk: {missing_files}"
                return "failed"

            if invalid_artifacts:
                self.state.phases[phase_key].status = PhaseStatus.FAILED
                self.state.phases[phase_key].error = f"Artifact validation: {invalid_artifacts}"
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
            update_cumulative_context(
                workspace=workspace,
                phase_name=step.name,
                artifacts=list(step.outputs),
            )
            await self._refresh_knowledge(step.name)
            self._task_outputs.clear()
            self._task_written_files.clear()
            return "completed"
        else:
            self.state.phases[phase_key].status = PhaseStatus.FAILED
            self._task_outputs.clear()
            self._task_written_files.clear()
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

    # ------------------------------------------------------------------
    # Artifact rescue — delegates to module-level standalone functions
    # ------------------------------------------------------------------

    def _rescue_misplaced_artifacts(
        self, missing: list[str], workspace: Path,
    ) -> list[str]:
        """Search for artifacts written to wrong paths and move them."""
        return _rescue_misplaced_artifacts(missing, workspace, self.project_root)

    def _rescue_from_written_files(
        self, missing: list[str], workspace: Path,
    ) -> list[str]:
        """Rescue artifacts by checking files the agent actually wrote via Write tool.

        The agent may have written the artifact to a wrong filename (e.g.,
        benchmark-report.json instead of benchmark_report.json). This method
        checks recorded Write tool paths against missing artifacts using
        fuzzy filename matching.
        """
        if not self._task_written_files:
            return []

        artifacts_dir = workspace / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        rescued: list[str] = []

        for artifact_name in list(missing):
            canonical = artifacts_dir / f"{artifact_name}.json"
            if canonical.exists():
                continue

            expected_stem = _normalize_artifact_stem(artifact_name)

            for written_path_str in self._task_written_files:
                written_path = Path(written_path_str)
                if not written_path.exists() or not written_path.is_file():
                    continue
                if written_path == canonical:
                    continue

                # Check if this written file matches the expected artifact
                if _normalize_artifact_stem(written_path.stem) == expected_stem:
                    try:
                        written_path.rename(canonical)
                        logger.info(
                            f"Rescued artifact '{artifact_name}' from agent-written file: "
                            f"'{written_path.name}' → {canonical}"
                        )
                        rescued.append(artifact_name)
                        break
                    except OSError:
                        pass

        return rescued

    def _rescue_artifacts_from_output(
        self, missing: list[str], workspace: Path,
    ) -> list[str]:
        """Extract artifact JSON from stored agent output text."""
        if not self._task_outputs:
            return []
        combined_output = "\n\n".join(self._task_outputs.values())
        return _rescue_artifacts_from_output(combined_output, missing, workspace)

    def _create_artifact_writer_tasks(
        self,
        step: WorkflowStepDefinition,
        original_tasks: list[WorkflowTaskState],
        missing_artifacts: list[str],
        workspace: Path,
        escalate: bool = False,
    ) -> list[WorkflowTaskState]:
        """Create lightweight retry tasks focused solely on writing artifacts.

        Instead of re-running the full agent, this creates a targeted prompt
        that includes any prior output and asks the agent to just write the file.
        Uses fewer max_turns since the only job is to call Write.

        Phase 2 optimization: Batches missing artifacts in groups of up to 3
        to reduce the number of serial writer task attempts.
        """
        artifacts_dir = workspace / "artifacts"

        # Gather any prior output to feed into the retry
        prior_output = "\n\n".join(self._task_outputs.values()).strip()
        prior_section = ""
        if prior_output:
            # Cap at 16KB to avoid prompt bloat
            if len(prior_output) > 16000:
                prior_output = prior_output[:15997] + "..."
            prior_section = (
                f"\n\n## Previous Agent Output (extract the artifact from this)\n\n"
                f"{prior_output}\n"
            )

        # Phase 2: Batch artifacts in groups of up to 3 to reduce task count
        batch_size = 3
        batches = [
            missing_artifacts[i : i + batch_size]
            for i in range(0, len(missing_artifacts), batch_size)
        ]

        retry_tasks: list[WorkflowTaskState] = []
        for batch_idx, artifact_batch in enumerate(batches, 1):
            batch_ids = ", ".join(artifact_batch)

            # Build context for each artifact in this batch
            batch_context = []
            all_schemas = []

            from orchestrator.models import ARTIFACT_MODELS

            for artifact_name in artifact_batch:
                artifact_path = artifacts_dir / f"{artifact_name}.json"
                batch_context.append(f"- {artifact_path}")

                # Get schema for this artifact
                model_cls = ARTIFACT_MODELS.get(artifact_name)
                if model_cls:
                    try:
                        schema_json = json.dumps(model_cls.model_json_schema(), indent=2)
                        if len(schema_json) <= 2000:
                            all_schemas.append(
                                f"### {artifact_name}.json Schema\n```json\n{schema_json}\n```"
                            )
                    except Exception:
                        pass

            batch_context_str = "\n".join(batch_context)
            schemas_str = "\n\n".join(all_schemas)

            # Read any input artifacts for context
            input_context = ""
            for inp in step.inputs:
                inp_path = artifacts_dir / f"{inp}.json"
                if inp_path.exists():
                    try:
                        content = inp_path.read_text()
                        if len(content) > 3000:
                            content = content[:2997] + "..."
                        input_context += f"\n### {inp}.json\n```json\n{content}\n```\n"
                    except OSError:
                        pass

            schema_section = ""
            if schemas_str:
                schema_section = f"\n## Required JSON Schemas\n\n{schemas_str}\n\n"

            description = (
                f"BATCH ARTIFACT WRITER TASK — create {len(artifact_batch)} files (batch {batch_idx}):\n\n"
                f"{batch_context_str}\n\n"
                f"## Instructions\n\n"
                f"1. Use the Write tool to create EACH file at its exact path\n"
                f"2. Each file MUST contain valid JSON\n"
                f"3. Use snake_case for all JSON keys (e.g. 'test_results', NOT 'testResults')\n"
                f"4. Do NOT output JSON in your response — ONLY use the Write tool\n"
                f"5. After writing each file, use Read to verify it exists\n"
                f"6. Create ALL {len(artifact_batch)} artifact(s) in this batch\n"
                f"{schema_section}"
                f"Feature request: {self.state.feature_request}\n"
                f"{input_context}{prior_section}"
            )

            # Use the original task's role or step's agent role
            role = original_tasks[0].assigned_role if original_tasks else step.agent_role
            retry_tasks.append(WorkflowTaskState(
                task_id=f"ARTIFACT-WRITE-BATCH-{batch_idx}",
                workflow_step=step.name,
                description=description,
                assigned_role=role,
                dependencies=[],
                expected_outputs=artifact_batch,
                override_max_turns=min(15, 5 + len(artifact_batch) * 2),  # More turns for batch
            ))
        return retry_tasks

    def _create_validation_repair_tasks(
        self,
        step: WorkflowStepDefinition,
        invalid_artifacts: dict[str, list[str]],
        workspace: Path,
        *,
        escalate: bool = False,
    ) -> list[WorkflowTaskState]:
        """Create tasks to fix artifacts that failed schema/Pydantic validation.

        Phase 2 optimization: Batches multiple invalid artifacts into single tasks
        to reduce serial repair attempts. For N artifacts, creates ceil(N/3) tasks
        instead of N tasks, reducing repair time from N×30s to ceil(N/3)×30s.
        """
        artifacts_dir = workspace / "artifacts"
        repair_tasks: list[WorkflowTaskState] = []

        # Phase 2: Batch artifacts in groups of up to 3 to reduce task count
        artifact_items = list(invalid_artifacts.items())
        batch_size = 3
        batches = [
            artifact_items[i : i + batch_size]
            for i in range(0, len(artifact_items), batch_size)
        ]

        for batch_idx, batch in enumerate(batches, 1):
            artifact_names = [name for name, _ in batch]
            batch_artifact_ids = ", ".join(artifact_names)

            # Build detailed error listing for all artifacts in this batch
            error_details = []
            for artifact_name, errors in batch:
                error_list = "\n".join(f"  - {e}" for e in errors)
                error_details.append(f"**{artifact_name}.json:**\n{error_list}")
            error_details_str = "\n\n".join(error_details)

            # Gather current content and schemas for all artifacts in batch
            batch_context = []
            for artifact_name, _ in batch:
                artifact_path = artifacts_dir / f"{artifact_name}.json"
                current_content = ""
                try:
                    raw = artifact_path.read_text()
                    current_content = raw[:4000] + "..." if len(raw) > 4000 else raw
                except OSError:
                    pass

                # Inject schema
                schema_section = ""
                from orchestrator.models import ARTIFACT_MODELS
                model_cls = ARTIFACT_MODELS.get(artifact_name)
                if model_cls:
                    try:
                        schema_json = json.dumps(model_cls.model_json_schema(), indent=2)
                        if len(schema_json) <= 2000:
                            schema_section = (
                                f"\n### {artifact_name} — Required JSON Schema\n"
                                f"```json\n{schema_json}\n```\n"
                            )
                    except Exception:
                        pass

                batch_context.append(
                    f"### {artifact_name}.json\n\n"
                    f"**Current content:**\n```json\n{current_content}\n```\n"
                    f"{schema_section}"
                )

            batch_context_str = "\n".join(batch_context)

            description = (
                f"BATCH ARTIFACT REPAIR — fix validation errors in multiple files (batch {batch_idx}):\n\n"
                f"Files to repair: {batch_artifact_ids}\n\n"
                f"## Validation Errors\n\n{error_details_str}\n\n"
                f"## Current Content & Schemas\n\n{batch_context_str}\n\n"
                f"## Instructions\n\n"
                f"1. Read each file listed above\n"
                f"2. Fix ALL the validation errors for each artifact\n"
                f"3. Write EACH corrected artifact back to its EXACT path\n"
                f"4. Use snake_case for all JSON keys\n"
                f"5. After writing, Read each file to verify it exists and is valid\n\n"
                f"Repair ALL {len(artifact_names)} artifact(s) in this batch."
            )

            # Determine role (use original task's role or step's agent role)
            role = step.agent_role

            repair_tasks.append(WorkflowTaskState(
                task_id=f"ARTIFACT-REPAIR-BATCH-{batch_idx}",
                workflow_step=step.name,
                description=description,
                assigned_role=role,
                dependencies=[],
                expected_outputs=artifact_names,
                override_max_turns=min(20, 5 + len(artifact_names) * 3),  # More turns for batch
            ))

        return repair_tasks

    def _create_validation_repair_tasks_sequential(
        self,
        step: WorkflowStepDefinition,
        invalid_artifacts: dict[str, list[str]],
        workspace: Path,
        *,
        escalate: bool = False,
    ) -> list[WorkflowTaskState]:
        """Create individual tasks to fix artifacts (pre-optimization version).

        Kept for compatibility but _create_validation_repair_tasks() now batches artifacts.
        """
        artifacts_dir = workspace / "artifacts"
        repair_tasks: list[WorkflowTaskState] = []

        for artifact_name, errors in invalid_artifacts.items():
            artifact_path = artifacts_dir / f"{artifact_name}.json"

            current_content = ""
            try:
                raw = artifact_path.read_text()
                current_content = raw[:8000] + "..." if len(raw) > 8000 else raw
            except OSError:
                pass

            error_list = "\n".join(f"- {e}" for e in errors)

            # Inject schema for guidance
            schema_section = ""
            from orchestrator.models import ARTIFACT_MODELS
            model_cls = ARTIFACT_MODELS.get(artifact_name)
            if model_cls:
                try:
                    schema_json = json.dumps(model_cls.model_json_schema(), indent=2)
                    if len(schema_json) <= 4000:
                        schema_section = (
                            f"\n### Required JSON Schema\n"
                            f"```json\n{schema_json}\n```\n"
                        )
                except Exception:
                    pass

            description = (
                f"ARTIFACT REPAIR TASK — fix validation errors in:\n\n"
                f"  {artifact_path}\n\n"
                f"The file exists but has these validation errors:\n{error_list}\n\n"
                f"Current content:\n```json\n{current_content}\n```\n"
                f"{schema_section}\n"
                f"Use snake_case for all JSON keys (e.g. 'test_results', NOT 'testResults').\n"
                f"Read the file, fix ALL the validation errors listed above, "
                f"and Write the corrected JSON back to the SAME path.\n"
                f"Then Read the file again to verify it was written correctly."
            )

            repair_tasks.append(WorkflowTaskState(
                task_id=f"ARTIFACT-REPAIR-{artifact_name}",
                workflow_step=step.name,
                description=description,
                assigned_role=step.agent_role,
                dependencies=[],
                expected_outputs=[artifact_name],
                override_max_turns=15,
            ))

        return repair_tasks

    def _build_artifact_error_context(
        self,
        missing: list[str],
        invalid: dict[str, list[str]],
    ) -> str:
        """Build error context string to append to task descriptions for full-step retry."""
        parts = ["--- ARTIFACT ERROR CONTEXT (from previous attempt) ---"]
        if missing:
            parts.append(f"Missing artifacts (not written to disk): {', '.join(f'{a}.json' for a in missing)}")
        for name, errors in invalid.items():
            parts.append(f"Artifact '{name}.json' validation errors:\n" + "\n".join(f"  - {e}" for e in errors))
        parts.append(
            "IMPORTANT: You MUST use the Write tool to create each artifact file at the exact path. "
            "Use snake_case for all JSON keys. After writing, use Read to verify the file exists."
        )
        return "\n\n".join(parts)

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
        """Execute tasks respecting their dependency graph (Phase 3 optimization).

        Uses dynamic scheduling (TaskScheduler) to start tasks as soon as their
        dependencies complete, rather than processing fixed dependency waves.
        This reduces idle time when tasks have heterogeneous execution times.

        Phase 3 optimization: Expected to save 10-150 seconds per pipeline.
        """
        # Log initial dependency structure for debugging
        waves = _compute_dependency_waves(tasks)
        logger.info(f"Dependency analysis: {len(tasks)} tasks in {len(waves)} wave(s)")
        for wave_num, wave in enumerate(waves, 1):
            wave_ids = [t.task_id for t in wave]
            logger.info(f"  Wave {wave_num}: {wave_ids} ({len(wave)} parallel)")

        # Create async callback for executing a single task
        async def execute_task_fn(task: WorkflowTaskState) -> Any:
            """Execute a single task with full agent invocation, retry, and state management."""
            # Skip already-completed tasks (partial step resume)
            if task.status == TaskStatus.COMPLETED:
                logger.info(f"Skipping {task.task_id} (already completed)")
                return {"skipped": True}

            # Check for user interruption
            if await self._check_interrupt("task_execution"):
                raise KeyboardInterrupt("User interrupted task execution")

            # Check budget before proceeding
            if self.run_logger and self.config.max_budget_usd:
                budget_status = self.run_logger.check_budget(self.config.max_budget_usd)
                if budget_status == "exceeded":
                    raise RuntimeError("Budget exceeded — aborting task")

            # Execute task using the standard single-task execution flow (with retries)
            await self._execute_single_task(step, task, workspace)
            self._checkpoint_state()
            self.progress.on_task_complete()

            # Return task status for logging
            return {
                "task_id": task.task_id,
                "status": task.status.value,
                "success": task.status == TaskStatus.COMPLETED,
            }

        # Create async callback for knowledge rebuilding
        async def knowledge_rebuild_fn() -> None:
            """Rebuild knowledge index to make symbols discoverable by later tasks."""
            if not (
                self.config.knowledge.enabled
                and self.config.knowledge_context
                and self.config.knowledge_context.mcp_configured
            ):
                return

            logger.info("Rebuilding knowledge index...")
            try:
                result = await build_knowledge(
                    project_root=self.project_root,
                    aicoder_path=self.config.knowledge.aicoder_path,
                    timeout_seconds=self.config.knowledge.build_timeout_seconds,
                    skip_if_fresh_minutes=0,
                    richness=self.config.knowledge.richness,
                    skip_vectors=True,   # Skip slow vector phase
                    skip_features=True,  # Skip slow feature phase
                )
                if result.success and self.config.knowledge.inject_brief:
                    brief = synthesize_brief(
                        result.knowledge_root,
                        max_files=self.config.knowledge.brief_max_files,
                        max_symbols=self.config.knowledge.brief_max_symbols,
                    )
                    self.config.knowledge_context.brief = brief
                    self.config.knowledge_context.file_count = result.file_count
                    logger.info(
                        f"Knowledge rebuilt: {result.file_count} files in {result.build_time_ms:.0f}ms"
                    )
                elif not result.success:
                    logger.warning(f"Knowledge rebuild failed: {result.error}")
            except Exception as e:
                logger.warning(f"Knowledge rebuild error (non-fatal): {e}")

        # Create scheduler and execute all tasks dynamically
        logger.info("Starting dynamic task scheduling (TaskScheduler)...")
        scheduler = TaskScheduler(
            tasks=tasks,
            partition_fn=self._partition_by_file_conflicts,
            execute_task_fn=execute_task_fn,
            knowledge_rebuild_fn=knowledge_rebuild_fn,
        )

        try:
            results = await scheduler.schedule_all()
            logger.info(f"Task scheduling completed: {len(results)} tasks executed")
        except RuntimeError as e:
            # Deadlock or critical error in dependency graph
            logger.error(f"Task scheduling failed: {e}")
            return False
        except KeyboardInterrupt:
            # User interrupted during task execution
            logger.info("Task execution interrupted by user")
            return False

        # Check for any failed tasks and mark downstream tasks as blocked
        failed_tasks = [t for t in tasks if t.status == TaskStatus.FAILED]
        if failed_tasks:
            failed_ids = [t.task_id for t in failed_tasks]
            logger.error(f"Task execution failed: {len(failed_ids)} failed tasks: {failed_ids}")

            # Mark downstream tasks as blocked
            failed_ids_set = {t.task_id for t in failed_tasks}
            for task in tasks:
                if task.status != TaskStatus.FAILED and set(task.dependencies) & failed_ids_set:
                    task.status = TaskStatus.BLOCKED
                    task.error = "Blocked by failed dependency"

            return False

        # Verify all tasks completed
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
        # Store output for artifact rescue (cleared after step completes)
        if result.output:
            self._task_outputs[task.task_id] = result.output
        if result.written_files:
            self._task_written_files.extend(result.written_files)

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
            mcp_servers=self._mcp_servers,
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
        if self._knowledge_watcher is not None and self._knowledge_watcher.alive:
            return  # Already running

        if self._knowledge_watcher is not None and self._knowledge_watcher.failed:
            logger.warning(
                f"Knowledge watcher had died ({self._knowledge_watcher.failure_error}), restarting"
            )
            self._knowledge_watcher = None

        self._knowledge_watcher = KnowledgeWatcher(
            project_root=self.project_root,
            aicoder_path=self.config.knowledge.aicoder_path,
            build_timeout_seconds=self.config.knowledge.build_timeout_seconds,
            debounce_seconds=self.config.knowledge.watcher_debounce_seconds,
            brief_max_files=self.config.knowledge.brief_max_files,
            brief_max_symbols=self.config.knowledge.brief_max_symbols,
            inject_brief=self.config.knowledge.inject_brief,
            knowledge_context=self.config.knowledge_context,
            richness=self.config.knowledge.richness,
            skip_vectors=self.config.knowledge.skip_vectors,
            skip_features=self.config.knowledge.skip_features,
        )
        await self._knowledge_watcher.start()

    async def _stop_knowledge_watcher(self) -> None:
        """Stop the file-change knowledge watcher if running."""
        if self._knowledge_watcher is None:
            return

        if self._knowledge_watcher.failed:
            logger.warning(
                f"Knowledge watcher had died during execution: "
                f"{self._knowledge_watcher.failure_error}"
            )

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
            richness=self.config.knowledge.richness,
            skip_vectors=self.config.knowledge.skip_vectors,
            skip_features=self.config.knowledge.skip_features,
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
        # Pass artifact cache to avoid per-task disk reads (bottleneck 003 optimization)
        artifact_digest_section = _inject_artifact_digests(
            workspace, list(step.inputs), self.config,
            artifact_cache=self._artifact_cache,
        )
        exploration = _exploration_instruction(self.config, role=task.assigned_role.value)

        input_section = ""
        if step.inputs:
            input_files = "\n".join(f"- {artifacts_dir}/{name}.json" for name in step.inputs)
            input_section = f"\n## Input Artifacts\n\n{input_files}\n"

        output_section = ""
        if step.outputs:
            output_lines = []
            for name in step.outputs:
                output_lines.append(f"  **{artifacts_dir}/{name}.json**")
            output_list = "\n".join(output_lines)
            output_section = (
                f"\n## REQUIRED Output Artifacts\n\n"
                f"You MUST create these files using the **Write tool**:\n\n"
                f"{output_list}\n\n"
                f"**CRITICAL**: Use the Write tool to physically create each file on disk.\n"
                f"Do NOT just output JSON in your response text — downstream phases depend on "
                f"the file existing at the exact path above.\n"
                f"After writing each file, use the Read tool to verify it was created successfully.\n"
            )

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
4. For EACH output artifact listed above: call the **Write tool** with the exact file path and valid JSON content. Then call **Read** to verify the file exists.{access_note}
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
