"""Cost Analytics REST endpoint for the Mobile API.

GET /api/v1/cost-analytics — returns aggregated cost analytics from workspace runs.
Delegates to RunDataReader.get_cost_analytics() via app.state.reader.
Requires Bearer token authentication (enforced by auth_middleware).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(tags=["monitoring"])


@router.get("/cost-analytics")
async def get_cost_analytics(request: Request) -> dict[str, Any]:
    """Return aggregated cost analytics across all workspace runs.

    Response schema:
        total_spend_usd: float — cumulative cost across all runs
        burn_rate_usd_per_hour: float — current USD/hour burn rate
        avg_cost_per_run: float — mean cost per run
        cost_by_agent: list[{agent, cost_usd, run_count}]
        cost_by_model: list[{model, cost_usd, run_count}]
    """
    try:
        reader = request.app.state.reader
        raw = reader.get_cost_analytics()

        # Compute total_spend_usd from by_agent dict
        by_agent_raw: dict[str, dict[str, Any]] = raw.get("by_agent", {})
        total_spend_usd: float = sum(v.get("cost", 0.0) for v in by_agent_raw.values())

        # Fallback: scan cost_trend for total
        cost_trend = raw.get("cost_trend", [])
        if not total_spend_usd and cost_trend:
            total_spend_usd = sum(entry.get("cost", 0.0) for entry in cost_trend)

        burn_rate_usd_per_hour: float = raw.get("burn_rate", 0.0)

        # avg_cost_per_run — derive from by_agent counts
        total_runs = max(max((v.get("count", 1) for v in by_agent_raw.values()), default=1), 1)
        avg_cost_per_run: float = total_spend_usd / total_runs if total_runs else 0.0

        cost_by_agent = [
            {
                "agent": agent,
                "cost_usd": round(data.get("cost", 0.0), 6),
                "run_count": data.get("count", 0),
            }
            for agent, data in by_agent_raw.items()
        ]

        by_model_raw: dict[str, dict[str, Any]] = raw.get("by_model", {})
        cost_by_model = [
            {
                "model": model,
                "cost_usd": round(data.get("cost", 0.0), 6),
                "run_count": data.get("count", 0),
            }
            for model, data in by_model_raw.items()
        ]

        return {
            "total_spend_usd": round(total_spend_usd, 6),
            "burn_rate_usd_per_hour": round(burn_rate_usd_per_hour, 6),
            "avg_cost_per_run": round(avg_cost_per_run, 6),
            "cost_by_agent": cost_by_agent,
            "cost_by_model": cost_by_model,
        }
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={"error": "Failed to retrieve cost analytics", "detail": str(exc)},
        )
