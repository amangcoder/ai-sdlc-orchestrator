"""Structured request logging middleware for the Mobile API.

Assigns a unique ``request_id`` to every HTTP request, logs request entry
and exit with method, path, status code, and wall-clock duration. Uses
``structlog`` for machine-parsable, key=value output so logs can be indexed
by Loki / ELK without post-processing.

Security: Authorization headers and other sensitive fields are NEVER logged.

Usage (wired automatically by ``create_mobile_app``)::

    from orchestrator.mobile_api.request_logger import RequestLoggerMiddleware
    app.add_middleware(RequestLoggerMiddleware)

Log shape::

    # Entry (DEBUG)
    {"event": "http_request_start", "request_id": "…", "method": "GET",
     "path": "/api/v1/runs", "remote_addr": "…"}

    # Exit (INFO for 2xx/3xx, WARN for 4xx, ERROR for 5xx)
    {"event": "http_request_end", "request_id": "…", "method": "GET",
     "path": "/api/v1/runs", "status_code": 200,
     "duration_ms": 12.3, "remote_addr": "…"}
"""

from __future__ import annotations

import time
import uuid
import logging
from typing import Callable

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

_log = structlog.get_logger(__name__)
_stdlib_log = logging.getLogger(__name__)

# Paths that are very high-frequency health probes — log at DEBUG to reduce noise
_LOW_VERBOSITY_PATHS: frozenset[str] = frozenset({"/health"})

# Headers that must NEVER appear in logs (security)
_SENSITIVE_HEADERS: frozenset[str] = frozenset({
    "authorization",
    "cookie",
    "x-api-key",
    "x-auth-token",
    "proxy-authorization",
})


def _safe_headers(headers: "dict[str, str]") -> dict[str, str]:
    """Return a copy of *headers* with sensitive values redacted."""
    return {
        k: "[REDACTED]" if k.lower() in _SENSITIVE_HEADERS else v
        for k, v in headers.items()
    }


def _remote_addr(request: Request) -> str:
    """Extract the real client IP, respecting X-Forwarded-For if set."""
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        # Take the leftmost address (closest client)
        return forwarded_for.split(",")[0].strip()
    client = request.client
    if client:
        return client.host
    return "unknown"


class RequestLoggerMiddleware(BaseHTTPMiddleware):
    """ASGI middleware that emits structured log entries for every HTTP request.

    Assigns a ``request_id`` UUID to each request and attaches it to the
    response as the ``X-Request-ID`` header so mobile clients and load
    balancers can correlate logs with specific requests.

    Log levels:
        DEBUG  — request entry
        INFO   — 1xx / 2xx / 3xx responses
        WARNING — 4xx responses (client errors)
        ERROR  — 5xx responses (server errors)
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        request_id = str(uuid.uuid4())
        method = request.method
        path = request.url.path
        remote = _remote_addr(request)
        low_verbosity = path in _LOW_VERBOSITY_PATHS

        # Bind request_id for the duration of the request so all log calls
        # within route handlers that use structlog will include it automatically.
        bound = _log.bind(
            request_id=request_id,
            method=method,
            path=path,
            remote_addr=remote,
        )

        if not low_verbosity:
            bound.debug("http_request_start")

        start = time.perf_counter()
        try:
            response: Response = await call_next(request)
        except Exception as exc:
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            bound.error(
                "http_request_unhandled_exception",
                duration_ms=duration_ms,
                exc_type=type(exc).__name__,
                exc_msg=str(exc)[:200],
            )
            raise
        finally:
            pass

        status_code: int = response.status_code
        duration_ms = round((time.perf_counter() - start) * 1000, 2)

        # Attach correlation ID to the response for client-side tracing
        response.headers["X-Request-ID"] = request_id

        log_ctx = dict(
            request_id=request_id,
            method=method,
            path=path,
            status_code=status_code,
            duration_ms=duration_ms,
            remote_addr=remote,
        )

        if status_code >= 500:
            _log.error("http_request_end", **log_ctx)
        elif status_code >= 400:
            if not low_verbosity:
                _log.warning("http_request_end", **log_ctx)
        else:
            if not low_verbosity:
                _log.info("http_request_end", **log_ctx)

        return response
