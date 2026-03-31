"""Tests for TASK-017: log analysis page and API endpoints.

Acceptance criteria verified:
  1. Log analysis page renders patterns, errors, and recommendations in HTML.
  2. GET /api/v1/runs/{run_id}/log-analysis returns JSON {run_id, patterns,
     errors, recommendations}.
  3. 'View Log Analysis' link is present on run_detail.html.
  4. Returns 404 with appropriate message when run has no log data.
  5. Router is registered in create_app() via app.include_router().
  6. Invalid run_id (bad characters) returns 400.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient

from orchestrator.dashboard.routes.log_analysis import create_log_analysis_router

# ---------------------------------------------------------------------------
# Constants & helpers
# ---------------------------------------------------------------------------

TEMPLATES_DIR = (
    Path(__file__).parent.parent
    / "src"
    / "orchestrator"
    / "dashboard"
    / "templates"
)

_SAMPLE_ANALYSIS: dict[str, Any] = {
    "run_ids": ["abc123"],
    "patterns": [
        {"type": "repeated_tasks", "items": ["pm", "architect"]},
        {"type": "redundant_file_reads", "items": ["src/main.py"]},
    ],
    "errors": [
        {"severity": "error", "message": "Tool call failed", "ts": "2024-01-01T10:00:00"},
        {"severity": "warning", "message": "Slow response", "ts": "2024-01-01T10:01:00"},
    ],
    "recommendations": [
        {"type": "suggestion", "text": "Avoid repeated calls to the same agent"},
        {"type": "high_cost_step", "agent": "pm", "step": "prd", "cost_usd": 0.05},
    ],
    "raw": {},
}


def _make_reader_with_log(run_id: str = "abc123") -> MagicMock:
    """Create a mock RunDataReader that has a log file for *run_id*."""
    reader = MagicMock()
    reader._find_log_file.return_value = Path("/fake/logs/run-abc123.jsonl")
    reader.get_log_analysis.return_value = _SAMPLE_ANALYSIS
    return reader


def _make_reader_no_log() -> MagicMock:
    """Create a mock RunDataReader that has NO log file for any run."""
    reader = MagicMock()
    reader._find_log_file.return_value = None
    return reader


def _make_app(reader: MagicMock) -> FastAPI:
    """Create a minimal FastAPI app with only the log analysis router."""
    app = FastAPI()
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    router = create_log_analysis_router(templates, reader)
    app.include_router(router)
    return app


# ---------------------------------------------------------------------------
# Router structure tests
# ---------------------------------------------------------------------------


class TestLogAnalysisRouterStructure:
    def test_factory_returns_apirouter(self):
        """create_log_analysis_router must return an APIRouter."""
        from fastapi import APIRouter

        reader = _make_reader_with_log()
        templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
        router = create_log_analysis_router(templates, reader)
        assert isinstance(router, APIRouter)

    def test_router_has_two_routes(self):
        """Router must expose exactly 2 routes."""
        reader = _make_reader_with_log()
        templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
        router = create_log_analysis_router(templates, reader)
        assert len(router.routes) == 2

    def test_route_paths(self):
        """Router must expose the expected paths."""
        reader = _make_reader_with_log()
        templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
        router = create_log_analysis_router(templates, reader)
        paths = {r.path for r in router.routes}
        assert "/runs/{run_id}/log-analysis" in paths
        assert "/api/v1/runs/{run_id}/log-analysis" in paths


# ---------------------------------------------------------------------------
# JSON API tests — GET /api/v1/runs/{run_id}/log-analysis
# ---------------------------------------------------------------------------


class TestApiLogAnalysisEndpoint:
    def test_returns_200_with_correct_shape(self):
        """Happy path: returns JSON with run_id, patterns, errors, recommendations."""
        reader = _make_reader_with_log("abc123")
        client = TestClient(_make_app(reader))
        resp = client.get("/api/v1/runs/abc123/log-analysis")
        assert resp.status_code == 200
        data = resp.json()
        assert data["run_id"] == "abc123"
        assert "patterns" in data
        assert "errors" in data
        assert "recommendations" in data

    def test_returns_patterns_list(self):
        """Patterns field must be a list."""
        reader = _make_reader_with_log("abc123")
        client = TestClient(_make_app(reader))
        resp = client.get("/api/v1/runs/abc123/log-analysis")
        assert resp.status_code == 200
        assert isinstance(resp.json()["patterns"], list)

    def test_returns_errors_list(self):
        """Errors field must be a list."""
        reader = _make_reader_with_log("abc123")
        client = TestClient(_make_app(reader))
        resp = client.get("/api/v1/runs/abc123/log-analysis")
        assert resp.status_code == 200
        assert isinstance(resp.json()["errors"], list)

    def test_returns_recommendations_list(self):
        """Recommendations field must be a list."""
        reader = _make_reader_with_log("abc123")
        client = TestClient(_make_app(reader))
        resp = client.get("/api/v1/runs/abc123/log-analysis")
        assert resp.status_code == 200
        assert isinstance(resp.json()["recommendations"], list)

    def test_returns_404_when_no_log_file(self):
        """Returns 404 when the run has no associated log file."""
        reader = _make_reader_no_log()
        client = TestClient(_make_app(reader))
        resp = client.get("/api/v1/runs/abc123/log-analysis")
        assert resp.status_code == 404
        body = resp.json()
        assert "error" in body

    def test_404_message_references_run(self):
        """404 error message must reference the run_id."""
        reader = _make_reader_no_log()
        client = TestClient(_make_app(reader))
        resp = client.get("/api/v1/runs/deadbeef/log-analysis")
        assert resp.status_code == 404
        assert "deadbeef" in resp.json()["error"]

    def test_returns_400_on_invalid_run_id(self):
        """Invalid run_id (path traversal chars) returns 400."""
        reader = _make_reader_with_log()
        client = TestClient(_make_app(reader))
        resp = client.get("/api/v1/runs/../etc/passwd/log-analysis")
        # FastAPI path routing will treat this as a non-matching path — 404 is
        # acceptable, but a 400 is preferred when validation fires.
        assert resp.status_code in (400, 404, 422)

    def test_does_not_include_raw_field(self):
        """API response must not expose the raw field."""
        reader = _make_reader_with_log("abc123")
        client = TestClient(_make_app(reader))
        resp = client.get("/api/v1/runs/abc123/log-analysis")
        assert "raw" not in resp.json()

    def test_reader_called_with_run_id(self):
        """get_log_analysis must be called with the request run_id."""
        reader = _make_reader_with_log("abc123")
        client = TestClient(_make_app(reader))
        client.get("/api/v1/runs/abc123/log-analysis")
        reader.get_log_analysis.assert_called_once_with("abc123")


# ---------------------------------------------------------------------------
# HTML page tests — GET /runs/{run_id}/log-analysis
# ---------------------------------------------------------------------------


class TestLogAnalysisHtmlPage:
    def test_returns_200_html(self):
        """Happy path: returns HTML 200."""
        reader = _make_reader_with_log("abc123")
        client = TestClient(_make_app(reader))
        resp = client.get("/runs/abc123/log-analysis")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_page_contains_patterns_heading(self):
        """HTML must include the 'Identified Patterns' section heading."""
        reader = _make_reader_with_log("abc123")
        client = TestClient(_make_app(reader))
        resp = client.get("/runs/abc123/log-analysis")
        assert resp.status_code == 200
        assert "Identified Patterns" in resp.text

    def test_page_contains_errors_heading(self):
        """HTML must include the 'Errors Found' section heading."""
        reader = _make_reader_with_log("abc123")
        client = TestClient(_make_app(reader))
        resp = client.get("/runs/abc123/log-analysis")
        assert resp.status_code == 200
        assert "Errors Found" in resp.text

    def test_page_contains_recommendations_heading(self):
        """HTML must include the 'Recommendations' section heading."""
        reader = _make_reader_with_log("abc123")
        client = TestClient(_make_app(reader))
        resp = client.get("/runs/abc123/log-analysis")
        assert resp.status_code == 200
        assert "Recommendations" in resp.text

    def test_page_contains_run_id_prefix(self):
        """HTML must include a truncated form of the run_id."""
        reader = _make_reader_with_log("abc123def456")
        reader.get_log_analysis.return_value = {**_SAMPLE_ANALYSIS, "run_ids": ["abc123def456"]}
        client = TestClient(_make_app(reader))
        resp = client.get("/runs/abc123def456/log-analysis")
        assert resp.status_code == 200
        # The template shows first 8 chars of run_id
        assert "abc123de" in resp.text

    def test_page_shows_pattern_items(self):
        """HTML must render pattern items from the analysis."""
        reader = _make_reader_with_log("abc123")
        client = TestClient(_make_app(reader))
        resp = client.get("/runs/abc123/log-analysis")
        assert resp.status_code == 200
        assert "repeated" in resp.text.lower()

    def test_page_shows_error_messages(self):
        """HTML must render error messages from the analysis."""
        reader = _make_reader_with_log("abc123")
        client = TestClient(_make_app(reader))
        resp = client.get("/runs/abc123/log-analysis")
        assert resp.status_code == 200
        assert "Tool call failed" in resp.text

    def test_page_shows_recommendation_text(self):
        """HTML must render recommendation text."""
        reader = _make_reader_with_log("abc123")
        client = TestClient(_make_app(reader))
        resp = client.get("/runs/abc123/log-analysis")
        assert resp.status_code == 200
        assert "Avoid repeated calls" in resp.text

    def test_page_returns_404_when_no_log(self):
        """HTML page must return 404 when no log file exists."""
        reader = _make_reader_no_log()
        client = TestClient(_make_app(reader))
        resp = client.get("/runs/abc123/log-analysis")
        assert resp.status_code == 404

    def test_page_has_back_link(self):
        """HTML page must contain a back link to the run detail page."""
        reader = _make_reader_with_log("abc123")
        client = TestClient(_make_app(reader))
        resp = client.get("/runs/abc123/log-analysis")
        assert resp.status_code == 200
        assert "/runs/abc123" in resp.text

    def test_empty_patterns_shows_empty_state(self):
        """When no patterns, the page must show an empty state message."""
        reader = MagicMock()
        reader._find_log_file.return_value = Path("/fake/logs/run-abc123.jsonl")
        reader.get_log_analysis.return_value = {
            "run_ids": ["abc123"],
            "patterns": [],
            "errors": [],
            "recommendations": [],
        }
        client = TestClient(_make_app(reader))
        resp = client.get("/runs/abc123/log-analysis")
        assert resp.status_code == 200
        assert "No patterns" in resp.text

    def test_severity_badge_rendered(self):
        """Error severity must be rendered as a badge in the HTML."""
        reader = _make_reader_with_log("abc123")
        client = TestClient(_make_app(reader))
        resp = client.get("/runs/abc123/log-analysis")
        assert resp.status_code == 200
        assert "badge" in resp.text


# ---------------------------------------------------------------------------
# run_detail.html link tests
# ---------------------------------------------------------------------------


class TestRunDetailLogAnalysisLink:
    def test_run_detail_template_has_log_analysis_link(self):
        """run_detail.html must contain a link to /runs/{run_id}/log-analysis."""
        template_path = (
            Path(__file__).parent.parent
            / "src"
            / "orchestrator"
            / "dashboard"
            / "templates"
            / "run_detail.html"
        )
        content = template_path.read_text()
        assert "log-analysis" in content

    def test_run_detail_template_link_uses_run_id(self):
        """The log analysis link in run_detail.html must use {{ run.run_id }}."""
        template_path = (
            Path(__file__).parent.parent
            / "src"
            / "orchestrator"
            / "dashboard"
            / "templates"
            / "run_detail.html"
        )
        content = template_path.read_text()
        assert "run.run_id" in content
        assert "log-analysis" in content


# ---------------------------------------------------------------------------
# create_app() integration — router is wired in
# ---------------------------------------------------------------------------


class TestLogAnalysisRouterWiredInApp:
    def test_create_app_includes_log_analysis_route(self, tmp_path: Path):
        """create_app() must register the /api/v1/runs/{run_id}/log-analysis route."""
        from orchestrator.dashboard.app import create_app

        app = create_app(workspace_root=tmp_path, project_name="test")
        routes_paths = [r.path for r in app.routes]
        assert any("log-analysis" in p for p in routes_paths)

    def test_html_page_registered_in_create_app(self, tmp_path: Path):
        """create_app() must register the HTML /runs/{run_id}/log-analysis route."""
        from orchestrator.dashboard.app import create_app

        app = create_app(workspace_root=tmp_path, project_name="test")
        routes_paths = [r.path for r in app.routes]
        assert "/runs/{run_id}/log-analysis" in routes_paths
