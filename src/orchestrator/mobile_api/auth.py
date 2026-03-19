"""Authentication module for the Mobile API.

FAIL-CLOSED: Raises RuntimeError at import time if ORCHESTRATOR_API_KEY is not set.
This guarantees the server never silently runs without authentication.
"""

from __future__ import annotations

import hmac
import os

from fastapi import Request, HTTPException
from starlette.status import HTTP_401_UNAUTHORIZED

# ── Fail-closed key loading ────────────────────────────────────────────────

_api_key = os.environ.get("ORCHESTRATOR_API_KEY")
if not _api_key:
    raise RuntimeError(
        "ORCHESTRATOR_API_KEY environment variable must be set before starting "
        "the Mobile API server. This is a required security configuration."
    )

ORCHESTRATOR_API_KEY: str = _api_key

# ── Token validation ───────────────────────────────────────────────────────

# Paths that bypass authentication entirely
_EXEMPT_PATHS: frozenset[str] = frozenset({
    "/health",
    "/docs",
    "/docs/",
    "/redoc",
    "/redoc/",
    "/openapi.json",
    "/api/v1/setup/qr",
    # SSH config probe — no auth required per REQ-023.
    # This endpoint only reports TCP reachability of the SSH port;
    # no credentials or sensitive data are ever returned.
    "/api/v1/ssh/config",
})


def verify_token(token: str) -> bool:
    """Validate an API token using timing-safe comparison.

    Uses hmac.compare_digest to prevent timing side-channel attacks.
    Returns True if the token matches ORCHESTRATOR_API_KEY, False otherwise.
    """
    return hmac.compare_digest(token, ORCHESTRATOR_API_KEY)


# ── Auth Middleware ────────────────────────────────────────────────────────

async def auth_middleware(request: Request, call_next):
    """Fail-closed Bearer token authentication for all /api/v1/* HTTP routes.

    Exempt paths: /health, /docs, /openapi.json, /api/v1/setup/qr, /redoc.
    All other /api/v1/* paths require a valid Bearer token.
    Non /api/v1/ paths (except exempt) pass through without authentication.

    WebSocket stream endpoints bypass HTTP auth (auth handled via first frame).
    """
    path = request.url.path

    # Exempt paths pass through without auth
    if path in _EXEMPT_PATHS:
        return await call_next(request)

    # WebSocket stream endpoints bypass HTTP auth (use first-frame auth protocol).
    # Matches /api/v1/runs/{run_id}/stream
    if path.startswith("/api/v1/runs/") and path.endswith("/stream"):
        return await call_next(request)

    # Only apply auth to /api/v1/* paths
    if not path.startswith("/api/v1/"):
        return await call_next(request)

    # Extract Authorization header
    auth_header = request.headers.get("Authorization", "")

    # Must be "Bearer <token>" — scheme-sensitive
    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
        )

    token = auth_header[7:]  # Strip "Bearer " prefix (7 chars)

    # Empty token is rejected
    if not token:
        raise HTTPException(
            status_code=HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
        )

    # Validate using timing-safe comparison
    if not verify_token(token):
        raise HTTPException(
            status_code=HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
        )

    return await call_next(request)
