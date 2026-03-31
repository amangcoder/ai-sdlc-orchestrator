"""Global artifact search page and API endpoint.

Endpoints
---------
GET  /artifacts
    HTML search page with a form (query, artifact type, agent filters) that
    submits via GET query parameters for bookmarkability.  Results are rendered
    server-side so the page is fully functional without JavaScript.

GET  /api/v1/artifacts/search
    JSON array of artifact dicts matching the supplied filters across **all**
    runs.  Delegates to :meth:`RunDataReader.search_artifacts_global`.

Query parameters (both endpoints)
----------------------------------
q       Full-text search string — case-insensitive substring match against
        artifact name and schema name.
type    Artifact schema filter (e.g. ``prd``, ``architecture``).
agent   Agent name filter (e.g. ``pm``, ``architect``).
limit   Maximum number of results (default 50, max 500).
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

if TYPE_CHECKING:
    from orchestrator.dashboard.data import RunDataReader

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Known schema types (mirrors src/schemas/*.schema.json filenames)
# ---------------------------------------------------------------------------

_KNOWN_SCHEMA_TYPES: list[str] = [
    "accessibility_audit",
    "api_contract",
    "architecture",
    "behavioral_review",
    "benchmark_report",
    "change_impact_analysis",
    "competitor_research",
    "compliance_report",
    "cost_estimate",
    "data_pipeline_design",
    "debate_conclusion",
    "debate_position",
    "dependency_audit",
    "end_user_evaluation",
    "engineering_plan",
    "field_specialist_review",
    "incident_report",
    "integration_test_plan",
    "legal_review",
    "load_test_report",
    "market_research",
    "mcp_test_report",
    "mcp_tool_spec",
    "migration_plan",
    "prd",
    "qa_plan",
    "qa_report",
    "refactoring_plan",
    "release_plan",
    "resilience_test_plan",
    "review",
    "runbook",
    "tasks",
    "tech_debt_inventory",
    "threat_model",
    "ux_spec",
    "vulnerability_report",
]

# ---------------------------------------------------------------------------
# Known agent names (common orchestrator roles)
# ---------------------------------------------------------------------------

_KNOWN_AGENTS: list[str] = [
    "pm",
    "architect",
    "principal_engineer",
    "tpm",
    "backend_engineer",
    "frontend_engineer",
    "automation_engineer",
    "qa",
    "security_engineer",
    "data_engineer",
    "database_engineer",
    "devops_engineer",
    "integration_test_engineer",
    "llm_specialist",
    "api_contract_designer",
    "caching_performance_engineer",
    "deep_researcher",
    "dependency_auditor",
    "load_test_engineer",
    "change_impact_analyzer",
]

# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

_AGENT_RE = re.compile(r"^[a-zA-Z0-9_\-]{1,64}$")
_TYPE_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_\-]*$")
_MAX_QUERY_LEN = 256


def _validate_search_params(
    q: str,
    artifact_type: Optional[str],
    agent: Optional[str],
) -> None:
    """Raise :exc:`HTTPException` (400) if any search parameter is invalid.

    Args:
        q: Free-text search query.
        artifact_type: Optional schema type filter.
        agent: Optional agent name filter.

    Raises:
        HTTPException: status 400 if any parameter fails validation.
    """
    if len(q) > _MAX_QUERY_LEN:
        raise HTTPException(
            status_code=400,
            detail=f"Query string too long (max {_MAX_QUERY_LEN} characters).",
        )
    if artifact_type is not None and (
        not artifact_type or not _TYPE_RE.match(artifact_type)
    ):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid artifact type filter {artifact_type!r}.",
        )
    if agent is not None and (not agent or not _AGENT_RE.match(agent)):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid agent filter {agent!r}.",
        )


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def create_search_router(
    templates: Jinja2Templates,
    reader: "RunDataReader",
) -> APIRouter:
    """Return an APIRouter with the global artifact search endpoints.

    Registers two routes:
    - ``GET /artifacts``  — server-side-rendered search page.
    - ``GET /api/v1/artifacts/search``  — JSON search API.

    Both endpoints delegate data retrieval to
    :meth:`RunDataReader.search_artifacts_global`.

    Args:
        templates: The :class:`Jinja2Templates` instance shared by the parent
                   application.
        reader: Initialised :class:`RunDataReader` for cross-run artifact
                lookup.

    Returns:
        Configured :class:`fastapi.APIRouter`.
    """
    router = APIRouter()

    # -----------------------------------------------------------------------
    # HTML page — GET /artifacts
    # -----------------------------------------------------------------------

    @router.get("/artifacts", response_class=HTMLResponse)
    async def artifacts_search_page(
        request: Request,
        q: str = Query(default="", description="Full-text search query"),
        type: Optional[str] = Query(
            default=None,
            alias="type",
            description="Filter by artifact schema type",
        ),
        agent: Optional[str] = Query(
            default=None, description="Filter by agent name"
        ),
        limit: int = Query(
            default=50, ge=1, le=500, description="Maximum number of results"
        ),
    ) -> HTMLResponse:
        """Render the global artifact search page with server-side results.

        The form submits via ``GET`` so the search query is reflected in the
        URL, making results fully bookmarkable and shareable.
        """
        error_message: Optional[str] = None
        results: list[dict] = []
        search_performed = bool(q or type or agent)

        if search_performed:
            try:
                _validate_search_params(q, type, agent)
            except HTTPException as exc:
                error_message = exc.detail
            else:
                try:
                    results = reader.search_artifacts_global(
                        query=q,
                        artifact_type=type,
                        agent=agent,
                        limit=limit,
                    )
                except Exception as exc:
                    logger.error(
                        "search_artifacts_global error q=%r type=%r agent=%r: %s",
                        q,
                        type,
                        agent,
                        exc,
                    )
                    error_message = "Search failed — please try again."

        return templates.TemplateResponse(
            "artifacts_search.html",
            {
                "request": request,
                "page_title": "Artifact Search",
                "results": results,
                "query_q": q,
                "query_type": type or "",
                "query_agent": agent or "",
                "query_limit": limit,
                "error_message": error_message,
                "schema_types": _KNOWN_SCHEMA_TYPES,
                "known_agents": _KNOWN_AGENTS,
                "search_performed": search_performed,
            },
        )

    # -----------------------------------------------------------------------
    # JSON API — GET /api/v1/artifacts/search
    # -----------------------------------------------------------------------

    @router.get("/api/v1/artifacts/search")
    async def api_search_artifacts_global(
        q: str = Query(default="", description="Full-text search query"),
        type: Optional[str] = Query(
            default=None,
            alias="type",
            description="Filter by artifact schema_name",
        ),
        agent: Optional[str] = Query(
            default=None, description="Filter by agent name"
        ),
        limit: int = Query(
            default=50, ge=1, le=500, description="Maximum results to return"
        ),
    ) -> list:
        """Search artifacts globally across all runs.

        Returns a JSON array of matching artifact dicts with the keys:
        ``name``, ``run_id``, ``schema``, ``agent``, ``version``,
        ``updated_at``, ``size_bytes``.

        All supplied filters are applied with AND semantics.  Passing no
        filters (empty ``q``, no ``type``, no ``agent``) returns the most
        recent *limit* artifacts across all runs.

        Response shape::

            [
              {
                "name": "prd",
                "run_id": "abc123def456",
                "schema": "prd",
                "agent": "pm",
                "version": 3,
                "updated_at": "2024-01-01T13:00:00Z",
                "size_bytes": 2048
              },
              ...
            ]
        """
        try:
            _validate_search_params(q, type, agent)
        except HTTPException:
            raise

        try:
            results = reader.search_artifacts_global(
                query=q,
                artifact_type=type,
                agent=agent,
                limit=limit,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            logger.error(
                "search_artifacts_global error q=%r type=%r agent=%r: %s",
                q,
                type,
                agent,
                exc,
            )
            raise HTTPException(status_code=500, detail="Search failed") from exc

        return results

    return router
