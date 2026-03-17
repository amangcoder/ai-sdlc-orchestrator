"""Background run lifecycle manager for the web dashboard."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from orchestrator.config import load_config
from orchestrator.main import WORKFLOW_ALIASES, sanitize_feature_request
from orchestrator.models import WorkflowType

logger = logging.getLogger(__name__)


class RunRequest(BaseModel):
    """Validated input for starting an orchestration run from the web UI."""

    feature_request: str = Field(..., min_length=1, max_length=10_000)
    workflow_type: str = Field(default="feature_development")
    debate: bool = False
    knowledge: bool | None = None
    enhanced_perception: bool = False
    max_budget_usd: float = Field(default=50.0, ge=1.0, le=500.0)
    max_concurrent_agents: int = Field(default=0, ge=0, le=100)
    dry_run: bool = False
    resume_run_id: str | None = None


class RunTracker:
    """Manages background orchestration runs within the dashboard process."""

    def __init__(self, workspace: Path, config_path: Path | None = None) -> None:
        self._workspace = workspace
        self._config_path = config_path
        self._tasks: dict[str, asyncio.Task[Any]] = {}
        self._errors: dict[str, str] = {}
        self._start_times: dict[str, float] = {}

    async def start_run(self, request: RunRequest) -> str:
        """Start a new orchestration run as a background task.

        Returns the run_id. Raises ValueError if a run is already active.
        """
        if self._tasks:
            active = next(iter(self._tasks))
            raise ValueError(
                f"A run is already active ({active}). "
                "Cancel or wait for it to finish before starting another."
            )

        sanitize_feature_request(request.feature_request)

        config = load_config(self._config_path)

        # Apply web overrides
        config.confirm = False
        config.tech_stack_confirmation = False
        config.enhanced_perception = request.enhanced_perception
        config.max_budget_usd = request.max_budget_usd
        config.debate.enabled = request.debate
        if request.knowledge is not None:
            config.knowledge.enabled = request.knowledge
        if request.max_concurrent_agents > 0:
            config.max_concurrent_agents = request.max_concurrent_agents

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
            self._run_wrapper(run_id, engine, request, workflow_type, interrupt_manager)
        )
        self._tasks[run_id] = task
        self._start_times[run_id] = time.monotonic()

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
