"""Settings dashboard page.

Endpoints
---------
GET /settings
    HTML page with four tabbed sections:
    - Monitoring  (metrics, tracing, Prometheus, Grafana, Jaeger, Loki URLs)
    - Artifacts   (versioning toggle, retention policy, GC controls)
    - SLOs        (target values for 6 SLIs)
    - Advanced    (all remaining config fields)

The page uses JavaScript to:
    1. Fetch current config from GET /api/v1/config on load.
    2. Populate form fields.
    3. Send only *changed* fields to PUT /api/v1/config on save.
    4. Display inline validation errors and toast notifications.
    5. Run the GC preview/execute flow via POST /api/v1/artifacts/retention.

Auth is enforced by the parent application's HTTP middleware; this route
does not perform auth itself.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

logger = logging.getLogger(__name__)


def create_settings_router(
    templates: Jinja2Templates,
    config_path: Optional[Path] = None,
) -> APIRouter:
    """Return an APIRouter with the GET /settings endpoint wired up.

    Args:
        templates: The Jinja2Templates instance shared by the parent app.
        config_path: Optional path to the orchestrator YAML config file.
                     Passed to the template so the client-side JS knows
                     whether the server has a writable config path configured.

    Returns:
        Configured FastAPI APIRouter.
    """
    router = APIRouter()

    @router.get("/settings", response_class=HTMLResponse)
    async def settings_page(request: Request) -> HTMLResponse:
        """Render the settings configuration page.

        The page renders with no pre-fetched config — a ``<script>`` in the
        template will call ``GET /api/v1/config`` after the DOM is ready and
        populate all form fields client-side.  This keeps the route thin and
        avoids YAML/config parsing on the server for every page load.
        """
        return templates.TemplateResponse(
            "settings.html",
            {
                "request": request,
                # Inform the template (and embedded JS) whether a config path
                # was supplied so the save button can be disabled if not.
                "has_config": config_path is not None,
                "config_path_display": str(config_path) if config_path else "not configured",
                "page_title": "Settings",
            },
        )

    return router
