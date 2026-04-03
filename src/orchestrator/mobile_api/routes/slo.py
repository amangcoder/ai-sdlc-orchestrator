"""SLO Compliance REST endpoint for the Mobile API.

GET /api/v1/slo — returns the current SLO compliance report with all SLIs.
Delegates to get_slo_report() from dashboard.data using app.state.monitoring_stack.
Requires Bearer token authentication (enforced by auth_middleware).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(tags=["monitoring"])

# Map SLI passing/near-miss/breached states to mobile-friendly status labels
_STATUS_PASSING = "passing"
_STATUS_AT_RISK = "at_risk"
_STATUS_BREACHED = "breached"

_AT_RISK_THRESHOLD = 10.0  # error_budget_remaining_pct below this → at_risk


def _sli_status(passing: bool, error_budget_remaining_pct: float) -> str:
    """Derive mobile status label from SLI result fields."""
    if passing:
        if error_budget_remaining_pct < _AT_RISK_THRESHOLD:
            return _STATUS_AT_RISK
        return _STATUS_PASSING
    return _STATUS_BREACHED


@router.get("/slo")
async def get_slo(request: Request) -> dict[str, Any]:
    """Return SLO compliance report with all 6 SLIs.

    Response schema:
        evaluated_at: str — ISO-8601 UTC timestamp
        all_passing: bool
        data_available: bool — True when real SLO data is available
        slis: list of:
            name: str
            target: float
            current_value: float
            status: "passing" | "at_risk" | "breached"
            error_budget_remaining_pct: float
    """
    try:
        from orchestrator.dashboard.data import get_slo_report

        monitoring_stack = getattr(request.app.state, "monitoring_stack", None)
        result = get_slo_report(monitoring_stack)

        report = result.get("report")
        data_available: bool = result.get("data_available", False)

        if report is None:
            return JSONResponse(
                status_code=503,
                content={"error": "SLO report unavailable"},
            )

        slis = []
        for sli in getattr(report, "slis", []):
            slis.append({
                "name": sli.name,
                "target": sli.target,
                "current_value": sli.actual,
                "status": _sli_status(sli.passing, sli.error_budget_remaining_pct),
                "error_budget_remaining_pct": round(sli.error_budget_remaining_pct, 2),
            })

        return {
            "evaluated_at": getattr(report, "evaluated_at", ""),
            "all_passing": getattr(report, "all_passing", False),
            "data_available": data_available,
            "slis": slis,
        }
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={"error": "Failed to retrieve SLO report", "detail": str(exc)},
        )
