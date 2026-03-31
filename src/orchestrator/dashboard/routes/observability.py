"""Observability dashboard page — deep links to Grafana, Jaeger, and Loki.

Endpoint:
  GET /observability?run_id=<id>  — HTML page with deep-link cards.

Deep links are generated from MonitoringConfig URL fields:
  - grafana_url      → 5 Grafana dashboard links + Loki Explore link
  - jaeger_ui_url    → Jaeger trace-search link
  - loki_endpoint    → Direct Loki API query link

No iframes are used (per REQ-019).  All links open in a new browser tab.

Service status cards (Prometheus, Grafana, Jaeger, Loki, Promtail) are loaded
client-side by fetching GET /api/v1/monitoring/health.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Optional
from urllib.parse import quote

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from orchestrator.dashboard.data import get_observability_urls
from orchestrator.dashboard.routes.validators import _RUN_ID_RE  # shared pattern

if TYPE_CHECKING:
    from orchestrator.dashboard.data import RunDataReader

logger = logging.getLogger(__name__)

# Display metadata for the five monitored services.
# Used to pre-render cards while health data is being fetched.
_SERVICE_DISPLAY: list[dict] = [
    {"name": "Prometheus", "key": "prometheus", "port": 9090,
     "deep_link_tpl": "http://localhost:9090"},
    {"name": "Grafana",    "key": "grafana",    "port": 3000,
     "deep_link_tpl": "http://localhost:3000"},
    {"name": "Jaeger",     "key": "jaeger",     "port": 16686,
     "deep_link_tpl": "http://localhost:16686/search?service=orchestrator"},
    {"name": "Loki",       "key": "loki",       "port": 3100,
     "deep_link_tpl": "http://localhost:3100"},
    {"name": "Promtail",   "key": "promtail",   "port": 9080,
     "deep_link_tpl": "http://localhost:9080/ready"},
]

# _RUN_ID_RE is imported from routes.validators (single source of truth).
# Pattern: hex characters (8–64 chars) plus dashes/underscores for forward-compat.


def _load_monitoring_config(config_path: Optional[Path]):  # type: ignore[return]
    """Load MonitoringConfig from the YAML config file.

    Returns a MonitoringConfig instance, or None if the config is absent /
    has no monitoring section / fails to parse.
    """
    if not config_path or not config_path.exists():
        return None
    try:
        import yaml  # type: ignore[import-untyped]
        raw = yaml.safe_load(config_path.read_text()) or {}
        monitoring_raw = raw.get("monitoring", {})
        if not monitoring_raw:
            return None
        from orchestrator.monitoring.config import MonitoringConfig  # local import to avoid circular deps
        return MonitoringConfig(**monitoring_raw)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not load MonitoringConfig for observability page: %s", exc)
        return None


def create_observability_router(
    templates: Jinja2Templates,
    config_path: Optional[Path] = None,
    reader: Optional["RunDataReader"] = None,
) -> APIRouter:
    """Return an APIRouter with the GET /observability endpoint wired up.

    Args:
        templates: The Jinja2Templates instance shared by the parent app.
        config_path: Optional path to the orchestrator YAML config file.
                     Used to load MonitoringConfig URL fields at request time.
        reader: Optional RunDataReader.  When provided, the observability page
                populates the run-ID filter dropdown from available runs.

    Returns:
        Configured FastAPI APIRouter.
    """
    router = APIRouter()

    @router.get("/observability", response_class=HTMLResponse)
    async def observability_page(
        request: Request,
        run_id: Optional[str] = Query(default=None, description="Filter deep links to a specific run ID"),
    ) -> HTMLResponse:
        """Render the observability hub page.

        Query parameters
        ----------------
        run_id : str, optional
            When supplied the generated deep links are pre-filtered to that
            specific run so operators can jump straight into the relevant
            Grafana panels / Jaeger trace / Loki log stream.
        """
        # Sanitise run_id to prevent open-redirect / injection attacks.
        safe_run_id: Optional[str] = None
        if run_id is not None:
            if _RUN_ID_RE.match(run_id):
                safe_run_id = run_id
            else:
                logger.warning("Ignoring unsafe run_id query param: %r", run_id[:64])

        # Load monitoring config (failures are non-fatal — page renders with
        # placeholder instructions instead of real URLs).
        mon_config = _load_monitoring_config(config_path)

        grafana_url: Optional[str] = None
        jaeger_ui_url: Optional[str] = None
        loki_endpoint: Optional[str] = None
        config_missing = True

        if mon_config is not None:
            grafana_url = mon_config.grafana_url
            jaeger_ui_url = mon_config.jaeger_ui_url
            loki_endpoint = mon_config.loki_endpoint
            # Config is considered "present" when at least one URL is configured.
            config_missing = not any([grafana_url, jaeger_ui_url, loki_endpoint])

        urls = get_observability_urls(
            run_id=safe_run_id,
            grafana_url=grafana_url,
            jaeger_ui_url=jaeger_ui_url,
            loki_endpoint=loki_endpoint,
        )

        # Build run list for the dropdown (most-recent first, capped at 100).
        runs: list[dict] = []
        if reader is not None:
            try:
                for r in reader.list_runs()[:100]:
                    runs.append({
                        "run_id": r.run_id,
                        "label": f"{r.run_id[:12]}… — {r.status}",
                    })
            except Exception as exc:  # noqa: BLE001
                logger.debug("Could not load runs for observability dropdown: %s", exc)

        return templates.TemplateResponse(
            "observability.html",
            {
                "request": request,
                "urls": urls,
                "urls_json": json.dumps(urls),
                "run_id": safe_run_id or "",
                "runs": runs,
                "config_missing": config_missing,
                "grafana_configured": grafana_url is not None,
                "jaeger_configured": jaeger_ui_url is not None,
                "loki_configured": loki_endpoint is not None,
                "grafana_url": grafana_url or "",
                "jaeger_ui_url": jaeger_ui_url or "",
                "monitoring_services": _SERVICE_DISPLAY,
                "page_title": "Observability",
            },
        )

    return router
