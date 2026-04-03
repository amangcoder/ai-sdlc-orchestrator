"""Mobile API FastAPI application factory.

Creates and configures the FastAPI app with:
  - Auth middleware (fail-closed Bearer token)
  - All API routers (runs, websocket, artifacts, config, qr_setup)
  - Health endpoint (auth-exempt)
  - Lifespan startup reconciliation (orphaned 'running' → 'interrupted')

IMPORTANT: app.state.reader and app.state.tracker are PUBLIC attributes.
The integration test fixtures replace app.state.tracker with a MagicMock
AFTER create_mobile_app() returns. Do NOT freeze app.state.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

# Try to import the version string
try:
    from orchestrator._version import __version__
except ImportError:
    try:
        from importlib.metadata import version
        __version__ = version("ai-sdlc-orchestrator")
    except Exception:
        __version__ = "0.0.0"


def create_mobile_app(
    workspace_dir: Path,
    config_path: Path | None = None,
) -> FastAPI:
    """Create and configure the Mobile API FastAPI application.

    Args:
        workspace_dir: Path to the orchestrator workspace directory.
            Used to locate state files, JSONL logs, and artifacts.
        config_path: Optional path to the orchestrator YAML config.
            Defaults to config/default.yaml if not provided.

    Returns:
        Fully configured FastAPI application instance.
    """
    # ── Lifespan (startup reconciliation) ─────────────────────────────────
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Reconcile orphaned 'running' state files on startup.

        Any state-{id}.json file with status='running' whose run_id is NOT
        in the tracker's active_run_ids list is considered orphaned (the server
        was restarted while a run was active). These are marked 'interrupted'.
        """
        tracker = app.state.tracker
        try:
            active_ids = set(tracker.active_run_ids())
        except Exception:
            active_ids = set()

        # Clear the LRU directory cache on startup to prevent stale entries
        # from a previous server process (different salt or filesystem layout).
        try:
            from orchestrator.mobile_api.dynamic_directory_service import _clear_cache
            _clear_cache()
        except Exception as exc:
            logger.debug("Could not clear directory cache on startup: %s", exc)

        for state_file in workspace_dir.glob("state-*.json"):
            try:
                state = json.loads(state_file.read_text(encoding="utf-8"))
                run_id = state.get("run_id")
                if state.get("status") == "running" and run_id and run_id not in active_ids:
                    # Subprocess runs store their PID. If the process is still
                    # alive (session-independent via start_new_session=True),
                    # leave the state as-is — the run is still in progress.
                    pid = state.get("pid")
                    if pid is not None:
                        try:
                            os.kill(pid, 0)  # signal 0 = liveness check only
                            logger.info(
                                "Run %s PID %d still alive — leaving as running", run_id, pid
                            )
                            continue  # process is alive, skip reconciliation
                        except (OSError, ProcessLookupError):
                            pass  # process is dead — fall through to mark interrupted

                    logger.info(
                        "Reconciling orphaned run %s: marking as interrupted", run_id
                    )
                    state["status"] = "interrupted"
                    state_file.write_text(
                        json.dumps(state, ensure_ascii=False),
                        encoding="utf-8",
                    )
            except (json.JSONDecodeError, OSError, KeyError) as exc:
                logger.debug("Skipping state file %s: %s", state_file, exc)

        yield
        # Shutdown: no cleanup needed

    # ── Application initialization ─────────────────────────────────────────
    app = FastAPI(
        title="Orchestrator Mobile API",
        description="Mobile-optimized REST and WebSocket API for the AI SDLC Orchestrator",
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        # Disable trailing-slash redirects so empty artifact names return 404
        # instead of a 307 redirect to the list endpoint.
        redirect_slashes=False,
    )

    # ── Config loading ─────────────────────────────────────────────────────
    # Load OrchestratorConfig for directory list and flag forwarding.
    # On failure, fall back to defaults rather than crashing startup.
    from orchestrator.config import load_config
    from orchestrator.models import OrchestratorConfig

    try:
        config = load_config(config_path)
    except Exception as exc:
        logger.warning(
            "Failed to load config from %s (using defaults): %s", config_path, exc
        )
        config = OrchestratorConfig()

    app.state.config = config

    # ── Workspace management ───────────────────────────────────────────────
    from orchestrator.workspace_manager import WorkspaceManager
    workspace_root = Path(config.workspace_root or workspace_dir).resolve()
    project_name = config.project_name or Path.cwd().name
    workspace_manager = WorkspaceManager(workspace_root, project_name)
    app.state.workspace_manager = workspace_manager  # shared with all routes

    # ── Shared state ───────────────────────────────────────────────────────
    # These are PUBLIC — test fixtures may replace app.state.tracker after factory returns.
    from orchestrator.dashboard.data import RunDataReader
    from orchestrator.dashboard.runner import RunTracker
    from orchestrator.mobile_api.rate_limit import RateLimiter

    app.state.reader = RunDataReader(workspace_manager)
    app.state.tracker = RunTracker(workspace_dir, config_path)
    # Per-app rate limiter so each test app instance has an independent window
    app.state.rate_limiter = RateLimiter(window_seconds=5.0)

    # MonitoringStack — initialized for SLO reporting; None if monitoring not configured.
    # The SLO endpoint calls get_slo_report(app.state.monitoring_stack) which handles None.
    app.state.monitoring_stack = None
    try:
        from orchestrator.monitoring import MonitoringStack
        mon_config = config.monitoring
        if mon_config.slo.enabled or mon_config.metrics_enabled:
            app.state.monitoring_stack = MonitoringStack(
                config=mon_config,
                run_id="mobile-api",
                workspace=workspace_dir,
            )
    except Exception as exc:
        logger.debug("MonitoringStack not initialized for mobile API: %s", exc)

    # Store config_path on state for config router access
    app.state.config_path = config_path

    # ── Per-installation directory salt ────────────────────────────────────
    # Stable UUID used to generate opaque directory IDs. Persisted to disk so
    # IDs remain consistent across server restarts (mobile clients cache them).
    salt_file = workspace_dir / ".server_salt"
    if salt_file.exists():
        try:
            app.state.directory_salt = uuid.UUID(salt_file.read_text(encoding="utf-8").strip())
        except (ValueError, OSError) as exc:
            logger.warning("Could not read salt file %s (%s) — regenerating", salt_file, exc)
            app.state.directory_salt = uuid.uuid4()
            try:
                salt_file.write_text(str(app.state.directory_salt), encoding="utf-8")
            except OSError:
                pass
    else:
        app.state.directory_salt = uuid.uuid4()
        try:
            salt_file.write_text(str(app.state.directory_salt), encoding="utf-8")
        except OSError:
            pass  # Non-fatal: IDs will regenerate on next restart

    # ── Directory map (built once at startup) ──────────────────────────────
    from orchestrator.mobile_api.directory_service import build_directory_entries

    try:
        directory_entries, frozen_dir_map = build_directory_entries(
            config, workspace_dir, app.state.directory_salt
        )
    except Exception as exc:
        logger.warning("Failed to build directory entries (%s) — using empty map", exc)
        directory_entries = []
        frozen_dir_map = {}

    app.state.directory_entries = directory_entries
    app.state.frozen_dir_map = frozen_dir_map

    # ── Dynamic directory browsing state ───────────────────────────────────
    # projects_root: resolved Path for dynamic directory tree (None if not configured).
    # max_browse_depth: maximum subdirectory depth clients may navigate.
    from pathlib import Path as _Path

    if config.projects_root is not None:
        try:
            app.state.projects_root = _Path(config.projects_root).resolve()
        except Exception as exc:
            logger.warning(
                "Failed to resolve projects_root %r (%s) — dynamic browsing disabled",
                config.projects_root,
                exc,
            )
            app.state.projects_root = None
    else:
        app.state.projects_root = None

    app.state.max_browse_depth = config.max_browse_depth

    # ── Request logging middleware (structured, non-sensitive) ─────────────
    # Registered first so it wraps ALL subsequent middleware and routes.
    # Emits request_id, method, path, status_code, and duration_ms for every
    # HTTP request. Authorization headers are NEVER logged.
    try:
        from orchestrator.mobile_api.request_logger import RequestLoggerMiddleware
        app.add_middleware(RequestLoggerMiddleware)
    except Exception as exc:
        logger.warning("Could not add request logger middleware: %s", exc)

    # ── Auth middleware (fail-closed Bearer token) ──────────────────────────
    from orchestrator.mobile_api.auth import auth_middleware

    app.middleware("http")(auth_middleware)

    # ── API routers ────────────────────────────────────────────────────────
    from orchestrator.mobile_api.routes.runs import router as runs_router
    from orchestrator.mobile_api.routes.websocket import router as ws_router
    from orchestrator.mobile_api.routes.artifacts import router as artifacts_router
    from orchestrator.mobile_api.routes.config import router as config_router
    from orchestrator.mobile_api.routes.directories import router as directories_router
    from orchestrator.mobile_api.routes.projects import router as projects_router
    from orchestrator.mobile_api.routes.ssh import router as ssh_router
    from orchestrator.mobile_api.qr_setup import router as qr_router
    # New monitoring / analytics routers (TASK-001, TASK-002)
    from orchestrator.mobile_api.routes.cost_analytics import router as cost_analytics_router
    from orchestrator.mobile_api.routes.slo import router as slo_router
    from orchestrator.mobile_api.routes.artifact_search import router as artifact_search_router
    from orchestrator.mobile_api.routes.metrics import router as metrics_router
    from orchestrator.mobile_api.routes.observability import router as observability_router
    from orchestrator.mobile_api.routes.alerts import router as alerts_router

    app.include_router(runs_router, prefix="/api/v1")
    app.include_router(ws_router, prefix="/api/v1")
    app.include_router(artifacts_router, prefix="/api/v1")
    app.include_router(config_router, prefix="/api/v1")
    app.include_router(directories_router, prefix="/api/v1")
    app.include_router(projects_router, prefix="/api/v1")
    app.include_router(ssh_router, prefix="/api/v1")
    app.include_router(qr_router)  # No prefix — /api/v1/setup/qr is in the router
    # Monitoring / analytics routers
    app.include_router(cost_analytics_router, prefix="/api/v1")
    app.include_router(slo_router, prefix="/api/v1")
    app.include_router(artifact_search_router, prefix="/api/v1")
    app.include_router(metrics_router, prefix="/api/v1")
    app.include_router(observability_router, prefix="/api/v1")
    app.include_router(alerts_router, prefix="/api/v1")

    # ── Health endpoint (auth-exempt) ──────────────────────────────────────
    from orchestrator.mobile_api.models import HealthResponse

    @app.get("/health", response_model=HealthResponse, tags=["health"])
    async def health():
        """Health check endpoint — no auth required.

        Returns server status, version, and number of active runs.
        Flutter SettingsScreen calls this during 'Test Connection'.
        """
        try:
            active_runs = len(app.state.tracker.active_run_ids())
        except Exception:
            active_runs = 0

        return HealthResponse(
            status="ok",
            version=__version__,
            active_runs=active_runs,
        )

    return app
