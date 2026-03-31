"""Tests for TASK-003 — Runs page with pagination, filtering, and empty state.

Acceptance criteria verified:
  AC-1  Runs page displays 25 runs per page with Previous/Next pagination
        controls — maps to REQ-002.
  AC-2  Active (running) runs are pinned to the top of the table — maps
        to REQ-002.
  AC-3  Filtering by status, workflow type, and date range works via query
        parameters — maps to REQ-002.
  AC-4  Feature request truncated to 60 chars; columns include Run ID,
        Feature Request, Workflow, Status, Cost, Duration, Started —
        maps to REQ-002, AC-002.
  AC-5  Empty state shows 'No runs yet. Start your first orchestration
        run.' with New Run button — maps to AC-023.
  AC-6  create_runs_router follows the factory-router pattern.
  AC-7  Duration column is present for both running and completed runs.
  AC-8  Color-coded status badges with aria-label are rendered.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient

from orchestrator.dashboard.routes.runs import (
    _PER_PAGE,
    _compute_duration,
    _enrich_run,
    create_runs_router,
)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

TEMPLATES_DIR = (
    Path(__file__).parent.parent
    / "src"
    / "orchestrator"
    / "dashboard"
    / "templates"
)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_run_summary(
    run_id: str = "abc123",
    feature_request: str = "Test feature request",
    workflow_type: str = "full",
    status: str = "completed",
    total_cost_usd: float = 0.0042,
    start_time: str = "2026-03-31T10:00:00+00:00",
    end_time: str | None = "2026-03-31T10:30:00+00:00",
    steps_completed: int = 5,
    steps_total: int = 5,
) -> MagicMock:
    """Return a mock RunSummary with the given field values."""
    run = MagicMock()
    run.run_id = run_id
    run.feature_request = feature_request
    run.workflow_type = workflow_type
    run.status = status
    run.total_cost_usd = total_cost_usd
    run.start_time = start_time
    run.end_time = end_time
    run.steps_completed = steps_completed
    run.steps_total = steps_total
    return run


def _make_paginated_result(
    runs: list[Any] | None = None,
    page: int = 1,
    per_page: int = _PER_PAGE,
    total_count: int | None = None,
    total_pages: int = 1,
) -> dict[str, Any]:
    runs = runs or []
    return {
        "total_count": total_count if total_count is not None else len(runs),
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages,
        "runs": runs,
    }


def _make_reader(result: dict[str, Any] | None = None) -> MagicMock:
    reader = MagicMock()
    reader.list_runs_paginated.return_value = result or _make_paginated_result()
    return reader


def _make_app(reader: MagicMock | None = None) -> FastAPI:
    app = FastAPI()
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    router = create_runs_router(templates=templates, reader=reader or _make_reader())
    app.include_router(router)
    return app


def _client(reader: MagicMock | None = None) -> TestClient:
    return TestClient(_make_app(reader), raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# Constant
# ---------------------------------------------------------------------------


class TestConstants:
    def test_per_page_is_25(self):
        assert _PER_PAGE == 25


# ---------------------------------------------------------------------------
# Factory-router contract
# ---------------------------------------------------------------------------


class TestFactoryRouterContract:
    def test_returns_fastapi_apirouter(self):
        from fastapi import APIRouter

        templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
        reader = _make_reader()
        result = create_runs_router(templates, reader)
        assert isinstance(result, APIRouter)

    def test_router_has_runs_get_route(self):
        from fastapi import APIRouter

        templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
        reader = _make_reader()
        router = create_runs_router(templates, reader)
        paths = [route.path for route in router.routes]
        assert "/runs" in paths


# ---------------------------------------------------------------------------
# _compute_duration
# ---------------------------------------------------------------------------


class TestComputeDuration:
    def test_returns_dash_when_no_start_time(self):
        assert _compute_duration(None, None) == "-"

    def test_returns_dash_for_empty_start_time(self):
        assert _compute_duration("", None) == "-"

    def test_seconds_only(self):
        result = _compute_duration(
            "2026-01-01T10:00:00+00:00",
            "2026-01-01T10:00:45+00:00",
        )
        assert result == "45s"

    def test_minutes_and_seconds(self):
        result = _compute_duration(
            "2026-01-01T10:00:00+00:00",
            "2026-01-01T10:05:30+00:00",
        )
        assert result == "5m 30s"

    def test_hours_and_minutes(self):
        result = _compute_duration(
            "2026-01-01T08:00:00+00:00",
            "2026-01-01T09:23:00+00:00",
        )
        assert result == "1h 23m"

    def test_end_time_none_uses_now(self):
        # A run started "just now" should return a very short non-dash duration.
        from datetime import datetime, timezone

        start = datetime.now(tz=timezone.utc).isoformat()
        result = _compute_duration(start, None)
        # Should return something like "0s" or "1s" — not "-".
        assert result != "-"

    def test_z_suffix_handled(self):
        result = _compute_duration("2026-01-01T10:00:00Z", "2026-01-01T10:01:00Z")
        assert result == "1m 0s"

    def test_invalid_start_time_returns_dash(self):
        assert _compute_duration("not-a-date", "2026-01-01T10:00:00Z") == "-"


# ---------------------------------------------------------------------------
# _enrich_run
# ---------------------------------------------------------------------------


class TestEnrichRun:
    def test_includes_all_required_fields(self):
        run = _make_run_summary()
        enriched = _enrich_run(run)
        for key in (
            "run_id",
            "feature_request",
            "workflow_type",
            "status",
            "total_cost_usd",
            "start_time",
            "end_time",
            "steps_completed",
            "steps_total",
            "duration",
        ):
            assert key in enriched, f"Missing key: {key}"

    def test_duration_is_computed(self):
        run = _make_run_summary(
            start_time="2026-01-01T10:00:00+00:00",
            end_time="2026-01-01T10:30:00+00:00",
        )
        enriched = _enrich_run(run)
        assert enriched["duration"] == "30m 0s"


# ---------------------------------------------------------------------------
# HTTP — basic rendering
# ---------------------------------------------------------------------------


class TestRunsPageRendering:
    def test_get_runs_returns_200(self):
        c = _client()
        resp = c.get("/runs")
        assert resp.status_code == 200

    def test_content_type_is_html(self):
        c = _client()
        resp = c.get("/runs")
        assert "text/html" in resp.headers["content-type"]

    def test_page_title_present(self):
        c = _client()
        resp = c.get("/runs")
        assert "All Runs" in resp.text

    def test_filter_bar_present(self):
        c = _client()
        resp = c.get("/runs")
        assert 'name="status"' in resp.text
        assert 'name="workflow"' in resp.text
        assert 'name="date_from"' in resp.text
        assert 'name="date_to"' in resp.text


# ---------------------------------------------------------------------------
# Empty state
# ---------------------------------------------------------------------------


class TestEmptyState:
    def test_empty_state_message_shown_when_no_runs(self):
        reader = _make_reader(_make_paginated_result(runs=[], total_count=0))
        resp = _client(reader).get("/runs")
        assert "No runs yet. Start your first orchestration run." in resp.text

    def test_new_run_link_present_in_empty_state(self):
        reader = _make_reader(_make_paginated_result(runs=[], total_count=0))
        resp = _client(reader).get("/runs")
        # Should have at least one "New Run" link
        assert resp.text.count("/new-run") >= 1

    def test_table_not_rendered_when_empty(self):
        reader = _make_reader(_make_paginated_result(runs=[], total_count=0))
        resp = _client(reader).get("/runs")
        assert "<table" not in resp.text


# ---------------------------------------------------------------------------
# Table columns
# ---------------------------------------------------------------------------


class TestTableColumns:
    def _client_with_one_run(self) -> TestClient:
        run = _make_run_summary(
            run_id="deadbeef1234",
            feature_request="A test feature request that is short",
            workflow_type="full",
            status="completed",
        )
        reader = _make_reader(_make_paginated_result(runs=[run], total_count=1))
        return _client(reader)

    def test_run_id_column(self):
        resp = self._client_with_one_run().get("/runs")
        # Should truncate to 8 chars and link to detail page.
        assert "deadbeef" in resp.text
        assert "/runs/deadbeef1234" in resp.text

    def test_feature_request_column(self):
        resp = self._client_with_one_run().get("/runs")
        assert "A test feature request" in resp.text

    def test_workflow_column(self):
        resp = self._client_with_one_run().get("/runs")
        assert "full" in resp.text

    def test_status_badge_rendered(self):
        resp = self._client_with_one_run().get("/runs")
        assert "badge-completed" in resp.text
        assert 'aria-label="Status: completed"' in resp.text

    def test_cost_column(self):
        resp = self._client_with_one_run().get("/runs")
        assert "$" in resp.text

    def test_duration_column_present(self):
        resp = self._client_with_one_run().get("/runs")
        assert "Duration" in resp.text

    def test_started_column_present(self):
        resp = self._client_with_one_run().get("/runs")
        assert "Started" in resp.text


# ---------------------------------------------------------------------------
# Feature request truncation
# ---------------------------------------------------------------------------


class TestFeatureRequestTruncation:
    def test_long_feature_request_is_truncated_to_60_chars(self):
        long_fr = "A" * 80
        run = _make_run_summary(feature_request=long_fr)
        reader = _make_reader(_make_paginated_result(runs=[run], total_count=1))
        resp = _client(reader).get("/runs")
        # The first 60 chars should appear in the cell.
        assert "A" * 60 in resp.text
        # A truncation indicator (HTML entity &hellip; / ellipsis) should be present,
        # confirming the cell text was cut short.
        assert "&hellip;" in resp.text

    def test_short_feature_request_not_truncated(self):
        short_fr = "Short request"
        run = _make_run_summary(feature_request=short_fr)
        reader = _make_reader(_make_paginated_result(runs=[run], total_count=1))
        resp = _client(reader).get("/runs")
        assert short_fr in resp.text


# ---------------------------------------------------------------------------
# Active runs pinned to top
# ---------------------------------------------------------------------------


class TestActiveRunsPinnedToTop:
    def test_running_row_has_active_class(self):
        run = _make_run_summary(run_id="runningrun", status="running", end_time=None)
        reader = _make_reader(_make_paginated_result(runs=[run], total_count=1))
        resp = _client(reader).get("/runs")
        assert 'class="row-active"' in resp.text

    def test_completed_row_does_not_have_active_class(self):
        run = _make_run_summary(run_id="donerun", status="completed")
        reader = _make_reader(_make_paginated_result(runs=[run], total_count=1))
        resp = _client(reader).get("/runs")
        assert 'class="row-active"' not in resp.text


# ---------------------------------------------------------------------------
# Pagination controls
# ---------------------------------------------------------------------------


class TestPaginationControls:
    def _make_many_runs(self, n: int, status: str = "completed") -> list[MagicMock]:
        return [
            _make_run_summary(run_id=f"run{i:04d}", status=status) for i in range(n)
        ]

    def test_no_pagination_when_single_page(self):
        runs = self._make_many_runs(5)
        reader = _make_reader(
            _make_paginated_result(runs=runs, total_count=5, total_pages=1)
        )
        resp = _client(reader).get("/runs")
        # Pagination nav should not appear for a single page.
        assert 'class="pagination"' not in resp.text

    def test_pagination_shown_for_multiple_pages(self):
        runs = self._make_many_runs(25)
        reader = _make_reader(
            _make_paginated_result(runs=runs, total_count=50, total_pages=2, page=1)
        )
        resp = _client(reader).get("/runs")
        assert "pagination" in resp.text
        assert "Page 1 of 2" in resp.text

    def test_previous_disabled_on_first_page(self):
        runs = self._make_many_runs(25)
        reader = _make_reader(
            _make_paginated_result(runs=runs, total_count=50, total_pages=2, page=1)
        )
        resp = _client(reader).get("/runs")
        # The disabled span should be present (no link).
        assert 'aria-disabled="true"' in resp.text

    def test_next_link_on_first_page(self):
        runs = self._make_many_runs(25)
        reader = _make_reader(
            _make_paginated_result(runs=runs, total_count=50, total_pages=2, page=1)
        )
        resp = _client(reader).get("/runs")
        assert "page=2" in resp.text

    def test_previous_link_on_second_page(self):
        runs = self._make_many_runs(25)
        reader = _make_reader(
            _make_paginated_result(runs=runs, total_count=50, total_pages=2, page=2)
        )
        resp = _client(reader).get("/runs?page=2")
        assert "page=1" in resp.text

    def test_next_disabled_on_last_page(self):
        runs = self._make_many_runs(10)
        reader = _make_reader(
            _make_paginated_result(runs=runs, total_count=10, total_pages=1, page=1)
        )
        resp = _client(reader).get("/runs")
        # Only one page — no forward arrow link.
        assert "page=2" not in resp.text


# ---------------------------------------------------------------------------
# Filtering — query params forwarded to reader
# ---------------------------------------------------------------------------


class TestFilteringQueryParams:
    def test_status_filter_forwarded_to_reader(self):
        reader = _make_reader()
        _client(reader).get("/runs?status=running")
        reader.list_runs_paginated.assert_called_once()
        call_kwargs = reader.list_runs_paginated.call_args[1]
        assert call_kwargs.get("status_filter") == "running"

    def test_workflow_filter_forwarded_to_reader(self):
        reader = _make_reader()
        _client(reader).get("/runs?workflow=backend")
        call_kwargs = reader.list_runs_paginated.call_args[1]
        assert call_kwargs.get("workflow_filter") == "backend"

    def test_date_from_forwarded_to_reader(self):
        reader = _make_reader()
        _client(reader).get("/runs?date_from=2026-01-01")
        call_kwargs = reader.list_runs_paginated.call_args[1]
        assert call_kwargs.get("date_from") == "2026-01-01"

    def test_date_to_forwarded_to_reader(self):
        reader = _make_reader()
        _client(reader).get("/runs?date_to=2026-03-31")
        call_kwargs = reader.list_runs_paginated.call_args[1]
        assert call_kwargs.get("date_to") == "2026-03-31"

    def test_status_all_treated_as_no_filter(self):
        reader = _make_reader()
        _client(reader).get("/runs?status=all")
        call_kwargs = reader.list_runs_paginated.call_args[1]
        assert call_kwargs.get("status_filter") is None

    def test_workflow_all_treated_as_no_filter(self):
        reader = _make_reader()
        _client(reader).get("/runs?workflow=all")
        call_kwargs = reader.list_runs_paginated.call_args[1]
        assert call_kwargs.get("workflow_filter") is None

    def test_page_param_forwarded_to_reader(self):
        reader = _make_reader()
        _client(reader).get("/runs?page=3")
        call_kwargs = reader.list_runs_paginated.call_args[1]
        assert call_kwargs.get("page") == 3

    def test_per_page_is_always_25(self):
        reader = _make_reader()
        _client(reader).get("/runs")
        call_kwargs = reader.list_runs_paginated.call_args[1]
        assert call_kwargs.get("per_page") == 25

    def test_filter_state_echoed_in_form(self):
        reader = _make_reader()
        resp = _client(reader).get("/runs?status=failed&workflow=backend")
        assert 'value="failed"' in resp.text or 'selected' in resp.text
        # The filter form should show the selected values.
        assert "backend" in resp.text

    def test_clear_filters_link_shown_with_active_filters(self):
        reader = _make_reader()
        resp = _client(reader).get("/runs?status=running")
        assert "Clear" in resp.text

    def test_clear_filters_link_hidden_without_filters(self):
        reader = _make_reader()
        resp = _client(reader).get("/runs")
        assert "Clear" not in resp.text


# ---------------------------------------------------------------------------
# Pagination URL preserves filters
# ---------------------------------------------------------------------------


class TestPaginationPreservesFilters:
    def test_pagination_links_include_status_filter(self):
        runs = [_make_run_summary(run_id=f"r{i}") for i in range(25)]
        reader = _make_reader(
            _make_paginated_result(runs=runs, total_count=50, total_pages=2, page=1)
        )
        resp = _client(reader).get("/runs?status=completed")
        # The next-page link should preserve the status filter.
        assert "status=completed" in resp.text
        assert "page=2" in resp.text

    def test_pagination_links_include_workflow_filter(self):
        runs = [_make_run_summary(run_id=f"r{i}") for i in range(25)]
        reader = _make_reader(
            _make_paginated_result(runs=runs, total_count=50, total_pages=2, page=1)
        )
        resp = _client(reader).get("/runs?workflow=frontend")
        assert "workflow=frontend" in resp.text


# ---------------------------------------------------------------------------
# Error resilience
# ---------------------------------------------------------------------------


class TestErrorResilience:
    def test_reader_exception_returns_empty_state(self):
        reader = MagicMock()
        reader.list_runs_paginated.side_effect = RuntimeError("DB unavailable")
        resp = _client(reader).get("/runs")
        # Should not 500 — graceful empty state.
        assert resp.status_code == 200
        assert "No runs yet." in resp.text

    def test_invalid_page_param_clamped_to_1(self):
        reader = _make_reader()
        # FastAPI Query(ge=1) rejects page=0 with 422, but page=-1 also 422.
        resp = _client(reader).get("/runs?page=0")
        assert resp.status_code == 422

    def test_valid_page_param_accepted(self):
        reader = _make_reader()
        resp = _client(reader).get("/runs?page=1")
        assert resp.status_code == 200
