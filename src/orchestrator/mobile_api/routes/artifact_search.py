"""Artifact Search REST endpoint for the Mobile API.

GET /api/v1/artifacts/search?q=&type=&agent= — full-text artifact search across all runs.
Delegates to RunDataReader.search_artifacts_global() via app.state.reader.
Requires Bearer token authentication (enforced by auth_middleware).
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

router = APIRouter(tags=["artifacts"])


@router.get("/artifacts/search")
async def search_artifacts(
    request: Request,
    q: Optional[str] = Query(default=None, alias="q", description="Search query string"),
    type: Optional[str] = Query(default=None, description="Filter by artifact type/schema"),
    agent: Optional[str] = Query(default=None, description="Filter by agent name"),
) -> dict[str, Any]:
    """Search artifacts globally across all workspace runs.

    Query Parameters:
        q: Search query string (required for meaningful results)
        type: Optional artifact type filter (e.g. 'prd', 'architecture')
        agent: Optional agent name filter

    Response schema:
        query: str
        results: list of:
            artifact_name: str
            run_id: str
            schema: str | null
            agent: str | null
            version: int
            updated_at: str
        total: int
    """
    if not q:
        return JSONResponse(
            status_code=400,
            content={"error": "Query parameter 'q' is required"},
        )

    # Validate query length to prevent abuse
    if len(q) > 256:
        return JSONResponse(
            status_code=400,
            content={"error": "Query parameter 'q' exceeds maximum length of 256 characters"},
        )

    try:
        reader = request.app.state.reader
        raw_results = reader.search_artifacts_global(
            query=q,
            artifact_type=type or None,
            agent=agent or None,
        )

        results = []
        for item in raw_results:
            results.append({
                "artifact_name": item.get("name", item.get("artifact_name", "")),
                "run_id": item.get("run_id", ""),
                "schema": item.get("schema_name", item.get("schema")),
                "agent": item.get("agent"),
                "version": item.get("current_version", item.get("version", 1)),
                "updated_at": item.get("updated_at", ""),
            })

        return {
            "query": q,
            "results": results,
            "total": len(results),
        }
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={"error": "Failed to search artifacts", "detail": str(exc)},
        )
