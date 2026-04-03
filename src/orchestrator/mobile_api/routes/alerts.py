"""Alerts and monitoring health REST endpoints for the Mobile API.

GET /api/v1/alerts — returns alert history.
GET /api/v1/monitoring/health — returns component health status.
Delegates to RunDataReader methods via app.state.reader.
Requires Bearer token authentication (enforced by auth_middleware).
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

router = APIRouter(tags=["monitoring"])


@router.get("/alerts")
async def get_alerts(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500, description="Maximum alerts to return"),
) -> dict[str, Any]:
    """Return alert history from the workspace.

    Response schema:
        alerts: list of:
            id: str
            severity: str — "critical" | "warning" | "info"
            message: str
            triggered_at: str — ISO-8601 UTC
            status: str — "active" | "resolved"
        total: int
    """
    try:
        reader = request.app.state.reader
        raw_alerts = reader.get_alert_history(limit=limit)

        alerts = []
        for idx, item in enumerate(raw_alerts):
            alerts.append({
                "id": item.get("id", str(idx)),
                "severity": item.get("severity", "info"),
                "message": item.get("message", item.get("text", "")),
                "triggered_at": item.get("triggered_at", item.get("timestamp", "")),
                "status": item.get("status", "active"),
            })

        return {
            "alerts": alerts,
            "total": len(alerts),
        }
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={"error": "Failed to retrieve alerts", "detail": str(exc)},
        )


@router.get("/monitoring/health")
async def get_monitoring_health(request: Request) -> dict[str, Any]:
    """Return component health for monitoring stack services.

    Probes: Prometheus, Grafana, Jaeger, Loki.

    Response schema:
        components: list of:
            name: str
            status: "healthy" | "degraded" | "unavailable"
            latency_ms: float | null
            error: str | null
    """
    try:
        reader = request.app.state.reader
        raw_health = reader.get_monitoring_health()

        components = []
        for item in raw_health:
            components.append({
                "name": item.get("name", "unknown"),
                "status": item.get("status", "unavailable"),
                "latency_ms": item.get("latency_ms"),
                "error": item.get("error"),
            })

        return {
            "components": components,
            "healthy_count": sum(1 for c in components if c["status"] == "healthy"),
            "total_count": len(components),
        }
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={"error": "Failed to retrieve monitoring health", "detail": str(exc)},
        )
