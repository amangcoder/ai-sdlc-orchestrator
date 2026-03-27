"""Cost analytics dashboard page and JSON API endpoint.

Endpoints
---------
GET /cost-analytics
    HTML page with Chart.js visualisations:
    - Cost trend over time (line chart)
    - Cost by agent (stacked bar chart)
    - Cost by model (pie chart)
    - Burn rate (gauge / doughnut chart)

GET /api/v1/cost-analytics
    JSON response: {cost_trend, by_agent, by_model, burn_rate}
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

if TYPE_CHECKING:
    from orchestrator.dashboard.data import RunDataReader

logger = logging.getLogger(__name__)


def create_cost_router(
    templates: Jinja2Templates,
    reader: "RunDataReader",
) -> APIRouter:
    """Return an APIRouter with the cost analytics endpoints wired up.

    Args:
        templates: The Jinja2Templates instance shared by the parent app.
        reader: The RunDataReader used to aggregate workspace cost data.

    Returns:
        Configured FastAPI APIRouter with 2 routes:
        - GET /cost-analytics      → HTML dashboard page
        - GET /api/v1/cost-analytics → JSON data
    """
    router = APIRouter()

    @router.get("/cost-analytics", response_class=HTMLResponse)
    async def cost_analytics_page(request: Request) -> HTMLResponse:
        """Render the cost analytics dashboard page with Chart.js charts."""
        analytics = reader.get_cost_analytics()
        return templates.TemplateResponse(
            "cost_analytics.html",
            {
                "request": request,
                "analytics": analytics,
                "analytics_json": json.dumps(analytics, default=str),
                "page_title": "Cost Analytics",
            },
        )

    @router.get("/api/v1/cost-analytics")
    async def api_cost_analytics() -> dict:
        """Return cost analytics data as JSON.

        Response shape::

            {
                "cost_trend": [{"date": "2024-01-01", "cost": 0.42}, ...],
                "by_agent":   {"pm": {"cost": 0.10, "count": 3}, ...},
                "by_model":   {"claude-3-5-sonnet": {"cost": 0.30, "count": 12}, ...},
                "burn_rate":  0.15
            }
        """
        return reader.get_cost_analytics()

    return router
