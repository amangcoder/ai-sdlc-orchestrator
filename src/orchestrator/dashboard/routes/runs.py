"""Runs list page with pagination and filtering.

Endpoints
---------
GET /runs
    HTML page listing orchestration runs with:
    - Filter bar (status, workflow type, date range)
    - 25 runs per page with Previous / Next pagination
    - Active (running) runs pinned to the top regardless of sort

The route is exposed via the factory function ``create_runs_router`` so it
can be registered by the parent ``create_app()`` factory without the router
module needing a reference to the application-level ``FastAPI`` instance.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

if TYPE_CHECKING:
    from orchestrator.dashboard.data import RunDataReader, RunSummary

logger = logging.getLogger(__name__)

# Runs displayed per page — matches REQ-002 acceptance criterion.
_PER_PAGE = 25


def _compute_duration(start_time: Optional[str], end_time: Optional[str]) -> str:
    """Return a human-readable duration string.

    For completed/failed runs the duration spans ``start_time`` →
    ``end_time``.  For running runs (``end_time`` is ``None``) the
    duration spans ``start_time`` → *now*.

    Returns ``"-"`` when ``start_time`` is absent or cannot be parsed.
    """
    if not start_time:
        return "-"
    try:
        start = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        if end_time:
            end = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
        else:
            end = datetime.now(tz=timezone.utc)
        delta_secs = max(0, int((end - start).total_seconds()))
        hours, rem = divmod(delta_secs, 3600)
        minutes, seconds = divmod(rem, 60)
        if hours:
            return f"{hours}h {minutes}m"
        if minutes:
            return f"{minutes}m {seconds}s"
        return f"{seconds}s"
    except (ValueError, TypeError):
        return "-"


def _enrich_run(run: "RunSummary") -> dict[str, Any]:
    """Convert a :class:`RunSummary` to a plain dict enriched with *duration*.

    The template receives plain dicts so it does not need to import or know
    about the ``RunSummary`` dataclass.
    """
    return {
        "run_id": run.run_id,
        "feature_request": run.feature_request,
        "workflow_type": run.workflow_type,
        "status": run.status,
        "total_cost_usd": run.total_cost_usd,
        "start_time": run.start_time,
        "end_time": run.end_time,
        "steps_completed": run.steps_completed,
        "steps_total": run.steps_total,
        "duration": _compute_duration(run.start_time, run.end_time),
    }


def create_runs_router(
    templates: Jinja2Templates,
    reader: "RunDataReader",
) -> APIRouter:
    """Return a configured :class:`~fastapi.APIRouter` for the /runs page.

    Args:
        templates: The Jinja2Templates instance shared by the parent app.
        reader:    :class:`~orchestrator.dashboard.data.RunDataReader` used
                   to retrieve paginated run data.

    Returns:
        A :class:`~fastapi.APIRouter` with one route:

        * ``GET /runs`` — HTML runs list with filtering and pagination.
    """
    router = APIRouter()

    @router.get("/runs", response_class=HTMLResponse)
    async def runs_page(
        request: Request,
        page: int = Query(default=1, ge=1, description="1-based page number"),
        status: Optional[str] = Query(
            default=None,
            description="Filter by run status (running/completed/failed/cancelled)",
        ),
        workflow: Optional[str] = Query(
            default=None,
            description="Filter by workflow type",
        ),
        date_from: Optional[str] = Query(
            default=None,
            alias="date_from",
            description="ISO-8601 date lower bound (YYYY-MM-DD)",
        ),
        date_to: Optional[str] = Query(
            default=None,
            alias="date_to",
            description="ISO-8601 date upper bound (YYYY-MM-DD)",
        ),
    ) -> HTMLResponse:
        """Render the paginated runs list page.

        Query parameters are echoed back to the template so the filter bar
        reflects the current selection and pagination links preserve filters.
        """
        # ------------------------------------------------------------------
        # Normalise filter values — treat empty string / "all" as no filter.
        # ------------------------------------------------------------------
        status_filter: Optional[str] = (
            status.strip()
            if status and status.strip() and status.strip() != "all"
            else None
        )
        workflow_filter: Optional[str] = (
            workflow.strip()
            if workflow and workflow.strip() and workflow.strip() != "all"
            else None
        )
        date_from_filter: Optional[str] = (
            date_from.strip() if date_from and date_from.strip() else None
        )
        date_to_filter: Optional[str] = (
            date_to.strip() if date_to and date_to.strip() else None
        )

        # ------------------------------------------------------------------
        # Fetch paginated data from the data layer.
        # ------------------------------------------------------------------
        try:
            result = reader.list_runs_paginated(
                page=page,
                per_page=_PER_PAGE,
                status_filter=status_filter,
                workflow_filter=workflow_filter,
                date_from=date_from_filter,
                date_to=date_to_filter,
            )
        except Exception:
            logger.exception("runs_page: list_runs_paginated failed")
            result = {
                "total_count": 0,
                "page": 1,
                "per_page": _PER_PAGE,
                "total_pages": 1,
                "runs": [],
            }

        # ------------------------------------------------------------------
        # Enrich runs with computed duration field.
        # ------------------------------------------------------------------
        enriched = [_enrich_run(r) for r in result["runs"]]

        # ------------------------------------------------------------------
        # Build pagination base URL that preserves active filters.
        # Each link only needs to append &page=N.
        # ------------------------------------------------------------------
        _params: list[str] = []
        if status_filter:
            _params.append(f"status={status_filter}")
        if workflow_filter:
            _params.append(f"workflow={workflow_filter}")
        if date_from_filter:
            _params.append(f"date_from={date_from_filter}")
        if date_to_filter:
            _params.append(f"date_to={date_to_filter}")

        if _params:
            pagination_base_url = "/runs?" + "&".join(_params) + "&page="
        else:
            pagination_base_url = "/runs?page="

        return templates.TemplateResponse(
            "runs.html",
            {
                "request": request,
                "runs": enriched,
                "total_count": result["total_count"],
                "page": result["page"],
                "per_page": result["per_page"],
                "total_pages": result["total_pages"],
                # Filter state echoed back for the filter-bar form.
                "filter_status": status or "",
                "filter_workflow": workflow or "",
                "filter_date_from": date_from or "",
                "filter_date_to": date_to or "",
                # Pre-built URL prefix for pagination Previous / Next links.
                "pagination_base_url": pagination_base_url,
                "page_title": "All Runs",
            },
        )

    return router
