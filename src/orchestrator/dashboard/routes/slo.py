"""SLO (Service Level Objective) dashboard page and JSON API endpoint.

Endpoints
---------
GET /slo
    HTML page with:
    - 6-row SLO compliance matrix (SLI name, target, actual, status)
    - Error budget gauges rendered as CSS progress bars (0–100 %)
    - Violation timeline listing any currently-failing SLIs

GET /api/v1/slo
    JSON SLOReport with full SLI details.

Color coding:
    green  — error budget ≥ 80 %
    yellow — error budget 20–80 %
    red    — error budget < 20 %

Auth is enforced by the parent application's HTTP middleware; these routes
do not perform auth themselves.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import TYPE_CHECKING, Any, Optional

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from orchestrator.dashboard.data import get_slo_report

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Display-formatting helpers
# ---------------------------------------------------------------------------

#: SLI names that are expressed as fractions (0–1) and should display as %.
_RATE_SLIS: frozenset[str] = frozenset({
    "pipeline_success_rate",
    "artifact_validation_rate",
    "recovery_success_rate",
})

#: Human-readable display names for each canonical SLI name.
_SLI_DISPLAY_NAMES: dict[str, str] = {
    "pipeline_success_rate": "Pipeline Success Rate",
    "phase_duration_p95": "Phase Duration p95",
    "cost_per_run_p50": "Cost Per Run p50",
    "artifact_validation_rate": "Artifact Validation Rate",
    "error_rate": "Error Rate Per Run",
    "recovery_success_rate": "Recovery Success Rate",
}

#: SLI names where the unit is seconds.
_SECONDS_SLIS: frozenset[str] = frozenset({"phase_duration_p95"})

#: SLI names where the unit is USD.
_USD_SLIS: frozenset[str] = frozenset({"cost_per_run_p50"})


def _format_sli_value(name: str, value: float) -> str:
    """Return a human-readable string for an SLI value.

    Args:
        name:  Canonical SLI name.
        value: Raw numeric value.

    Returns:
        Formatted string with unit suffix.
    """
    if name in _RATE_SLIS:
        return f"{value * 100:.2f}%"
    if name in _SECONDS_SLIS:
        return f"{value:.1f}s"
    if name in _USD_SLIS:
        return f"${value:.4f}"
    # error_rate — plain count
    return f"{value:.2f}"


def _budget_color(budget_pct: float) -> str:
    """Return the CSS color class for a given error-budget percentage.

    Args:
        budget_pct: Error budget remaining in percent (0–100).

    Returns:
        One of ``"green"``, ``"yellow"``, or ``"red"``.
    """
    if budget_pct >= 80.0:
        return "green"
    if budget_pct >= 20.0:
        return "yellow"
    return "red"


def _enrich_slis(slis: list[Any]) -> list[dict[str, Any]]:
    """Convert SLIResult objects into template-friendly dicts.

    Each dict has all original fields plus:
    - ``target_display``  — formatted target value string
    - ``actual_display``  — formatted actual value string
    - ``budget_color``    — ``"green"`` / ``"yellow"`` / ``"red"``
    - ``budget_pct``      — clamped 0–100 float

    Args:
        slis: List of :class:`~orchestrator.monitoring.slo.SLIResult` instances.

    Returns:
        List of enriched dicts, one per SLI.
    """
    enriched = []
    for sli in slis:
        # SLIResult is a dataclass; convert to dict first for safe access.
        d = dataclasses.asdict(sli) if dataclasses.is_dataclass(sli) else dict(sli)
        name = d["name"]
        budget_pct = float(d.get("error_budget_remaining_pct", 0.0))
        budget_pct = max(0.0, min(100.0, budget_pct))
        d["budget_pct"] = budget_pct
        d["budget_color"] = _budget_color(budget_pct)
        d["target_display"] = _format_sli_value(name, float(d.get("target", 0.0)))
        d["actual_display"] = _format_sli_value(name, float(d.get("actual", 0.0)))
        d["display_name"] = _SLI_DISPLAY_NAMES.get(name, name)
        enriched.append(d)
    return enriched


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------

def create_slo_router(
    templates: Jinja2Templates,
    monitoring_stack: Optional[Any] = None,
) -> APIRouter:
    """Return an APIRouter with the SLO dashboard endpoints wired up.

    Args:
        templates:        Jinja2Templates instance shared by the parent app.
        monitoring_stack: Optional :class:`~orchestrator.monitoring.MonitoringStack`
                          instance.  When present and its ``slo_tracker`` is not
                          ``None``, real SLI data is evaluated.  When absent the
                          page renders with neutral/default values instead of
                          returning a 404.

    Returns:
        Configured FastAPI :class:`~fastapi.APIRouter` with 2 routes:

        * ``GET /slo``         → HTML compliance matrix page
        * ``GET /api/v1/slo``  → JSON :class:`~orchestrator.monitoring.slo.SLOReport`
    """
    router = APIRouter()

    @router.get("/slo", response_class=HTMLResponse)
    async def slo_page(request: Request) -> HTMLResponse:
        """Render the SLO compliance dashboard page.

        Always returns 200 — when the SLO tracker is disabled the page
        renders with neutral default values rather than a 404 or error.
        """
        slo_data = get_slo_report(monitoring_stack)
        report = slo_data["report"]
        data_available = slo_data["data_available"]

        slis: list[dict[str, Any]] = []
        all_passing = False
        evaluated_at = ""
        evaluation_window_hours = 24

        if report is not None:
            raw_slis = getattr(report, "slis", [])
            slis = _enrich_slis(raw_slis)
            all_passing = getattr(report, "all_passing", False)
            evaluated_at = getattr(report, "evaluated_at", "")
            evaluation_window_hours = getattr(report, "evaluation_window_hours", 24)

        # Build violation timeline: currently-failing SLIs ordered by worst budget.
        violations = [s for s in slis if not s.get("passing", True)]
        violations.sort(key=lambda s: s.get("budget_pct", 100.0))

        # Derive summary banner: worst status wins (breached > at_risk > passing).
        breached_count = sum(1 for s in slis if s.get("budget_color") == "red")
        at_risk_count = sum(1 for s in slis if s.get("budget_color") == "yellow")
        if breached_count > 0:
            banner_status = "breached"
        elif at_risk_count > 0:
            banner_status = "at_risk"
        else:
            banner_status = "passing"

        return templates.TemplateResponse(
            "slo.html",
            {
                "request": request,
                "slis": slis,
                "all_passing": all_passing,
                "evaluated_at": evaluated_at,
                "evaluation_window_hours": evaluation_window_hours,
                "violations": violations,
                "data_available": data_available,
                "page_title": "SLO Compliance",
                "at_risk_count": at_risk_count,
                "breached_count": breached_count,
                "banner_status": banner_status,
            },
        )

    @router.get("/api/v1/slo")
    async def api_slo() -> dict[str, Any]:
        """Return the SLO evaluation report as JSON.

        Response shape::

            {
                "evaluated_at": "2024-01-01T00:00:00+00:00",
                "evaluation_window_hours": 24,
                "all_passing": false,
                "data_available": false,
                "slis": [
                    {
                        "name": "pipeline_success_rate",
                        "target": 0.95,
                        "actual": 0.0,
                        "passing": false,
                        "error_budget_remaining_pct": 100.0
                    },
                    ...
                ]
            }
        """
        slo_data = get_slo_report(monitoring_stack)
        report = slo_data["report"]
        data_available = slo_data["data_available"]

        if report is None:
            return {
                "evaluated_at": "",
                "evaluation_window_hours": 24,
                "all_passing": False,
                "data_available": False,
                "slis": [],
            }

        if dataclasses.is_dataclass(report):
            report_dict = dataclasses.asdict(report)
        else:
            report_dict = dict(report)

        report_dict["data_available"] = data_available
        return report_dict

    return router
