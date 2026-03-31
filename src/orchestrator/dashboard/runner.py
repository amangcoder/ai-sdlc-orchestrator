"""Background run lifecycle manager for the web dashboard."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from orchestrator.config import load_config
from orchestrator.main import WORKFLOW_ALIASES, sanitize_feature_request
from orchestrator.models import WorkflowType

logger = logging.getLogger(__name__)


class RunRequest(BaseModel):
    """Validated input for starting an orchestration run from the web UI or Mobile API.

    All new fields default to None. None means "use the server config default"
    for that field — the override logic in RunTracker.start_run() applies the
    appropriate backend default when None is received.

    IMPORTANT: Use `if value is not None:` checks — NEVER `if value:` — to
    avoid silently dropping falsy values like False, 0, or empty strings.
    """

    # ── Existing fields (unchanged — dashboard call sites depend on these) ─
    feature_request: str = Field(..., min_length=1, max_length=10_000)
    workflow_type: str = Field(default="feature_development")
    debate: bool = False
    knowledge: bool | None = None  # tristate: None=inherit, True=on, False=off
    max_budget_usd: float = Field(default=50.0, ge=1.0, le=500.0)
    max_concurrent_agents: int = Field(default=0, ge=0, le=100)
    dry_run: bool = False
    resume_run_id: str | None = None

    # ── New fields (all None = use config default) ─────────────────────────
    workspace_dir_override: str | None = None
    self_orchestrate: bool | None = None
    confirm: bool | None = None
    tech_stack_confirmation: bool | None = None
    checklist_verify: bool | None = None
    # Override max_concurrent_agents via nullable field (separate from int default=0)
    mode: Literal["fast", "superhaiku", "supersonnet", "balanced", "overkill"] | None = None
    speed: Literal["turbo", "standard", "thorough", "paranoid", "auto"] | None = None
    phase: Literal["pm", "architect", "engineer", "qa", "reviewer"] | None = None
    from_phase: Literal["pm", "architect", "engineer", "qa", "reviewer"] | None = None
    log_format: Literal["console", "json"] | None = None
    researchers: int | None = None
    brainstormers: int | None = None
    debate_rounds: int | None = None

    # ── Dashboard-specific fields (TASK-004) ───────────────────────────────
    # model_routing: simplified routing tier for the new-run dashboard form.
    # "default" means no override; "speed"/"quality"/"economy" map to routing modes.
    model_routing: str = Field(default="default")
    # config_path: optional path to a config YAML file, overrides server default.
    config_path: str | None = None
    # custom_workflow: JSON string defining a custom workflow; applied when
    # workflow_type == "custom".
    custom_workflow: str | None = None


class RunTracker:
    """Manages background orchestration runs within the dashboard process."""

    def __init__(self, workspace: Path, config_path: Path | None = None) -> None:
        self._workspace = workspace
        self._config_path = config_path
        self._tasks: dict[str, asyncio.Task[Any]] = {}
        self._errors: dict[str, str] = {}
        self._start_times: dict[str, float] = {}
        # Maps resolved workspace path → run_id for per-project conflict detection.
        # Allows parallel runs across different projects while blocking duplicate
        # runs within the same project.
        self._active_by_workspace: dict[str, str] = {}

    async def start_run(self, request: RunRequest) -> str:
        """Start a new orchestration run as a background task.

        Returns the run_id. Raises ValueError if a run is already active for
        the same workspace (project). Parallel runs across different projects
        are allowed.
        """

        sanitize_feature_request(request.feature_request)

        config = load_config(self._config_path)

        # ── Apply workspace directory override ────────────────────────────
        if request.workspace_dir_override is not None:
            config.workspace_dir = request.workspace_dir_override

        # Per-workspace conflict detection: block duplicate runs on the same
        # project, but allow parallel runs across different projects.
        active_workspace = str(Path(config.workspace_dir).resolve())
        if active_workspace in self._active_by_workspace:
            active = self._active_by_workspace[active_workspace]
            raise ValueError(
                f"A run is already active ({active}) for this project. "
                "Cancel or wait for it to finish before starting another."
            )

        # ── Apply flag overrides ──────────────────────────────────────────
        # CRITICAL: Use `if value is not None:` — NEVER `if value:`.
        # Falsy values (False, 0) are valid user intentions and must not be dropped.

        # self_orchestrate
        if request.self_orchestrate is not None:
            config.self_orchestrate = request.self_orchestrate

        # confirm: backward-compat default is False (dashboard never sends confirm)
        if request.confirm is not None:
            config.confirm = request.confirm
        else:
            config.confirm = False  # backward compat — legacy dashboard omits this

        # tech_stack_confirmation: backward-compat default is False for web
        if request.tech_stack_confirmation is not None:
            config.tech_stack_confirmation = request.tech_stack_confirmation
        else:
            config.tech_stack_confirmation = False  # backward compat

        # checklist_verify
        if request.checklist_verify is not None:
            config.checklist_verify = request.checklist_verify

        # max_budget_usd (always present)
        config.max_budget_usd = request.max_budget_usd

        # debate
        config.debate.enabled = request.debate

        # knowledge (tristate: None=inherit, True=on, False=off)
        if request.knowledge is not None:
            config.knowledge.enabled = request.knowledge

        # max_concurrent_agents (legacy field: 0 = "use config default")
        if request.max_concurrent_agents > 0:
            config.max_concurrent_agents = request.max_concurrent_agents

        # routing mode (from explicit mode field, or from dashboard model_routing)
        if request.mode is not None:
            config.routing_mode = request.mode
        elif request.model_routing and request.model_routing != "default":
            _routing_map: dict[str, str] = {
                "speed": "fast",
                "quality": "overkill",
                "economy": "superhaiku",
            }
            _mapped_mode = _routing_map.get(request.model_routing)
            if _mapped_mode:
                config.routing_mode = _mapped_mode

        # speed mode
        if request.speed is not None:
            from orchestrator.model_routing import apply_speed_mode, auto_classify_speed
            from orchestrator.models import SpeedMode

            speed_mode = SpeedMode(request.speed)
            if speed_mode == SpeedMode.AUTO:
                speed_mode = asyncio.run(
                    auto_classify_speed(request.feature_request, Path.cwd())
                )
            apply_speed_mode(config, speed_mode)

        # debate sub-settings
        if request.researchers is not None:
            config.debate.researcher_count = request.researchers
        if request.brainstormers is not None:
            config.debate.brainstormer_count = request.brainstormers
        if request.debate_rounds is not None:
            config.debate.max_rounds = request.debate_rounds

        # Resolve workflow type
        wf_key = request.workflow_type.lower().replace("-", "_")
        workflow_type = WORKFLOW_ALIASES.get(wf_key)
        if workflow_type is None:
            try:
                workflow_type = WorkflowType(wf_key)
            except ValueError:
                raise ValueError(f"Unknown workflow type: {request.workflow_type}")

        # Pre-generate run_id for immediate redirect
        run_id = request.resume_run_id or uuid.uuid4().hex[:12]

        # Set up interrupt manager (sentinel only, no signal handler in web)
        from orchestrator.interruption import InterruptManager

        interrupt_manager = InterruptManager()
        workspace = Path(config.workspace_dir).resolve()
        workspace.mkdir(parents=True, exist_ok=True)
        interrupt_manager.setup_sentinel(workspace)

        from orchestrator.engine import OrchestratorEngine

        engine = OrchestratorEngine(
            config=config,
            dry_run=request.dry_run,
            interrupt_manager=interrupt_manager,
            confirm_callback=None,
        )

        task = asyncio.create_task(
            self._run_wrapper(run_id, engine, request, workflow_type, interrupt_manager)  # noqa: E501
        )
        self._tasks[run_id] = task
        self._start_times[run_id] = time.monotonic()
        self._active_by_workspace[active_workspace] = run_id

        return run_id

    async def _run_wrapper(
        self,
        run_id: str,
        engine: Any,
        request: RunRequest,
        workflow_type: WorkflowType,
        interrupt_manager: Any,
    ) -> None:
        """Wraps engine.run() with error handling and cleanup."""
        try:
            await engine.run(
                request.feature_request,
                single_phase=request.phase,
                from_phase=request.from_phase,
                resume=bool(request.resume_run_id),
                resume_run_id=request.resume_run_id,
                workflow_type=workflow_type,
                run_id=run_id if not request.resume_run_id else None,
            )
        except Exception as exc:
            logger.exception("Run %s crashed: %s", run_id, exc)
            self._errors[run_id] = str(exc)
        finally:
            self._tasks.pop(run_id, None)
            self._start_times.pop(run_id, None)
            # Release the per-workspace lock so a new run can start for this project
            self._active_by_workspace = {
                ws: rid for ws, rid in self._active_by_workspace.items() if rid != run_id
            }

    async def cancel_run(self, run_id: str) -> bool:
        """Request graceful cancellation of an active run via sentinel file."""
        sentinel = self._workspace / ".interrupt"
        sentinel.write_text("web_cancel")

        if run_id in self._tasks and not self._tasks[run_id].done():
            return True
        return False

    def is_active(self, run_id: str) -> bool:
        return run_id in self._tasks

    def active_run_ids(self) -> list[str]:
        return list(self._tasks.keys())

    def get_error(self, run_id: str) -> str | None:
        return self._errors.get(run_id)
