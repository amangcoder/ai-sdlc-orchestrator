"""Observability deep-link REST endpoint for the Mobile API.

GET /api/v1/observability?run_id=X — returns deep-link URLs for Grafana, Jaeger, Loki.
Delegates to get_observability_urls() from dashboard.data and probes service reachability.
Requires Bearer token authentication (enforced by auth_middleware).
"""

from __future__ import annotations

import socket
import urllib.parse
from typing import Any, Optional
from urllib.error import URLError
from urllib.request import Request as UrlRequest, urlopen

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

router = APIRouter(tags=["monitoring"])

_PROBE_TIMEOUT_S = 2.0


def _probe_url(url: Optional[str]) -> str:
    """Probe a URL for reachability. Returns 'reachable', 'unreachable', or 'unknown'."""
    if not url:
        return "unknown"
    try:
        parsed = urllib.parse.urlparse(url)
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if not host:
            return "unknown"
        sock = socket.create_connection((host, port), timeout=_PROBE_TIMEOUT_S)
        sock.close()
        return "reachable"
    except (OSError, socket.timeout):
        return "unreachable"
    except Exception:
        return "unknown"


@router.get("/observability")
async def get_observability(
    request: Request,
    run_id: Optional[str] = Query(default=None, description="Run ID for deep-link filtering"),
) -> dict[str, Any]:
    """Return observability deep-link URLs with service reachability status.

    Query Parameters:
        run_id: Optional run ID to scope Grafana/Loki/Jaeger links

    Response schema:
        run_id: str | null
        urls: dict of service name → url
        reachability: dict of service name → "reachable" | "unreachable" | "unknown"
    """
    try:
        from orchestrator.dashboard.data import get_observability_urls

        config = request.app.state.config
        grafana_url: Optional[str] = getattr(config.monitoring, "grafana_url", None)
        jaeger_url: Optional[str] = getattr(config.monitoring, "jaeger_ui_url", None)
        loki_endpoint: Optional[str] = getattr(config.monitoring, "loki_endpoint", None)

        urls = get_observability_urls(
            run_id=run_id,
            grafana_url=grafana_url,
            jaeger_ui_url=jaeger_url,
            loki_endpoint=loki_endpoint,
        )

        # Probe the primary service endpoints (not every derived URL)
        reachability: dict[str, str] = {}
        _PROBE_MAP: dict[str, Optional[str]] = {
            "grafana": grafana_url,
            "jaeger": jaeger_url,
            "loki": loki_endpoint,
        }
        for service, base_url in _PROBE_MAP.items():
            reachability[service] = _probe_url(base_url)

        return {
            "run_id": run_id,
            "urls": urls,
            "reachability": reachability,
        }
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={"error": "Failed to retrieve observability URLs", "detail": str(exc)},
        )
