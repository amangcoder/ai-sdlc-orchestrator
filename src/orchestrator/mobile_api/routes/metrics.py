"""Metrics summary REST endpoint for the Mobile API.

GET /api/v1/metrics — returns aggregated pipeline metrics.
Delegates to RunDataReader.get_metrics_summary() via app.state.reader.
Requires Bearer token authentication (enforced by auth_middleware).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(tags=["monitoring"])


@router.get("/metrics")
async def get_metrics(request: Request) -> dict[str, Any]:
    """Return aggregated pipeline metrics summary.

    Response schema:
        total_runs: int
        active_runs: int
        total_cost_usd: float
        avg_phase_duration_seconds: float
    """
    try:
        reader = request.app.state.reader
        summary = reader.get_metrics_summary()

        return {
            "total_runs": summary.get("total_runs", 0),
            "active_runs": summary.get("active_runs", 0),
            "total_cost_usd": round(float(summary.get("total_cost_usd", 0.0)), 6),
            "avg_phase_duration_seconds": round(
                float(summary.get("avg_phase_duration_seconds", 0.0)), 3
            ),
        }
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={"error": "Failed to retrieve metrics", "detail": str(exc)},
        )
