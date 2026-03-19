"""Directory listing endpoints for the Mobile API.

Endpoints:
  GET  /api/v1/directories                      — list startup-frozen workspace directories
  GET  /api/v1/directories/root                 — get projects_root entry for dynamic browsing
  GET  /api/v1/directories/{dir_id}/children    — list dynamic subdirectory children
  POST /api/v1/directories/{dir_id}/children    — create new subdirectory

Auth: handled by the existing AuthMiddleware applied in app.py.
This router does NOT implement per-route auth.

IMPORTANT: The static /root route must be registered BEFORE the dynamic
/{dir_id}/children route to prevent FastAPI matching "root" as a dir_id.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from orchestrator.mobile_api.models import (
    CreateDirectoryRequest,
    DirectoryChildrenResponse,
    DirectoryEntry,
    DirectoryListResponse,
)

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


# ── GET /api/v1/directories/root ───────────────────────────────────────────
# IMPORTANT: Defined BEFORE /{dir_id}/children so FastAPI resolves "root"
# as a static segment and doesn't treat it as a dir_id parameter.


@router.get("/directories/root")
async def get_root_directory(request: Request):
    """Return the projects_root entry for dynamic directory browsing.

    Returns HTTP 404 if projects_root is not configured in the server config.
    The response contains an opaque ID — no raw filesystem paths.
    """
    projects_root = getattr(request.app.state, "projects_root", None)
    if projects_root is None:
        return JSONResponse(
            status_code=404,
            content={"error": "projects_root is not configured on this server"},
        )

    try:
        from orchestrator.mobile_api.dynamic_directory_service import get_root_entry

        salt = request.app.state.directory_salt
        entry_dict = get_root_entry(projects_root, salt)
        return DirectoryEntry(**entry_dict).model_dump()
    except Exception as exc:
        logger.exception("Failed to build root directory entry: %s", exc)
        return JSONResponse(
            status_code=500,
            content={"error": "Failed to retrieve root directory"},
        )


# ── GET /api/v1/directories/{dir_id}/children ─────────────────────────────


@router.get("/directories/{dir_id}/children")
async def list_directory_children(dir_id: str, request: Request):
    """List immediate subdirectories of the directory identified by dir_id.

    Returns:
        200 DirectoryChildrenResponse — entries, parent_id, depth, at_depth_limit
        400 — directory is at or beyond the maximum browse depth
        403 — dir_id resolves outside projects_root (TOCTOU / access denied)
        404 — projects_root not configured or dir_id cannot be resolved

    Delegates all path resolution and safety logic to DynamicDirectoryService.
    """
    projects_root = getattr(request.app.state, "projects_root", None)
    if projects_root is None:
        return JSONResponse(
            status_code=404,
            content={"error": "projects_root is not configured on this server"},
        )

    try:
        from orchestrator.mobile_api.dynamic_directory_service import (
            compute_depth,
            list_children,
            resolve_dynamic_id,
        )

        salt = request.app.state.directory_salt
        max_depth: int = getattr(request.app.state, "max_browse_depth", 10)

        # Resolve the opaque dir_id to a validated filesystem path
        resolved = resolve_dynamic_id(dir_id, projects_root, salt)
        if resolved is None:
            return JSONResponse(
                status_code=403,
                content={
                    "error": "Directory not found or access denied",
                    "detail": (
                        "The specified dir_id could not be resolved within projects_root"
                    ),
                },
            )

        # Check depth limit
        depth = compute_depth(resolved, projects_root.resolve())
        if depth >= max_depth:
            return JSONResponse(
                status_code=400,
                content={
                    "error": "Maximum directory depth reached",
                    "detail": (
                        f"This directory is at depth {depth}, "
                        f"which equals the maximum allowed depth of {max_depth}"
                    ),
                    "at_depth_limit": True,
                },
            )

        # List the children
        children = list_children(resolved, projects_root, salt, depth, max_depth)
        at_depth_limit = (depth + 1) >= max_depth

        response = DirectoryChildrenResponse(
            entries=[DirectoryEntry(**e) for e in children],
            parent_id=dir_id,
            depth=depth,
            at_depth_limit=at_depth_limit,
        )
        return response.model_dump()

    except Exception as exc:
        logger.exception(
            "Failed to list children for dir_id %s: %s", dir_id, exc
        )
        return JSONResponse(
            status_code=500,
            content={"error": "Failed to list directory children"},
        )


# ── POST /api/v1/directories/{dir_id}/children ────────────────────────────


@router.post("/directories/{dir_id}/children", status_code=201)
async def create_directory(
    dir_id: str,
    body: CreateDirectoryRequest,
    request: Request,
):
    """Create a new subdirectory inside the directory identified by dir_id.

    Returns:
        201 DirectoryEntry — the newly created directory entry
        400 — name fails regex validation
        403 — dir_id resolves outside projects_root (access denied)
        404 — projects_root not configured
        409 — a directory with that name already exists

    Delegates all path resolution and safety logic to DynamicDirectoryService.
    """
    projects_root = getattr(request.app.state, "projects_root", None)
    if projects_root is None:
        return JSONResponse(
            status_code=404,
            content={"error": "projects_root is not configured on this server"},
        )

    try:
        from orchestrator.mobile_api.dynamic_directory_service import (
            create_child,
            resolve_dynamic_id,
        )

        salt = request.app.state.directory_salt

        # Resolve the parent dir_id to a validated filesystem path
        parent_path = resolve_dynamic_id(dir_id, projects_root, salt)
        if parent_path is None:
            return JSONResponse(
                status_code=403,
                content={
                    "error": "Parent directory not found or access denied",
                    "detail": (
                        "The specified dir_id could not be resolved within projects_root"
                    ),
                },
            )

        # Attempt to create the child directory
        entry_dict = create_child(parent_path, body.name, projects_root, salt)
        return JSONResponse(
            status_code=201,
            content=DirectoryEntry(**entry_dict).model_dump(),
        )

    except FileExistsError:
        return JSONResponse(
            status_code=409,
            content={
                "error": "Directory already exists",
                "detail": f"A directory named {body.name!r} already exists at this location",
            },
        )
    except PermissionError as exc:
        return JSONResponse(
            status_code=403,
            content={"error": "Access denied", "detail": str(exc)},
        )
    except ValueError as exc:
        return JSONResponse(
            status_code=400,
            content={"error": "Invalid directory name", "detail": str(exc)},
        )
    except Exception as exc:
        logger.exception(
            "Failed to create directory %r in dir_id %s: %s", body.name, dir_id, exc
        )
        return JSONResponse(
            status_code=500,
            content={"error": "Failed to create directory"},
        )
