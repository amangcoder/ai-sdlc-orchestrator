"""Directory listing endpoint for the Mobile API.

Endpoints:
  GET /api/v1/directories  — list available workspace directories

Auth: handled by the existing AuthMiddleware applied in app.py.
This router does NOT implement per-route auth.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from orchestrator.mobile_api.models import DirectoryEntry, DirectoryListResponse

logger = logging.getLogger(__name__)

router = APIRouter()


# ── GET /api/v1/directories ────────────────────────────────────────────────


@router.get("/directories")
async def list_directories(request: Request):
    """Return the list of workspace directories available to mobile clients.

    Each entry has an opaque ID (no raw path), display name, optional
    tech-stack tag, and optional last-used timestamp.

    Returns HTTP 500 (not an unhandled exception) if app.state is
    misconfigured.
    """
    try:
        raw_entries: list[dict] = request.app.state.directory_entries
        directories = [DirectoryEntry(**e) for e in raw_entries]
        return DirectoryListResponse(directories=directories).model_dump()
    except Exception as exc:
        logger.exception("Failed to load directory list: %s", exc)
        return JSONResponse(
            status_code=500,
            content={"error": "Failed to load directory list"},
        )
