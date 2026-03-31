"""FastAPI dashboard application."""

from __future__ import annotations

import asyncio
import hmac
import json
import os
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from orchestrator.dashboard.data import RunDataReader
from orchestrator.dashboard.routes.artifacts import create_artifacts_router
from orchestrator.dashboard.routes.cost import create_cost_router
from orchestrator.dashboard.routes.dashboard_overview import create_dashboard_overview_router
from orchestrator.dashboard.routes.log_analysis import create_log_analysis_router
from orchestrator.dashboard.routes.monitoring_health import create_monitoring_health_router
from orchestrator.dashboard.routes.observability import create_observability_router
from orchestrator.dashboard.routes.prompt import create_prompt_router
from orchestrator.dashboard.routes.runs import create_runs_router
from orchestrator.dashboard.routes.search import create_search_router
from orchestrator.dashboard.routes.settings import create_settings_router
from orchestrator.dashboard.routes.slo import create_slo_router
from orchestrator.dashboard.runner import RunRequest, RunTracker
from orchestrator.workspace_manager import WorkspaceManager

STATIC_DIR = Path(__file__).parent / "static"
TEMPLATES_DIR = Path(__file__).parent / "templates"


def _build_workflow_phases(run_id: str, run: Any, manager: Any) -> list[dict[str, str]]:
    """Return an ordered list of phase dicts for the live-run phase stepper.

    Each entry has the shape::

        {"key": "ux_specification", "label": "UX Specification", "status": "completed"}

    Statuses: ``completed`` | ``running`` | ``failed`` | ``pending``.

    The ordering is derived from the state-file's ``completed_steps`` +
    ``current_step`` fields (written in execution order by the workflow engine),
    falling back to the ``phases`` dict key insertion order when those fields
    are absent.
    """
    phases_dict: dict[str, Any] = run.phases or {}

    # -- Attempt to build ordered list from the state file --
    state_path = manager.find_run_state(run_id)
    if state_path and state_path.exists():
        try:
            state = json.loads(state_path.read_text())
            completed_steps: list[str] = state.get("completed_steps") or []
            current_step: str | None = state.get("current_step")

            # Ordered labels: completed → current
            ordered_labels: list[str] = list(completed_steps)
            if current_step and current_step not in ordered_labels:
                ordered_labels.append(current_step)

            # Add phases from phases_dict not yet captured (with label conversion)
            for key in phases_dict:
                label = key.replace("_", " ").title()
                if label not in ordered_labels:
                    ordered_labels.append(label)

            # Attempt to append remaining steps from the workflow definition
            try:
                from orchestrator.workflows import select_workflow
                from orchestrator.models import WorkflowType as _WorkflowType
                wt = _WorkflowType(run.workflow_type)
                wf = select_workflow(wt)
                for step in wf.steps:
                    step_label = step.name
                    if step_label not in ordered_labels:
                        ordered_labels.append(step_label)
            except Exception:
                pass

            result: list[dict[str, str]] = []
            for label in ordered_labels:
                key = label.lower().replace(" ", "_")
                phase_data = phases_dict.get(key, {})
                raw_status = (phase_data.get("status") or "").lower()
                if raw_status == "completed":
                    status = "completed"
                elif raw_status == "failed":
                    status = "failed"
                elif raw_status == "running" or label == (current_step or ""):
                    status = "running"
                else:
                    status = "pending"
                result.append({"key": key, "label": label, "status": status})
            return result
        except Exception:
            pass

    # -- Fallback: phases dict insertion order --
    return [
        {
            "key": k,
            "label": k.replace("_", " ").title(),
            "status": (v.get("status") or "pending").lower(),
        }
        for k, v in phases_dict.items()
    ]

# --- SSE limits ---
MAX_SSE_DURATION = 30 * 60  # 30 minutes
MAX_SSE_CONNECTIONS = 10

# --- Auth ---
DASHBOARD_TOKEN = os.environ.get("ORCHESTRATOR_DASHBOARD_TOKEN")

# Track active SSE connections
_active_sse_connections = 0


def create_app(workspace_root: Path, project_name: str, config_path: Path | None = None) -> FastAPI:
    from orchestrator import __version__
    app = FastAPI(title="Orchestrator Dashboard", version=__version__)
    
    manager = WorkspaceManager(workspace_root, project_name)
    reader = RunDataReader(manager)
    runner = RunTracker(manager.project_workspace, config_path)
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # --- Dashboard overview router (must be registered before generic page routes
    #     so /dashboard and /api/v1/dashboard/* are resolved first) ---
    dashboard_overview_router = create_dashboard_overview_router(templates, reader)
    app.include_router(dashboard_overview_router)

    # --- Runs list router ---
    runs_router = create_runs_router(templates, reader)
    app.include_router(runs_router)

    # --- Observability router ---
    observability_router = create_observability_router(templates, config_path, reader)
    app.include_router(observability_router)

    # --- Monitoring health API router ---
    monitoring_health_router = create_monitoring_health_router(reader)
    app.include_router(monitoring_health_router)

    # --- Cost analytics router ---
    cost_router = create_cost_router(templates, reader)
    app.include_router(cost_router)

    # --- SLO compliance router ---
    # monitoring_stack is per-run; pass None here — the router gracefully falls
    # back to neutral default values when no tracker is attached.
    slo_router = create_slo_router(templates, monitoring_stack=None)
    app.include_router(slo_router)

    # --- Global artifact search router (registered before the per-run artifact
    #     router so GET /api/v1/artifacts/search uses RunDataReader.search_artifacts_global)
    search_router = create_search_router(templates, reader)
    app.include_router(search_router)

    # --- Artifacts router ---
    try:
        from orchestrator.artifact_manager import ArtifactManager
        _artifacts_dir = manager.project_workspace / "artifacts"
        _artifacts_dir.mkdir(parents=True, exist_ok=True)
        _artifact_manager = ArtifactManager(_artifacts_dir)
        artifacts_router = create_artifacts_router(templates, _artifact_manager, reader)
        app.include_router(artifacts_router)
    except Exception as _exc:
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "ArtifactManager init failed — artifact routes disabled: %s", _exc
        )

    # --- Prompt interaction router ---
    prompt_router = create_prompt_router(templates, manager.project_workspace)
    app.include_router(prompt_router)

    # --- Settings router ---
    settings_router = create_settings_router(templates, config_path)
    app.include_router(settings_router)

    # --- Log analysis router ---
    log_analysis_router = create_log_analysis_router(templates, reader)
    app.include_router(log_analysis_router)

    # --- Auth middleware ---
    # Capture the token at app-creation time so tests that patch the module
    # global before calling create_app() get deterministic behaviour.
    _auth_token = DASHBOARD_TOKEN

    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        if _auth_token and not request.url.path.startswith(('/healthz', '/health', '/static')):
            token = request.headers.get("Authorization", "").removeprefix("Bearer ")
            # Fallback: accept ?token=X query parameter for EventSource compatibility
            # (the browser EventSource API cannot send custom headers).
            if not token:
                token = request.query_params.get("token", "")
            # Use hmac.compare_digest to prevent timing side-channel attacks.
            if not hmac.compare_digest(token.encode(), _auth_token.encode()):
                return JSONResponse(status_code=401, content={"error": "Unauthorized"})
        return await call_next(request)

    # --- HTML pages ---

    @app.get("/", response_class=RedirectResponse)
    async def index():
        return RedirectResponse(url="/dashboard")

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    async def run_detail_page(request: Request, run_id: str):
        run = reader.get_run(run_id)
        if not run:
            return HTMLResponse("<h1>Run not found</h1>", status_code=404)
        return templates.TemplateResponse("run_detail.html", {
            "request": request,
            "run": run,
            "phases_json": json.dumps(run.phases, default=str),
            "page_title": f"Run {run_id}",
        })

    @app.get("/runs/{run_id}/live", response_class=HTMLResponse)
    async def run_live_page(request: Request, run_id: str):
        run = reader.get_run(run_id)
        if not run:
            return HTMLResponse("<h1>Run not found</h1>", status_code=404)
        workflow_phases = _build_workflow_phases(run_id, run, manager)
        return templates.TemplateResponse("live.html", {
            "request": request,
            "run": run,
            "workflow_phases": workflow_phases,
            "page_title": f"Live: {run_id}",
            # Passed to JS so EventSource can include token in its URL
            # (?token=X) since the browser EventSource API cannot send headers.
            "dashboard_token": _auth_token or "",
        })

    @app.get("/metrics", response_class=HTMLResponse)
    async def metrics_page(request: Request):
        summary = reader.get_metrics_summary()

        # --- Prometheus connection status ---
        prometheus_status = "unknown"
        try:
            import urllib.request as _urllib_request
            import urllib.error as _urllib_error
            with _urllib_request.urlopen("http://localhost:9090/-/ready", timeout=1.5) as _resp:
                prometheus_status = "up" if _resp.status < 500 else "down"
        except _urllib_error.HTTPError as _exc:
            prometheus_status = "up" if _exc.code < 500 else "down"
        except Exception:
            prometheus_status = "down"

        # --- KPI data ---
        all_runs = reader.list_runs()
        active_runs = sum(1 for r in all_runs if r.status == "running")
        total_cost = sum(r.total_cost_usd for r in all_runs)

        # Avg phase duration (seconds) — computed from completed phases across runs
        phase_durations: list[float] = []
        for r in all_runs[:50]:  # cap scan at 50 runs for performance
            try:
                state_path = reader.manager.find_run_state(r.run_id)
                if state_path and state_path.exists():
                    import json as _json
                    state = _json.loads(state_path.read_text())
                    for phase_data in state.get("phases", {}).values():
                        start = phase_data.get("start_time")
                        end = phase_data.get("end_time")
                        if start and end:
                            from datetime import datetime as _dt
                            try:
                                s = _dt.fromisoformat(start)
                                e = _dt.fromisoformat(end)
                                phase_durations.append((e - s).total_seconds())
                            except (ValueError, TypeError):
                                pass
            except Exception:
                pass

        avg_phase_duration = (
            round(sum(phase_durations) / len(phase_durations), 1)
            if phase_durations else 0.0
        )

        # Recent run metrics table (last 10 runs)
        recent_runs = []
        for r in all_runs[:10]:
            recent_runs.append({
                "run_id": r.run_id,
                "status": r.status,
                "workflow_type": r.workflow_type,
                "cost_usd": r.total_cost_usd,
                "steps_completed": r.steps_completed,
                "steps_total": r.steps_total,
                "start_time": r.start_time,
            })

        # Grafana and Prometheus deep-link URLs (from config if available)
        grafana_url: str | None = None
        prometheus_ui_url: str | None = None
        if config_path and config_path.exists():
            try:
                import yaml as _yaml
                _cfg_raw = _yaml.safe_load(config_path.read_text()) or {}
                _mon = _cfg_raw.get("monitoring", {})
                grafana_url = _mon.get("grafana_url")
                prometheus_ui_url = _mon.get("prometheus_url", "http://localhost:9090")
            except Exception:
                pass
        if prometheus_ui_url is None:
            prometheus_ui_url = "http://localhost:9090"

        return templates.TemplateResponse("metrics.html", {
            "request": request,
            "summary": summary,
            "summary_json": json.dumps(summary, default=str),
            "prometheus_status": prometheus_status,
            "active_runs": active_runs,
            "total_cost_usd": total_cost,
            "avg_phase_duration": avg_phase_duration,
            "recent_runs": recent_runs,
            "grafana_url": grafana_url or "",
            "prometheus_ui_url": prometheus_ui_url,
            "page_title": "Metrics",
        })

    @app.get("/alerts", response_class=HTMLResponse)
    async def alerts_page(
        request: Request,
        severity: str = "",
        status: str = "",
    ):
        alerts = reader.get_alert_history()

        # Normalise query param values
        sev = severity.strip().lower()
        st = status.strip().lower()

        # Filter by severity (skip when empty or "all")
        if sev and sev != "all":
            alerts = [
                a for a in alerts
                if (a.get("severity") or "info").lower() == sev
            ]

        # Filter by status (skip when empty or "all")
        if st and st != "all":
            alerts = [
                a for a in alerts
                if (a.get("status") or "active").lower() == st
            ]

        # Pre-serialise each alert payload for expandable detail rows.
        # The _json field is computed BEFORE the dict is mutated so it doesn't
        # include the injected key itself.
        alerts_enriched = []
        for a in alerts:
            payload_json = json.dumps(a, indent=2, default=str)
            alerts_enriched.append({**a, "_json": payload_json})

        return templates.TemplateResponse("alerts.html", {
            "request": request,
            "alerts": alerts_enriched,
            "page_title": "Alerts",
            "filter_severity": sev,
            "filter_status": st,
        })

    @app.get("/new-run", response_class=HTMLResponse)
    async def new_run_page(request: Request):
        from orchestrator.models import WorkflowType
        workflow_types = [wt.value for wt in WorkflowType if wt != WorkflowType.CUSTOM]
        active_runs = runner.active_run_ids()
        all_runs = reader.list_runs()
        active_set = set(active_runs)
        resumable = [r for r in all_runs if r.status != "completed" and r.run_id not in active_set]
        return templates.TemplateResponse("new_run.html", {
            "request": request,
            "workflow_types": workflow_types,
            "active_runs": active_runs,
            "resumable_runs": resumable,
            "page_title": "New Run",
        })

    # --- JSON API (served at both /api/ and /api/v1/ for mobile compatibility) ---

    def _runs_list():
        runs = reader.list_runs()
        return [
            {
                "run_id": r.run_id,
                "feature_request": r.feature_request,
                "workflow_type": r.workflow_type,
                "status": r.status,
                "total_cost_usd": r.total_cost_usd,
                "start_time": r.start_time,
                "end_time": r.end_time,
                "steps_completed": r.steps_completed,
                "steps_total": r.steps_total,
            }
            for r in runs
        ]

    @app.get("/api/runs")
    @app.get("/api/v1/runs")
    async def api_runs():
        return _runs_list()

    @app.get("/api/runs/active")
    @app.get("/api/v1/runs/active")
    async def api_active_runs():
        return {"active": runner.active_run_ids()}

    def _run_detail(run_id: str):
        run = reader.get_run(run_id)
        if not run:
            return None
        return {
            "run_id": run.run_id,
            "feature_request": run.feature_request,
            "workflow_type": run.workflow_type,
            "status": run.status,
            "total_cost_usd": run.total_cost_usd,
            "phases": run.phases,
            "interrupt_history": run.interrupt_history,
        }

    @app.get("/api/runs/{run_id}")
    @app.get("/api/v1/runs/{run_id}")
    async def api_run_detail(run_id: str):
        result = _run_detail(run_id)
        if not result:
            return JSONResponse({"error": "not found"}, status_code=404)
        return result

    @app.get("/api/runs/{run_id}/events")
    @app.get("/api/v1/runs/{run_id}/events")
    async def api_run_events(run_id: str, offset: int = 0, limit: int = 100):
        events = reader.get_events(run_id, offset, limit)
        return {"events": events, "offset": offset, "count": len(events)}

    @app.get("/api/runs/{run_id}/timeline")
    @app.get("/api/v1/runs/{run_id}/timeline")
    async def api_run_timeline(run_id: str):
        run = reader.get_run(run_id)
        if not run or not run.timeline:
            return JSONResponse({"error": "no timeline data"}, status_code=404)
        return run.timeline

    @app.get("/api/metrics")
    @app.get("/api/v1/metrics")
    async def api_metrics():
        return reader.get_metrics_summary()

    @app.get("/api/alerts")
    @app.get("/api/v1/alerts")
    async def api_alerts(limit: int = 100):
        return reader.get_alert_history(limit)

    @app.get("/api/v1/runs/{run_id}/artifacts/{name}")
    async def api_run_artifact(run_id: str, name: str):
        # Layer 1 — regex allowlist: reject unexpected characters before any
        # filesystem access.
        #   _validate_run_id: from routes/artifacts (reused as per task spec)
        #   _validate_filename: looser than _validate_name — allows dot-extensions
        #     (e.g. prd.json) since this endpoint serves raw on-disk files, not
        #     the extensionless ArtifactManager names.
        from orchestrator.dashboard.routes.artifacts import _validate_run_id
        from orchestrator.dashboard.routes.validators import _validate_filename
        from fastapi import HTTPException
        try:
            _validate_run_id(run_id)
            _validate_filename(name)
        except HTTPException as exc:
            return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

        # Layer 2 — path confinement: resolve the full path and confirm it
        # stays inside the run's artifacts directory.
        artifacts_dir = manager.artifacts_dir(run_id).resolve()
        artifact_path = (artifacts_dir / name).resolve()
        if not artifact_path.is_relative_to(artifacts_dir):
            return JSONResponse({"error": "forbidden"}, status_code=403)

        if not artifact_path.exists():
            return JSONResponse({"error": "not found"}, status_code=404)
        try:
            return json.loads(artifact_path.read_text())
        except (json.JSONDecodeError, OSError):
            return JSONResponse({"error": "invalid artifact"}, status_code=500)

    @app.get("/api/v1/config")
    async def api_get_config():
        if config_path and config_path.exists():
            import yaml
            return yaml.safe_load(config_path.read_text()) or {}
        return {}

    @app.put("/api/v1/config")
    async def api_update_config(request: Request):
        import shutil
        import tempfile
        import yaml
        from orchestrator.config import load_config
        from orchestrator.monitoring.errors import ConfigurationError
        from orchestrator.persistence import atomic_write

        if not config_path:
            return JSONResponse(
                status_code=400,
                content={"error": "No config file path configured for this server"},
            )

        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"error": "Invalid JSON body"})

        if not isinstance(body, dict):
            return JSONResponse(
                status_code=400, content={"error": "Request body must be a JSON object"}
            )

        # Read existing YAML config (or start from empty dict).
        existing: dict = {}
        if config_path.exists():
            try:
                existing = yaml.safe_load(config_path.read_text()) or {}
            except Exception:
                existing = {}

        # Shallow merge at the top level — callers send full section dicts for
        # nested keys (e.g. {"monitoring": {...merged monitoring block...}}).
        merged: dict = {**existing, **body}

        # Validate the merged config by writing to a temp file and running it
        # through load_config(), which validates via all Pydantic sub-models.
        tmp_path: Path | None = None
        try:
            fd, tmp_name = tempfile.mkstemp(suffix=".yaml")
            tmp_path = Path(tmp_name)
            try:
                os.write(fd, yaml.dump(merged).encode())
            finally:
                os.close(fd)

            try:
                load_config(tmp_path)
            except (ConfigurationError, Exception) as exc:
                return JSONResponse(
                    status_code=400,
                    content={"error": f"Invalid configuration: {exc}"},
                )
        finally:
            if tmp_path is not None:
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

        # Create a backup of the existing config before overwriting.
        if config_path.exists():
            bak_path = config_path.with_suffix(config_path.suffix + ".bak")
            shutil.copy2(config_path, bak_path)

        # Persist the merged config atomically.
        config_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(config_path, yaml.dump(merged))

        return {"updated": True, **body}

    @app.post("/api/runs")
    @app.post("/api/v1/runs")
    @app.post("/api/v1/runs/start")
    async def api_start_run(body: RunRequest):
        try:
            run_id = await runner.start_run(body)
            return {"run_id": run_id, "redirect": f"/runs/{run_id}/live"}
        except ValueError as e:
            return JSONResponse(status_code=409, content={"error": str(e)})
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": str(e)})

    @app.post("/api/runs/{run_id}/cancel")
    @app.post("/api/v1/runs/{run_id}/cancel")
    async def api_cancel_run(run_id: str):
        cancelled = await runner.cancel_run(run_id)
        if cancelled:
            return {"status": "cancelling", "run_id": run_id}
        return JSONResponse(status_code=404, content={"error": "Run not active in this process"})

    @app.post("/api/runs/{run_id}/resume")
    @app.post("/api/v1/runs/{run_id}/resume")
    async def api_resume_run(run_id: str):
        # Fix: use manager.project_workspace — `workspace_dir` was not defined
        # in the create_app() scope and would raise NameError at runtime.
        _workspace = manager.project_workspace
        state_path = _workspace / f"state-{run_id}.json"
        if not state_path.exists():
            # Try generic state.json
            generic = _workspace / "state.json"
            if generic.exists():
                try:
                    data = json.loads(generic.read_text())
                    if data.get("run_id") != run_id:
                        return JSONResponse(status_code=404, content={"error": "No state file for this run"})
                except (json.JSONDecodeError, OSError):
                    return JSONResponse(status_code=404, content={"error": "No state file for this run"})
            else:
                return JSONResponse(status_code=404, content={"error": "No state file for this run"})
            state_data = data
        else:
            state_data = json.loads(state_path.read_text())

        req = RunRequest(
            feature_request=state_data.get("feature_request", ""),
            resume_run_id=run_id,
            workflow_type=state_data.get("workflow_type", "feature_development"),
        )
        try:
            new_run_id = await runner.start_run(req)
            return {"run_id": new_run_id, "redirect": f"/runs/{new_run_id}/live"}
        except ValueError as e:
            return JSONResponse(status_code=409, content={"error": str(e)})

    # --- SSE for live updates ---

    @app.get("/api/runs/{run_id}/stream")
    @app.get("/api/v1/runs/{run_id}/stream")
    async def sse_stream(run_id: str, offset: int = 0):
        """Stream SSE events for a run.

        The optional ``offset`` query parameter causes the stream to start
        from that event index rather than from the beginning, enabling
        reconnecting clients to resume without replaying already-seen events.
        The ``token`` query parameter is consumed by the auth middleware and
        is not used directly here.
        """
        global _active_sse_connections

        if _active_sse_connections >= MAX_SSE_CONNECTIONS:
            return JSONResponse(
                status_code=429,
                content={"error": f"Too many SSE connections (max {MAX_SSE_CONNECTIONS})"},
            )

        async def event_generator():
            global _active_sse_connections
            _active_sse_connections += 1
            start_time = time.monotonic()
            try:
                last_line = max(0, offset)
                while True:
                    # Enforce max duration
                    elapsed = time.monotonic() - start_time
                    if elapsed >= MAX_SSE_DURATION:
                        yield "data: {\"event\": \"stream_timeout\"}\n\n"
                        break
                    events = reader.tail_events(run_id, after_line=last_line)
                    for event in events:
                        yield f"data: {json.dumps(event, default=str)}\n\n"
                        last_line += 1
                    # Check if run is complete
                    if any(e.get("event") == "run_complete" for e in events):
                        yield "data: {\"event\": \"stream_end\"}\n\n"
                        break
                    await asyncio.sleep(1)
            finally:
                _active_sse_connections -= 1

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )

    # --- Health check ---

    @app.get("/healthz")
    @app.get("/health")
    async def healthz():
        from orchestrator import __version__
        return {"status": "ok", "service": "orchestrator-dashboard", "version": __version__}

    return app
