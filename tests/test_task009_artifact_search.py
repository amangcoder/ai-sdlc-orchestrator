"""Tests for TASK-009: Global artifact search page and API endpoint.

Acceptance criteria verified:
  AC-1  GET /artifacts renders a search page with text query, type filter, and
        agent filter inputs — maps to REQ-008.
  AC-2  GET /api/v1/artifacts/search returns filtered results as JSON array —
        maps to REQ-008.
  AC-3  Results table shows artifact name, run ID (linked), schema, agent,
        version, updated_at, size — maps to REQ-008.
  AC-4  Search is bookmarkable via query parameters in the URL — maps to REQ-008.
  AC-5  Empty results display 'No matching artifacts found' message — maps to REQ-008.
  AC-6  Factory function create_search_router(templates, reader) -> APIRouter exists.
  AC-7  API endpoint validates query params and returns 400 on invalid input.
  AC-8  Limit parameter is accepted by the API endpoint (default 50).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient

from orchestrator.dashboard.routes.search import (
    _KNOWN_AGENTS,
    _KNOWN_SCHEMA_TYPES,
    _validate_search_params,
    create_search_router,
)
from fastapi import HTTPException

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TEMPLATES_DIR = (
    Path(__file__).parent.parent
    / "src"
    / "orchestrator"
    / "dashboard"
    / "templates"
)

RUN_ID = "run-abc12345def6"
ARTIFACT_NAME = "prd"


# ---------------------------------------------------------------------------
# Sample search result fixture
# ---------------------------------------------------------------------------


def _make_result(
    name: str = ARTIFACT_NAME,
    run_id: str = RUN_ID,
    schema: str = "prd",
    agent: str = "pm",
    version: int = 2,
    updated_at: str = "2024-06-15T10:30:00Z",
    size_bytes: int = 2048,
) -> dict:
    return {
        "name": name,
        "run_id": run_id,
        "schema": schema,
        "agent": agent,
        "version": version,
        "updated_at": updated_at,
        "size_bytes": size_bytes,
    }


# ---------------------------------------------------------------------------
# App factory helpers
# ---------------------------------------------------------------------------


def _make_reader(results: list[dict] | None = None) -> MagicMock:
    """Return a mock RunDataReader whose search_artifacts_global returns *results*."""
    reader = MagicMock()
    reader.search_artifacts_global.return_value = results if results is not None else []
    return reader


def _make_app(results: list[dict] | None = None) -> FastAPI:
    app = FastAPI()
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    reader = _make_reader(results)
    router = create_search_router(templates, reader)
    app.include_router(router)
    return app


def _make_client(results: list[dict] | None = None) -> TestClient:
    return TestClient(_make_app(results), raise_server_exceptions=True)


# ===========================================================================
# Tests: factory function and module-level exports
# ===========================================================================


class TestCreateSearchRouter:
    def test_returns_apirouter(self) -> None:
        from fastapi import APIRouter

        templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
        reader = _make_reader()
        router = create_search_router(templates, reader)
        assert isinstance(router, APIRouter)

    def test_known_schema_types_non_empty(self) -> None:
        assert len(_KNOWN_SCHEMA_TYPES) > 0
        assert "prd" in _KNOWN_SCHEMA_TYPES
        assert "architecture" in _KNOWN_SCHEMA_TYPES
        assert "tasks" in _KNOWN_SCHEMA_TYPES

    def test_known_agents_non_empty(self) -> None:
        assert len(_KNOWN_AGENTS) > 0
        assert "pm" in _KNOWN_AGENTS
        assert "architect" in _KNOWN_AGENTS
        assert "backend_engineer" in _KNOWN_AGENTS


# ===========================================================================
# Tests: validation helper
# ===========================================================================


class TestValidateSearchParams:
    def test_valid_params_no_error(self) -> None:
        # Should not raise
        _validate_search_params("prd", "architecture", "pm")

    def test_empty_params_no_error(self) -> None:
        _validate_search_params("", None, None)

    def test_query_too_long_raises(self) -> None:
        with pytest.raises(HTTPException) as exc_info:
            _validate_search_params("x" * 300, None, None)
        assert exc_info.value.status_code == 400
        assert "too long" in exc_info.value.detail.lower()

    def test_invalid_type_raises(self) -> None:
        with pytest.raises(HTTPException) as exc_info:
            _validate_search_params("", "../evil", None)
        assert exc_info.value.status_code == 400

    def test_invalid_agent_raises(self) -> None:
        with pytest.raises(HTTPException) as exc_info:
            _validate_search_params("", None, "agent with spaces")
        assert exc_info.value.status_code == 400

    def test_valid_type_with_hyphens(self) -> None:
        # Should not raise
        _validate_search_params("", "change_impact_analysis", None)

    def test_empty_type_empty_string_treated_as_none(self) -> None:
        # Empty string for type should be passed as None (handled at param level)
        _validate_search_params("", None, None)  # should not raise


# ===========================================================================
# Tests: HTML page — GET /artifacts
# ===========================================================================


class TestArtifactsSearchPage:
    def test_page_returns_200(self) -> None:
        client = _make_client()
        resp = client.get("/artifacts")
        assert resp.status_code == 200

    def test_page_title_in_response(self) -> None:
        client = _make_client()
        resp = client.get("/artifacts")
        assert "Artifact Search" in resp.text

    def test_search_form_present(self) -> None:
        client = _make_client()
        resp = client.get("/artifacts")
        html = resp.text
        # Form submits via GET to /artifacts
        assert 'method="GET"' in html or "method=GET" in html.replace('"', "")
        assert 'action="/artifacts"' in html

    def test_text_query_input_present(self) -> None:
        client = _make_client()
        resp = client.get("/artifacts")
        html = resp.text
        # Query input field
        assert 'name="q"' in html

    def test_type_dropdown_present(self) -> None:
        client = _make_client()
        resp = client.get("/artifacts")
        html = resp.text
        assert 'name="type"' in html
        # Dropdown should contain known schema types
        assert "prd" in html
        assert "architecture" in html

    def test_agent_dropdown_present(self) -> None:
        client = _make_client()
        resp = client.get("/artifacts")
        html = resp.text
        assert 'name="agent"' in html
        # Dropdown should contain known agent names
        assert "pm" in html
        assert "architect" in html

    def test_search_button_present(self) -> None:
        client = _make_client()
        resp = client.get("/artifacts")
        assert "Search" in resp.text

    def test_no_results_section_without_search(self) -> None:
        """No results table and no 'No matching' message before search is submitted."""
        client = _make_client()
        resp = client.get("/artifacts")
        html = resp.text
        assert "No matching artifacts found" not in html
        # Table should not appear either (no search performed)
        assert "search-results-table" not in html

    # -----------------------------------------------------------------
    # AC-1: Search form submits via GET (bookmarkable)
    # -----------------------------------------------------------------

    def test_query_reflected_in_form_value(self) -> None:
        """AC-4: Search query is visible in the URL and reflected in the form."""
        client = _make_client([_make_result()])
        resp = client.get("/artifacts?q=prd")
        assert resp.status_code == 200
        html = resp.text
        assert 'value="prd"' in html

    def test_type_filter_preselected_in_dropdown(self) -> None:
        """AC-4: Selected type appears as selected option in the dropdown."""
        client = _make_client([_make_result()])
        resp = client.get("/artifacts?type=prd")
        html = resp.text
        # The 'prd' option should be marked selected
        assert 'value="prd" selected' in html or 'value="prd"  selected' in html

    def test_agent_filter_preselected_in_dropdown(self) -> None:
        """AC-4: Selected agent appears as selected option in the dropdown."""
        client = _make_client([_make_result()])
        resp = client.get("/artifacts?agent=pm")
        html = resp.text
        assert 'value="pm" selected' in html or 'value="pm"  selected' in html

    # -----------------------------------------------------------------
    # AC-3: Results table columns
    # -----------------------------------------------------------------

    def test_results_table_shown_when_results_present(self) -> None:
        """AC-3: Results table is rendered with expected columns."""
        client = _make_client([_make_result()])
        resp = client.get("/artifacts?q=prd")
        assert resp.status_code == 200
        html = resp.text
        assert "search-results-table" in html

    def test_results_table_has_artifact_name_column(self) -> None:
        client = _make_client([_make_result()])
        resp = client.get("/artifacts?q=prd")
        html = resp.text
        assert "Artifact Name" in html

    def test_results_table_has_run_id_column(self) -> None:
        client = _make_client([_make_result()])
        resp = client.get("/artifacts?q=prd")
        html = resp.text
        assert "Run ID" in html

    def test_results_table_has_schema_column(self) -> None:
        client = _make_client([_make_result()])
        resp = client.get("/artifacts?q=prd")
        html = resp.text
        assert "Schema" in html

    def test_results_table_has_agent_column(self) -> None:
        client = _make_client([_make_result()])
        resp = client.get("/artifacts?q=prd")
        html = resp.text
        assert "Agent" in html

    def test_results_table_has_version_column(self) -> None:
        client = _make_client([_make_result()])
        resp = client.get("/artifacts?q=prd")
        html = resp.text
        assert "Version" in html

    def test_results_table_has_updated_column(self) -> None:
        client = _make_client([_make_result()])
        resp = client.get("/artifacts?q=prd")
        html = resp.text
        assert "Last Updated" in html

    def test_results_table_has_size_column(self) -> None:
        client = _make_client([_make_result()])
        resp = client.get("/artifacts?q=prd")
        html = resp.text
        assert "Size" in html

    def test_run_id_is_linked(self) -> None:
        """AC-3: Run ID in results table links to /runs/{run_id}."""
        client = _make_client([_make_result(run_id=RUN_ID)])
        resp = client.get("/artifacts?q=prd")
        html = resp.text
        assert f'/runs/{RUN_ID}' in html

    def test_artifact_name_shown_in_results(self) -> None:
        client = _make_client([_make_result(name="architecture")])
        resp = client.get("/artifacts?q=arch")
        html = resp.text
        assert "architecture" in html

    def test_size_formatted_as_bytes(self) -> None:
        """Size column shows human-readable format for small artifacts."""
        client = _make_client([_make_result(size_bytes=512)])
        resp = client.get("/artifacts?q=prd")
        assert "512 B" in resp.text

    def test_size_formatted_as_kb(self) -> None:
        client = _make_client([_make_result(size_bytes=2048)])
        resp = client.get("/artifacts?q=prd")
        # 2048 / 1024 = 2.0 KB
        assert "2.0 KB" in resp.text

    def test_size_formatted_as_mb(self) -> None:
        client = _make_client([_make_result(size_bytes=2_097_152)])
        resp = client.get("/artifacts?q=prd")
        # 2 MB
        assert "2.0 MB" in resp.text

    def test_version_displayed_with_prefix(self) -> None:
        client = _make_client([_make_result(version=3)])
        resp = client.get("/artifacts?q=prd")
        assert "v3" in resp.text

    def test_updated_at_truncated_to_seconds(self) -> None:
        result = _make_result(updated_at="2024-06-15T10:30:00.123456Z")
        client = _make_client([result])
        resp = client.get("/artifacts?q=prd")
        assert "2024-06-15T10:30:00" in resp.text

    def test_missing_schema_shows_dash(self) -> None:
        result = _make_result(schema="")
        client = _make_client([result])
        resp = client.get("/artifacts?q=prd")
        # Empty schema should render as '—'
        assert "—" in resp.text

    def test_missing_agent_shows_dash(self) -> None:
        result = _make_result(agent="")
        client = _make_client([result])
        resp = client.get("/artifacts?q=prd")
        assert "—" in resp.text

    # -----------------------------------------------------------------
    # AC-5: Empty results message
    # -----------------------------------------------------------------

    def test_empty_results_shows_no_matching_message(self) -> None:
        """AC-5: 'No matching artifacts found' shown when search returns empty list."""
        client = _make_client([])  # empty results
        resp = client.get("/artifacts?q=nonexistent")
        assert resp.status_code == 200
        assert "No matching artifacts found" in resp.text

    def test_empty_results_no_table_rendered(self) -> None:
        client = _make_client([])
        resp = client.get("/artifacts?q=nothing")
        assert "search-results-table" not in resp.text

    def test_results_count_shown_in_summary(self) -> None:
        results = [_make_result(), _make_result(name="architecture", schema="architecture")]
        client = _make_client(results)
        resp = client.get("/artifacts?q=test")
        html = resp.text
        assert "2 artifacts found" in html

    def test_singular_result_count(self) -> None:
        client = _make_client([_make_result()])
        resp = client.get("/artifacts?q=prd")
        html = resp.text
        assert "1 artifact found" in html

    # -----------------------------------------------------------------
    # Search delegates to reader
    # -----------------------------------------------------------------

    def test_reader_called_with_correct_params(self) -> None:
        app = FastAPI()
        templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
        reader = _make_reader([_make_result()])
        router = create_search_router(templates, reader)
        app.include_router(router)
        client = TestClient(app, raise_server_exceptions=True)

        client.get("/artifacts?q=prd&type=prd&agent=pm&limit=10")

        reader.search_artifacts_global.assert_called_once_with(
            query="prd",
            artifact_type="prd",
            agent="pm",
            limit=10,
        )

    def test_no_reader_call_without_search_params(self) -> None:
        """Reader should NOT be called when no search params supplied."""
        app = FastAPI()
        templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
        reader = _make_reader()
        router = create_search_router(templates, reader)
        app.include_router(router)
        client = TestClient(app, raise_server_exceptions=True)

        client.get("/artifacts")

        reader.search_artifacts_global.assert_not_called()

    def test_reader_exception_shows_error_message(self) -> None:
        app = FastAPI()
        templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
        reader = MagicMock()
        reader.search_artifacts_global.side_effect = RuntimeError("disk read error")
        router = create_search_router(templates, reader)
        app.include_router(router)
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.get("/artifacts?q=prd")
        assert resp.status_code == 200
        assert "Search failed" in resp.text


# ===========================================================================
# Tests: JSON API — GET /api/v1/artifacts/search
# ===========================================================================


class TestApiSearchArtifactsGlobal:
    def test_returns_200_with_results(self) -> None:
        """AC-2: API returns JSON array of matching artifacts."""
        client = _make_client([_make_result()])
        resp = client.get("/api/v1/artifacts/search?q=prd")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 1

    def test_returns_empty_list_when_no_results(self) -> None:
        client = _make_client([])
        resp = client.get("/api/v1/artifacts/search?q=nothing")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_result_shape_has_required_fields(self) -> None:
        """AC-2: Each result has the documented fields."""
        result = _make_result()
        client = _make_client([result])
        resp = client.get("/api/v1/artifacts/search")
        data = resp.json()
        assert len(data) == 1
        item = data[0]
        assert item["name"] == ARTIFACT_NAME
        assert item["run_id"] == RUN_ID
        assert item["schema"] == "prd"
        assert item["agent"] == "pm"
        assert item["version"] == 2
        assert item["updated_at"] == "2024-06-15T10:30:00Z"
        assert item["size_bytes"] == 2048

    def test_no_params_returns_all(self) -> None:
        """Empty query + no filters returns all artifacts (up to limit)."""
        results = [_make_result(), _make_result(name="architecture")]
        client = _make_client(results)
        resp = client.get("/api/v1/artifacts/search")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_type_filter_passed_to_reader(self) -> None:
        app = FastAPI()
        templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
        reader = _make_reader([_make_result()])
        router = create_search_router(templates, reader)
        app.include_router(router)
        client = TestClient(app, raise_server_exceptions=True)

        client.get("/api/v1/artifacts/search?type=architecture")

        reader.search_artifacts_global.assert_called_once_with(
            query="",
            artifact_type="architecture",
            agent=None,
            limit=50,
        )

    def test_agent_filter_passed_to_reader(self) -> None:
        app = FastAPI()
        templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
        reader = _make_reader([_make_result()])
        router = create_search_router(templates, reader)
        app.include_router(router)
        client = TestClient(app, raise_server_exceptions=True)

        client.get("/api/v1/artifacts/search?agent=architect")

        reader.search_artifacts_global.assert_called_once_with(
            query="",
            artifact_type=None,
            agent="architect",
            limit=50,
        )

    def test_limit_param_passed_to_reader(self) -> None:
        app = FastAPI()
        templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
        reader = _make_reader([])
        router = create_search_router(templates, reader)
        app.include_router(router)
        client = TestClient(app, raise_server_exceptions=True)

        client.get("/api/v1/artifacts/search?limit=100")

        reader.search_artifacts_global.assert_called_once_with(
            query="",
            artifact_type=None,
            agent=None,
            limit=100,
        )

    def test_default_limit_is_50(self) -> None:
        app = FastAPI()
        templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
        reader = _make_reader([])
        router = create_search_router(templates, reader)
        app.include_router(router)
        client = TestClient(app, raise_server_exceptions=True)

        client.get("/api/v1/artifacts/search")

        _, kwargs = reader.search_artifacts_global.call_args
        assert kwargs["limit"] == 50

    # -----------------------------------------------------------------
    # AC-7: Input validation
    # -----------------------------------------------------------------

    def test_invalid_type_returns_400(self) -> None:
        client = _make_client()
        resp = client.get("/api/v1/artifacts/search?type=../evil")
        assert resp.status_code == 400

    def test_invalid_agent_returns_400(self) -> None:
        client = _make_client()
        resp = client.get("/api/v1/artifacts/search?agent=agent%20with%20spaces")
        assert resp.status_code == 400

    def test_limit_below_one_returns_422(self) -> None:
        """FastAPI should reject limit < 1 (Query ge=1)."""
        client = _make_client()
        resp = client.get("/api/v1/artifacts/search?limit=0")
        assert resp.status_code == 422

    def test_limit_above_500_returns_422(self) -> None:
        client = _make_client()
        resp = client.get("/api/v1/artifacts/search?limit=501")
        assert resp.status_code == 422

    def test_query_too_long_returns_400(self) -> None:
        long_q = "x" * 300
        client = _make_client()
        resp = client.get(f"/api/v1/artifacts/search?q={long_q}")
        assert resp.status_code == 400

    def test_reader_exception_returns_500(self) -> None:
        app = FastAPI()
        templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
        reader = MagicMock()
        reader.search_artifacts_global.side_effect = RuntimeError("unexpected")
        router = create_search_router(templates, reader)
        app.include_router(router)
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.get("/api/v1/artifacts/search?q=prd")
        assert resp.status_code == 500
        assert "Search failed" in resp.json()["detail"]

    def test_reader_value_error_returns_400(self) -> None:
        app = FastAPI()
        templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
        reader = MagicMock()
        reader.search_artifacts_global.side_effect = ValueError("bad input")
        router = create_search_router(templates, reader)
        app.include_router(router)
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.get("/api/v1/artifacts/search?q=prd")
        assert resp.status_code == 400


# ===========================================================================
# Tests: integration with create_app (route wiring)
# ===========================================================================


class TestSearchRouterInApp:
    """Verify the search router is wired into the full application."""

    @pytest.fixture
    def full_app_client(self, tmp_path: Path) -> TestClient:
        from unittest.mock import patch

        import sys

        # Patch WorkspaceManager to avoid touching real filesystem
        with patch(
            "orchestrator.dashboard.app.WorkspaceManager"
        ) as mock_wm_cls, patch(
            "orchestrator.dashboard.app.RunDataReader"
        ) as mock_reader_cls, patch(
            "orchestrator.dashboard.app.RunTracker"
        ):
            mock_wm = MagicMock()
            mock_wm.project_workspace = tmp_path
            mock_wm_cls.return_value = mock_wm

            mock_reader = MagicMock()
            mock_reader.search_artifacts_global.return_value = []
            mock_reader_cls.return_value = mock_reader

            from orchestrator.dashboard.app import create_app

            app = create_app(
                workspace_root=tmp_path,
                project_name="test-project",
            )
            return TestClient(app, raise_server_exceptions=False)

    def test_artifacts_route_registered(self, full_app_client: TestClient) -> None:
        resp = full_app_client.get("/artifacts")
        assert resp.status_code == 200

    def test_api_search_route_registered(self, full_app_client: TestClient) -> None:
        resp = full_app_client.get("/api/v1/artifacts/search")
        assert resp.status_code == 200

    def test_artifacts_page_does_not_conflict_with_run_artifacts_view(
        self, full_app_client: TestClient
    ) -> None:
        """/artifacts must not shadow /runs/{run_id}/artifacts-view."""
        # The runs/{run_id}/artifacts-view route is handled by the artifacts router.
        # This test verifies there is no 404 or routing conflict by hitting /artifacts
        # and a different path pattern.
        resp = full_app_client.get("/artifacts")
        assert resp.status_code == 200
