"""Monitoring health API endpoint.

Endpoint:
  GET /api/v1/monitoring/health  — probe monitoring services and return status.

Returns an array of service status objects for: Prometheus, Grafana,
Jaeger, Loki, and Promtail.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter

from orchestrator.dashboard.data import RunDataReader

logger = logging.getLogger(__name__)

# Canonical monitoring services checked by this endpoint.
# Order matches the UI display order: Prometheus, Grafana, Jaeger, Loki, Promtail.
_MONITORING_SERVICES: list[tuple[str, int]] = [
    ("prometheus", 9090),
    ("grafana", 3000),
    ("jaeger", 16686),
    ("loki", 3100),
    ("promtail", 9080),
]


def create_monitoring_health_router(reader: RunDataReader) -> APIRouter:
    """Return an APIRouter with GET /api/v1/monitoring/health wired up.

    Args:
        reader: RunDataReader instance used for health probing.

    Returns:
        Configured FastAPI APIRouter.
    """
    router = APIRouter()

    @router.get("/api/v1/monitoring/health")
    async def monitoring_health():
        """Probe monitoring services and return their reachability status.

        Returns:
            List of service status dicts, each containing:
            - ``service``: human-readable service name
            - ``port``: configured port number
            - ``status``: ``"up"`` when reachable, ``"down"`` otherwise
            - ``response_time_ms``: round-trip latency in ms (``-1`` when down)
            - ``http_status``: HTTP response code (``-1`` when down)
        """
        return reader.get_monitoring_health(services=_MONITORING_SERVICES)

    return router
