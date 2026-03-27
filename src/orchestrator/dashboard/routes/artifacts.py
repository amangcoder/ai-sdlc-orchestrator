"""Artifact management dashboard page and JSON API endpoints.

Endpoints
---------
GET  /runs/{run_id}/artifacts-view
    HTML page showing artifact list table, version history modal, diff viewer.

GET  /api/v1/runs/{run_id}/artifacts
    JSON list of ArtifactMetadata for the run.

GET  /api/v1/runs/{run_id}/artifacts/{name}/versions
    JSON list of ArtifactVersion (version history).

GET  /api/v1/runs/{run_id}/artifacts/{name}/versions/{v}
    JSON artifact dict at version *v*, or 404.

GET  /api/v1/artifacts/compare?run_a=&run_b=&artifact=
    ArtifactDiff with added/removed/changed top-level keys.

GET  /api/v1/artifacts/search?q=&type=&agent=
    List of ArtifactMetadata matching the filters.

POST /api/v1/artifacts/retention
    Garbage-collection with dry_run support.
    Requires ``X-Confirm-Retention-Delete: yes`` header when dry_run=False.

Path traversal defence (two layers)
-------------------------------------
Layer 1 — regex allowlist: artifact names must match ``^[a-zA-Z0-9][a-zA-Z0-9_-]*$``
Layer 2 — ArtifactManager._safe_path(): resolves the path and verifies it
          stays within the configured artifacts directory.

Version IDs are parsed as ``int`` path parameters; non-integer values are
rejected by FastAPI before reaching endpoint logic.  Negative / zero values
are rejected explicitly.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Optional

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from orchestrator.artifact_manager import ArtifactManager

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Validation patterns
# ---------------------------------------------------------------------------

# Mirrors the ArtifactManager._NAME_RE pattern exactly so we can reject bad
# names at the HTTP layer before touching the filesystem at all.
_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")

# run_id accepts the same characters used by the orchestrator (hex + dashes/underscores)
_RUN_ID_RE = re.compile(r"^[a-zA-Z0-9_\-]{1,64}$")


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class RetentionRequest(BaseModel):
    """Body for POST /api/v1/artifacts/retention."""

    max_age_days: int = Field(default=90, ge=1, description="Delete versions older than N days")
    max_runs: int = Field(default=100, ge=1, description="Retain at most the N most recent runs")
    keep_failed: bool = Field(default=True, description="Keep artifacts from failed runs")
    dry_run: bool = Field(default=True, description="List candidates without deleting")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_MAX_NAME_LEN = 128  # Reasonable upper bound to prevent abuse


def _validate_name(name: str) -> None:
    """Raise HTTPException(400) if *name* fails the artifact-name allowlist.

    Enforces:
    - Characters: alphanumeric, hyphens, underscores only
    - Starts with a letter or digit (not a hyphen/underscore)
    - Maximum length of 128 characters to prevent abuse / DoS
    """
    if not name or len(name) > _MAX_NAME_LEN or not _NAME_RE.match(name):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid artifact name {name!r}. "
                "Names must start with a letter or digit, contain only "
                "letters, digits, hyphens, and underscores, "
                f"and be at most {_MAX_NAME_LEN} characters."
            ),
        )


def _validate_run_id(run_id: str) -> None:
    """Raise HTTPException(400) if *run_id* contains unexpected characters."""
    if not _RUN_ID_RE.match(run_id):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid run_id {run_id!r}.",
        )


def _validate_version(v: int) -> None:
    """Raise HTTPException(400) if version number is not positive."""
    if v <= 0:
        raise HTTPException(
            status_code=400,
            detail=f"Version number must be a positive integer, got {v}.",
        )


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def create_artifacts_router(
    templates: Jinja2Templates,
    artifact_manager: "ArtifactManager",
) -> APIRouter:
    """Return an APIRouter wired up with all artifact management endpoints.

    Args:
        templates: The Jinja2Templates instance shared by the parent app.
        artifact_manager: Initialised ArtifactManager instance.

    Returns:
        Configured FastAPI APIRouter.
    """
    router = APIRouter()

    # -----------------------------------------------------------------------
    # HTML page
    # -----------------------------------------------------------------------

    @router.get("/runs/{run_id}/artifacts-view", response_class=HTMLResponse)
    async def artifacts_page(request: Request, run_id: str) -> HTMLResponse:
        """Render the artifact list page for a run.

        Displays a table of all artifacts with clickable rows that open a
        version-history modal, and a diff viewer for comparing runs.
        """
        _validate_run_id(run_id)
        try:
            artifacts = artifact_manager.list_artifacts(run_id)
        except Exception as exc:
            logger.warning("list_artifacts failed for run %s: %s", run_id, exc)
            artifacts = []

        return templates.TemplateResponse(
            "artifacts.html",
            {
                "request": request,
                "run_id": run_id,
                "artifacts": [a.model_dump() for a in artifacts],
                "page_title": f"Artifacts — {run_id[:12]}",
            },
        )

    # -----------------------------------------------------------------------
    # GET /api/v1/runs/{run_id}/artifacts
    # -----------------------------------------------------------------------

    @router.get("/api/v1/runs/{run_id}/artifacts")
    async def api_list_artifacts(run_id: str) -> list:
        """Return ArtifactMetadata list for all artifacts in a run.

        Response shape::

            [
              {
                "name": "prd",
                "current_version": 3,
                "run_id": "abc123",
                "agent": "pm",
                "schema_name": null,
                "created_at": "2024-01-01T12:00:00Z",
                "updated_at": "2024-01-01T13:00:00Z",
                "size_bytes": 2048
              },
              ...
            ]
        """
        _validate_run_id(run_id)
        try:
            artifacts = artifact_manager.list_artifacts(run_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            logger.error("list_artifacts error for run %s: %s", run_id, exc)
            raise HTTPException(status_code=500, detail="Failed to list artifacts") from exc
        return [a.model_dump() for a in artifacts]

    # -----------------------------------------------------------------------
    # GET /api/v1/runs/{run_id}/artifacts/{name}/versions
    # -----------------------------------------------------------------------

    @router.get("/api/v1/runs/{run_id}/artifacts/{name}/versions")
    async def api_artifact_versions(run_id: str, name: str) -> list:
        """Return version history for a named artifact.

        Response shape::

            [
              {
                "version": 1,
                "run_id": "abc123",
                "agent": "pm",
                "created_at": "2024-01-01T12:00:00Z",
                "size_bytes": 1024,
                "run_status": "completed"
              },
              ...
            ]

        The list is ordered oldest-first (ascending version number).
        """
        _validate_run_id(run_id)
        _validate_name(name)
        try:
            history = artifact_manager.get_artifact_history(run_id, name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            logger.error(
                "get_artifact_history error for run=%s name=%s: %s", run_id, name, exc
            )
            raise HTTPException(status_code=500, detail="Failed to retrieve version history") from exc
        return [v.model_dump() for v in history]

    # -----------------------------------------------------------------------
    # GET /api/v1/runs/{run_id}/artifacts/{name}/versions/{v}
    # -----------------------------------------------------------------------

    @router.get("/api/v1/runs/{run_id}/artifacts/{name}/versions/{v}")
    async def api_artifact_version(run_id: str, name: str, v: int) -> dict:
        """Return the artifact dict at a specific version number.

        Returns 404 if the artifact or version does not exist.
        Version *v* must be a positive integer.
        """
        _validate_run_id(run_id)
        _validate_name(name)
        _validate_version(v)
        try:
            data = artifact_manager.load_artifact(run_id, name, version=v)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            logger.error(
                "load_artifact error for run=%s name=%s v=%d: %s", run_id, name, v, exc
            )
            raise HTTPException(status_code=500, detail="Failed to load artifact version") from exc

        if data is None:
            raise HTTPException(
                status_code=404,
                detail=f"Artifact {name!r} version {v} not found for run {run_id!r}.",
            )
        return data

    # -----------------------------------------------------------------------
    # GET /api/v1/artifacts/compare
    # -----------------------------------------------------------------------

    @router.get("/api/v1/artifacts/compare")
    async def api_compare_artifacts(
        run_a: str = Query(..., description="First run ID"),
        run_b: str = Query(..., description="Second run ID"),
        artifact: str = Query(..., description="Artifact name to compare"),
    ) -> dict:
        """Return a structural diff between the same artifact across two runs.

        Compares the top-level JSON keys present in each version.

        Response shape::

            {
              "name": "prd",
              "run_a": "abc123",
              "run_b": "def456",
              "version_a": 2,
              "version_b": 3,
              "added": ["new_key"],
              "removed": ["old_key"],
              "changed": ["modified_key"]
            }
        """
        _validate_run_id(run_a)
        _validate_run_id(run_b)
        _validate_name(artifact)
        try:
            diff = artifact_manager.compare_artifacts(run_a, run_b, artifact)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            logger.error(
                "compare_artifacts error run_a=%s run_b=%s artifact=%s: %s",
                run_a, run_b, artifact, exc,
            )
            raise HTTPException(status_code=500, detail="Failed to compare artifacts") from exc
        return diff.model_dump()

    # -----------------------------------------------------------------------
    # GET /api/v1/artifacts/search
    # -----------------------------------------------------------------------

    @router.get("/api/v1/artifacts/search")
    async def api_search_artifacts(
        q: str = Query(default="", description="Full-text search query"),
        type: Optional[str] = Query(default=None, alias="type", description="Filter by schema_name"),
        agent: Optional[str] = Query(default=None, description="Filter by agent name"),
    ) -> list:
        """Search artifacts by query string, type (schema_name), and agent.

        Response is a list of ArtifactMetadata dicts matching ALL supplied filters.

        Response shape::

            [{"name": "prd", "run_id": "abc123", ...}, ...]
        """
        # Validate optional filter values to prevent injection
        if type is not None and not _NAME_RE.match(type):
            raise HTTPException(status_code=400, detail=f"Invalid artifact type filter {type!r}.")
        if agent is not None and not re.match(r"^[a-zA-Z0-9_\-]{1,64}$", agent):
            raise HTTPException(status_code=400, detail=f"Invalid agent filter {agent!r}.")

        try:
            results = artifact_manager.search_artifacts(
                query=q,
                artifact_type=type,
                agent=agent,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            logger.error("search_artifacts error q=%r type=%r agent=%r: %s", q, type, agent, exc)
            raise HTTPException(status_code=500, detail="Failed to search artifacts") from exc
        return [a.model_dump() for a in results]

    # -----------------------------------------------------------------------
    # POST /api/v1/artifacts/retention
    # -----------------------------------------------------------------------

    @router.post("/api/v1/artifacts/retention")
    async def api_retention(
        body: RetentionRequest,
        x_confirm_retention_delete: Optional[str] = Header(
            default=None,
            alias="X-Confirm-Retention-Delete",
            description="Must be 'yes' when dry_run=false to confirm destructive deletion.",
        ),
    ) -> dict:
        """Run (or preview) the artifact retention / garbage-collection policy.

        When ``dry_run=true`` (the default), the endpoint lists what *would*
        be deleted without touching any files.

        When ``dry_run=false``, the caller **must** supply the header::

            X-Confirm-Retention-Delete: yes

        Without that header a 403 is returned to prevent accidental deletion.

        Response shape (RetentionResult)::

            {
              "deleted_versions": 12,
              "deleted_paths": [".versions/prd/v1.json", ...],
              "retained_versions": 30,
              "dry_run": false
            }
        """
        if not body.dry_run:
            if x_confirm_retention_delete != "yes":
                raise HTTPException(
                    status_code=403,
                    detail=(
                        "Destructive retention delete requires the header "
                        "'X-Confirm-Retention-Delete: yes'. "
                        "Set dry_run=true to preview without deleting."
                    ),
                )

        try:
            result = artifact_manager.apply_retention_policy(
                max_age_days=body.max_age_days,
                max_runs=body.max_runs,
                keep_failed=body.keep_failed,
                dry_run=body.dry_run,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            logger.error("apply_retention_policy error: %s", exc)
            raise HTTPException(status_code=500, detail="Retention policy execution failed") from exc
        return result.model_dump()

    return router
