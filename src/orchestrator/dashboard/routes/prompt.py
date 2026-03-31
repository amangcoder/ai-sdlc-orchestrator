"""Prompt interaction REST endpoints for the web dashboard.

Enables the dashboard to query for pending prompts and submit responses via
REST — complementing the existing WebSocket prompt flow used by the mobile API.

Endpoints
---------
GET  /api/v1/runs/{run_id}/prompt
    Returns {pending: true, agent: str, question: str, run_id: str} when a
    prompt is waiting, or {pending: false, run_id: str} when no prompt is
    pending.

POST /api/v1/runs/{run_id}/prompt
    Body: {response: str}
    Writes the user's response for the running orchestrator to consume via its
    existing poll_for_response() loop.
    Returns {accepted: true, run_id: str} on success.
    Returns 404 when no prompt file is pending for the run.
    Returns 400 for an empty (or whitespace-only) response.

File conventions (from prompt_manager.py)
-----------------------------------------
.prompt-{run_id}.json   — written by the engine when a question is posed.
                          Contains: {prompt_id, question, type, options, created_at}
.response-{run_id}.json — written here so the engine's poll_for_response()
                          loop can consume it.
                          Contains: {prompt_id, response}

Security
--------
The response string is sanitised before being written to disk:
  • Unicode categories Cc (control) and Cf (format/BIDI overrides) are stripped.
  • Maximum length of _MAX_RESPONSE_LEN characters is enforced to prevent abuse.
  • The run_id is validated via _validate_run_id (allowlist regex).
"""

from __future__ import annotations

import json
import logging
import os
import unicodedata
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from orchestrator.dashboard.routes.artifacts import _validate_run_id

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Maximum length of the sanitised response string.
_MAX_RESPONSE_LEN = 4096

#: Unicode general categories that may contain prompt-injection vectors.
#: Cc = control characters (ASCII 0–31, 127, extended Latin-1 controls)
#: Cf = format characters (BIDI overrides, zero-width joiners, soft hyphens …)
_STRIP_CATEGORIES: frozenset[str] = frozenset({"Cc", "Cf"})


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class PromptResponseBody(BaseModel):
    """Request body for POST /api/v1/runs/{run_id}/prompt."""

    response: str = Field(
        ...,
        description="The user's text response to the pending prompt.",
        max_length=_MAX_RESPONSE_LEN,
    )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _sanitize_response(text: str) -> str:
    """Return a sanitised copy of *text* safe to write to the response file.

    Strips Unicode categories Cc (control) and Cf (format/BIDI) to prevent
    prompt-injection via RTL overrides, null bytes, or other invisible chars.
    Trims the result to at most *_MAX_RESPONSE_LEN* characters.

    Args:
        text: Raw response string received from the HTTP request body.

    Returns:
        Sanitised, length-capped string.
    """
    cleaned = "".join(
        ch for ch in text if unicodedata.category(ch) not in _STRIP_CATEGORIES
    )
    return cleaned[:_MAX_RESPONSE_LEN]


def _read_prompt_file(workspace_path: Path, run_id: str) -> dict | None:
    """Read and parse the pending prompt file for *run_id*.

    Args:
        workspace_path: Directory containing ``.prompt-{run_id}.json``.
        run_id:         The run identifier.

    Returns:
        Parsed prompt dict, or ``None`` if the file does not exist or cannot
        be parsed.
    """
    prompt_file = workspace_path / f".prompt-{run_id}.json"
    if not prompt_file.exists():
        return None
    try:
        return json.loads(prompt_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning(
            "Could not read prompt file for run %s at %s: %s",
            run_id,
            prompt_file,
            exc,
        )
        return None


def _write_response_file(
    workspace_path: Path,
    run_id: str,
    prompt_id: str,
    response: str,
) -> None:
    """Atomically write ``.response-{run_id}.json`` for the orchestrator to poll.

    Uses a temp-file + ``os.replace()`` pattern (same as ``write_prompt``) to
    guarantee that the orchestrator never observes a partial write.

    Args:
        workspace_path: Directory in which to write the response file.
        run_id:         The run identifier.
        prompt_id:      Prompt ID from the corresponding prompt file; the
                        orchestrator's ``poll_for_response()`` checks this field.
        response:       Sanitised response string.

    Raises:
        OSError: If the filesystem write fails.
    """
    payload = {"prompt_id": prompt_id, "response": response}
    response_file = workspace_path / f".response-{run_id}.json"
    tmp_file = workspace_path / f".response-{run_id}.json.tmp"
    try:
        tmp_file.write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        os.replace(str(tmp_file), str(response_file))
    except OSError:
        # Attempt cleanup of the temp file; ignore errors.
        try:
            tmp_file.unlink(missing_ok=True)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def create_prompt_router(
    templates: Jinja2Templates,  # noqa: ARG001 — kept for factory signature parity
    workspace_path: Path,
) -> APIRouter:
    """Return an APIRouter wired up with the prompt interaction endpoints.

    Args:
        templates:      The Jinja2Templates instance shared by the parent app.
                        Unused by these JSON-only endpoints but kept for
                        consistency with sibling router factories (e.g.
                        ``create_artifacts_router``).
        workspace_path: Path to the project workspace directory where the
                        orchestrator writes ``.prompt-{run_id}.json`` and
                        polls for ``.response-{run_id}.json``.

    Returns:
        Configured ``fastapi.APIRouter``.
    """
    router = APIRouter()

    # -----------------------------------------------------------------------
    # GET /api/v1/runs/{run_id}/prompt
    # -----------------------------------------------------------------------

    @router.get("/api/v1/runs/{run_id}/prompt")
    async def api_get_prompt(run_id: str) -> dict:
        """Return the pending prompt status for a run.

        When a prompt is waiting::

            {
              "pending": true,
              "run_id": "abc123",
              "agent": "pm",
              "question": "Confirm the tech stack?"
            }

        When no prompt is pending::

            {"pending": false, "run_id": "abc123"}

        The ``agent`` field comes from the prompt file if present; older prompt
        files that pre-date this field will return an empty string.

        Returns:
            200 — always (pending flag differentiates the two cases).
            400 — if run_id contains unexpected characters.
        """
        _validate_run_id(run_id)

        prompt_data = _read_prompt_file(workspace_path, run_id)
        if prompt_data is None:
            return {"pending": False, "run_id": run_id}

        question = str(prompt_data.get("question", ""))
        agent = str(prompt_data.get("agent", ""))

        return {
            "pending": True,
            "run_id": run_id,
            "agent": agent,
            "question": question,
        }

    # -----------------------------------------------------------------------
    # POST /api/v1/runs/{run_id}/prompt
    # -----------------------------------------------------------------------

    @router.post("/api/v1/runs/{run_id}/prompt")
    async def api_post_prompt(run_id: str, body: PromptResponseBody) -> dict:
        """Submit a response to the pending prompt for a run.

        Writes ``.response-{run_id}.json`` to the workspace so the
        orchestrator's ``poll_for_response()`` loop can consume it.  The
        WebSocket prompt flow remains untouched; both delivery paths write the
        same response file format.

        Body::

            {"response": "Confirm"}

        On success::

            {"accepted": true, "run_id": "abc123"}

        Returns:
            200 — response accepted and written to disk.
            400 — ``run_id`` format invalid, or response is empty after
                  sanitisation.
            404 — no ``.prompt-{run_id}.json`` file exists (run has no pending
                  prompt, or run does not exist).
            500 — filesystem write error (logged server-side).
        """
        _validate_run_id(run_id)

        # Check that a prompt is actually pending — return 404 otherwise.
        prompt_data = _read_prompt_file(workspace_path, run_id)
        if prompt_data is None:
            raise HTTPException(
                status_code=404,
                detail=f"No pending prompt found for run {run_id!r}.",
            )

        # Sanitise: strip control/format chars and enforce max length.
        sanitized = _sanitize_response(body.response)

        if not sanitized.strip():
            raise HTTPException(
                status_code=400,
                detail="Response must not be empty.",
            )

        prompt_id = str(prompt_data.get("prompt_id", ""))

        try:
            _write_response_file(workspace_path, run_id, prompt_id, sanitized)
        except OSError as exc:
            logger.error(
                "Failed to write response file for run %s: %s", run_id, exc
            )
            raise HTTPException(
                status_code=500,
                detail="Failed to write response — check server logs.",
            ) from exc

        logger.info(
            "Accepted prompt response for run %s (prompt_id=%s, response_len=%d)",
            run_id,
            prompt_id,
            len(sanitized),
        )
        return {"accepted": True, "run_id": run_id}

    return router
