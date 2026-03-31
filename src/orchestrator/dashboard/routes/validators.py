"""Shared validation patterns for dashboard routes.

Centralises the ``_RUN_ID_RE`` pattern and associated validator functions so
that ``routes/artifacts.py``, ``routes/observability.py``, and ``app.py`` all
agree on what constitutes a valid run_id.  Import from here instead of
duplicating the regex in multiple modules.
"""

from __future__ import annotations

import re

from fastapi import HTTPException

# ---------------------------------------------------------------------------
# Shared patterns
# ---------------------------------------------------------------------------

# run_id accepts the same characters used by the orchestrator (hex + dashes/
# underscores).  Single source of truth — previously duplicated independently
# in routes/artifacts.py and routes/observability.py.
_RUN_ID_RE = re.compile(r"^[a-zA-Z0-9_\-]{1,64}$")

# Artifact name allowlist — mirrors ArtifactManager._NAME_RE exactly so HTTP
# validation matches filesystem validation.
_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")

_MAX_NAME_LEN = 128  # Reasonable upper bound to prevent abuse / DoS


# ---------------------------------------------------------------------------
# Validator helpers
# ---------------------------------------------------------------------------


def _validate_run_id(run_id: str) -> None:
    """Raise ``HTTPException(400)`` if *run_id* contains unexpected characters."""
    if not _RUN_ID_RE.match(run_id):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid run_id {run_id!r}.",
        )


def _validate_name(name: str) -> None:
    """Raise ``HTTPException(400)`` if *name* fails the artifact-name allowlist.

    Enforces:
    - Characters: alphanumeric, hyphens, underscores only
    - Starts with a letter or digit (not a hyphen/underscore)
    - Maximum length of 128 characters to prevent abuse / DoS

    Use this for the ArtifactManager's extensionless artifact names (e.g. ``prd``,
    ``architecture``).  For raw filenames that may include an extension (e.g.
    ``prd.json``) use :func:`_validate_filename` instead.
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


# Filename regex for raw on-disk artifact files (may have a dot-extension).
# Allows:  alphanumeric, hyphens, underscores, single dots (for extensions).
# Blocks:  directory separators, null bytes, leading dots (.env, .htaccess),
#          consecutive dots (..) to prevent traversal.
_FILENAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_\-]*(?:\.[a-zA-Z0-9]+)?$")
_MAX_FILENAME_LEN = 256


def _validate_filename(name: str) -> None:
    """Raise ``HTTPException(400)`` for raw filenames that fail the allowlist.

    This is a looser check than :func:`_validate_name` — it permits a single
    dot-extension (e.g. ``prd.json``) but still blocks path traversal patterns,
    hidden files, and names with path separators.  The real confinement defence
    is the ``resolve()`` + ``is_relative_to()`` check performed after this.
    """
    if not name or len(name) > _MAX_FILENAME_LEN or not _FILENAME_RE.match(name):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid filename {name!r}. "
                "Names must start with a letter or digit, contain only "
                "letters, digits, hyphens, underscores, and at most one "
                f"dot-extension, and be at most {_MAX_FILENAME_LEN} characters."
            ),
        )
