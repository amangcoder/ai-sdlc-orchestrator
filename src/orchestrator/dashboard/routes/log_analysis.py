"""Log analysis page and API endpoint for orchestrator runs.

Endpoints
---------
GET /runs/{run_id}/log-analysis
    HTML page with three sections:
    - Identified Patterns (with frequency counts)
    - Errors Found (with severity badge, message, and timestamp)
    - Recommendations (actionable bullet list)

GET /api/v1/runs/{run_id}/log-analysis
    JSON response: {run_id, patterns, errors, recommendations}
    Returns 404 if the run has no log file.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from orchestrator.dashboard.routes.validators import _validate_run_id

if TYPE_CHECKING:
    from orchestrator.dashboard.data import RunDataReader

logger = logging.getLogger(__name__)


def create_log_analysis_router(
    templates: Jinja2Templates,
    reader: "RunDataReader",
) -> APIRouter:
    """Return an APIRouter with the log analysis endpoints wired up.

    Args:
        templates: The Jinja2Templates instance shared by the parent app.
        reader: The RunDataReader used to retrieve log analysis data.

    Returns:
        Configured FastAPI APIRouter with 2 routes:
        - GET /runs/{run_id}/log-analysis      → HTML page
        - GET /api/v1/runs/{run_id}/log-analysis → JSON data
    """
    router = APIRouter()

    def _get_analysis_or_404(run_id: str) -> dict:
        """Return log analysis dict or raise HTTPException(404) if no log file.

        Args:
            run_id: The validated run identifier.

        Returns:
            Analysis dict with keys: run_ids, patterns, errors, recommendations.

        Raises:
            HTTPException: 404 if no log file exists for the run.
        """
        # Check if a log file exists for this run before calling analysis.
        # _find_log_file is the canonical private helper on RunDataReader.
        log_file = reader._find_log_file(run_id)
        if log_file is None:
            raise HTTPException(
                status_code=404,
                detail=f"No log data found for run '{run_id}'.",
            )
        return reader.get_log_analysis(run_id)

    # -----------------------------------------------------------------------
    # HTML page — GET /runs/{run_id}/log-analysis
    # -----------------------------------------------------------------------

    @router.get("/runs/{run_id}/log-analysis", response_class=HTMLResponse)
    async def log_analysis_page(request: Request, run_id: str) -> HTMLResponse:
        """Render the log analysis page for a given run.

        Sections rendered:
        - Identified Patterns: list of pattern descriptions with frequency counts.
        - Errors Found: list of errors with severity badge, message, timestamp.
        - Recommendations: bullet list of actionable recommendations.

        Returns 404 HTML if the run has no log data.
        """
        try:
            _validate_run_id(run_id)
        except HTTPException as exc:
            return HTMLResponse(
                f"<h1>Bad Request</h1><p>{exc.detail}</p>",
                status_code=exc.status_code,
            )

        try:
            analysis = _get_analysis_or_404(run_id)
        except HTTPException as exc:
            if exc.status_code == 404:
                return HTMLResponse(
                    f"<h1>Not Found</h1><p>{exc.detail}</p>",
                    status_code=404,
                )
            raise

        return templates.TemplateResponse(
            "log_analysis.html",
            {
                "request": request,
                "run_id": run_id,
                "patterns": analysis.get("patterns", []),
                "errors": analysis.get("errors", []),
                "recommendations": analysis.get("recommendations", []),
                "page_title": f"Log Analysis — {run_id[:8]}...",
            },
        )

    # -----------------------------------------------------------------------
    # JSON API — GET /api/v1/runs/{run_id}/log-analysis
    # -----------------------------------------------------------------------

    @router.get("/api/v1/runs/{run_id}/log-analysis")
    async def api_log_analysis(run_id: str) -> JSONResponse:
        """Return structured log analysis for a single run as JSON.

        Response shape::

            {
                "run_id":          "abc123def456",
                "patterns":        [{"type": "repeated_tasks", "items": [...]}],
                "errors":          [{"severity": "error", "message": "...", "ts": "..."}],
                "recommendations": [{"type": "suggestion", "text": "..."}]
            }

        Returns:
            JSON response with analysis data, or 404 if no log file exists.
        """
        try:
            _validate_run_id(run_id)
        except HTTPException as exc:
            return JSONResponse(
                status_code=exc.status_code,
                content={"error": exc.detail},
            )

        try:
            analysis = _get_analysis_or_404(run_id)
        except HTTPException as exc:
            return JSONResponse(
                status_code=exc.status_code,
                content={"error": exc.detail},
            )
        except Exception as exc:
            logger.error("log_analysis error for run_id=%r: %s", run_id, exc)
            return JSONResponse(
                status_code=500,
                content={"error": "Log analysis failed. Please try again."},
            )

        return JSONResponse(
            content={
                "run_id": run_id,
                "patterns": analysis.get("patterns", []),
                "errors": analysis.get("errors", []),
                "recommendations": analysis.get("recommendations", []),
            }
        )

    return router
