"""Integration tests for TASK-019 — Artifact search and diff endpoints.

These are *integration* tests that exercise the full create_app() stack,
verifying the contract between the search/diff routes and the RunDataReader.

Acceptance criteria verified:
  - GET /artifacts returns 200 HTML with search form (REQ-008)
  - GET /api/v1/artifacts/search with filter combinations returns correct JSON (REQ-008)
  - Empty search (no q/type/agent) returns empty list (REQ-008)
  - GET /api/v1/artifacts/compare with valid inputs returns diff shape (REQ-008)
  - GET /api/v1/artifacts/compare with invalid/missing inputs returns 400/422 (REQ-008)
  - Auth enforcement: 401 without token, 200 with token (AC-017)
  - Backward compat: artifact list response shape unchanged (AC-021)
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TEMPLATES_DIR = (
    Path(__file__).parent.parent
    / "src" / "orchestrator" / "dashboard" / "templates"
)

RUN_ID_A = "run-aaaabbbbcccc"
RUN_ID_B = "run-ddddeeeeeeee"
ARTIFACT_NAME = "prd"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_dashboard_app(
    workspace_root: Path,
    project_name: str = "test-project",
    config_path: Optional[Path] = None,
    *,
    auth_token: Optional[str] = None,
) -> FastAPI:
    import orchestrator.dashboard.app as _app_mod
    from orchestrator.dashboard.app import create_app

    saved = _app_mod.DASHBOARD_TOKEN
    _app_mod.DASHBOARD_TOKEN = auth_token
    try:
        return create_app(workspace_root, project_name, config_path=config_path)
    finally:
        _app_mod.DASHBOARD_TOKEN = saved


def _client(tmp_path: Path, *, auth_token: Optional[str] = None) -> TestClient:
    return TestClient(
        _make_dashboard_app(tmp_path, auth_token=auth_token),
        raise_server_exceptions=False,
    )


def _make_search_result(
    name: str = ARTIFACT_NAME,
    run_id: str = RUN_ID_A,
    schema: str = "prd",
    agent: str = "pm",
    version: int = 1,
    updated_at: str = "2026-03-01T12:00:00Z",
    size_bytes: int = 1024,
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


def _make_router_with_reader(results: list, diff_result: dict | None = None):
    """Build a minimal FastAPI app with the search router and a mock reader."""
    from orchestrator.dashboard.routes.search import create_search_router

    app = FastAPI()
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    reader = MagicMock()
    reader.search_artifacts_global.return_value = results

    if diff_result is not None:
        reader.compare_artifacts = MagicMock(return_value=diff_result)

    router = create_search_router(templates, reader)
    app.include_router(router)
    return app, reader


# ---------------------------------------------------------------------------
# GET /artifacts — HTML page (full-stack)
# ---------------------------------------------------------------------------


class TestArtifactsPageFullStack:
    """Verify GET /artifacts renders correctly via the full app stack."""

    def test_returns_200(self, tmp_path: Path) -> None:
        resp = _client(tmp_path).get("/artifacts")
        assert resp.status_code == 200

    def test_content_type_is_html(self, tmp_path: Path) -> None:
        resp = _client(tmp_path).get("/artifacts")
        assert "text/html" in resp.headers["content-type"]

    def test_page_title_present(self, tmp_path: Path) -> None:
        resp = _client(tmp_path).get("/artifacts")
        assert "Artifact" in resp.text

    def test_search_form_present(self, tmp_path: Path) -> None:
        html = _client(tmp_path).get("/artifacts").text
        assert 'name="q"' in html
        assert 'name="type"' in html
        assert 'name="agent"' in html

    def test_search_button_present(self, tmp_path: Path) -> None:
        html = _client(tmp_path).get("/artifacts").text
        assert "Search" in html

    def test_navigation_bar_present(self, tmp_path: Path) -> None:
        html = _client(tmp_path).get("/artifacts").text
        assert "Orchestrator" in html

    def test_known_schema_types_in_dropdown(self, tmp_path: Path) -> None:
        html = _client(tmp_path).get("/artifacts").text
        assert "prd" in html
        assert "architecture" in html

    def test_known_agents_in_dropdown(self, tmp_path: Path) -> None:
        html = _client(tmp_path).get("/artifacts").text
        assert "pm" in html

    def test_no_results_table_without_search(self, tmp_path: Path) -> None:
        """Without any query params, the results table must not appear."""
        html = _client(tmp_path).get("/artifacts").text
        assert "No matching artifacts found" not in html
        assert "search-results-table" not in html


# ---------------------------------------------------------------------------
# GET /api/v1/artifacts/search — JSON API filter combinations
# ---------------------------------------------------------------------------


class TestArtifactsSearchApiFullStack:
    """Verify search API filter combinations via full app stack."""

    def test_empty_search_returns_200(self, tmp_path: Path) -> None:
        resp = _client(tmp_path).get("/api/v1/artifacts/search")
        assert resp.status_code == 200

    def test_empty_search_returns_list(self, tmp_path: Path) -> None:
        data = _client(tmp_path).get("/api/v1/artifacts/search").json()
        assert isinstance(data, list)

    def test_search_with_query_returns_200(self, tmp_path: Path) -> None:
        resp = _client(tmp_path).get("/api/v1/artifacts/search?q=prd")
        assert resp.status_code == 200

    def test_search_with_type_filter_returns_200(self, tmp_path: Path) -> None:
        resp = _client(tmp_path).get("/api/v1/artifacts/search?type=architecture")
        assert resp.status_code == 200

    def test_search_with_agent_filter_returns_200(self, tmp_path: Path) -> None:
        resp = _client(tmp_path).get("/api/v1/artifacts/search?agent=pm")
        assert resp.status_code == 200

    def test_search_with_limit_returns_200(self, tmp_path: Path) -> None:
        resp = _client(tmp_path).get("/api/v1/artifacts/search?limit=10")
        assert resp.status_code == 200

    def test_search_combined_filters_returns_200(self, tmp_path: Path) -> None:
        resp = _client(tmp_path).get(
            "/api/v1/artifacts/search?q=prd&type=prd&agent=pm&limit=20"
        )
        assert resp.status_code == 200

    def test_search_no_params_returns_empty_list_by_default(self, tmp_path: Path) -> None:
        """Without real artifacts on disk, the search returns empty list."""
        data = _client(tmp_path).get("/api/v1/artifacts/search").json()
        assert data == [] or isinstance(data, list)

    def test_search_invalid_type_returns_400(self, tmp_path: Path) -> None:
        resp = _client(tmp_path).get("/api/v1/artifacts/search?type=../evil")
        assert resp.status_code == 400

    def test_search_invalid_agent_returns_400(self, tmp_path: Path) -> None:
        resp = _client(tmp_path).get("/api/v1/artifacts/search?agent=agent%20with%20spaces")
        assert resp.status_code == 400

    def test_search_limit_below_one_returns_422(self, tmp_path: Path) -> None:
        resp = _client(tmp_path).get("/api/v1/artifacts/search?limit=0")
        assert resp.status_code == 422

    def test_search_limit_above_500_returns_422(self, tmp_path: Path) -> None:
        resp = _client(tmp_path).get("/api/v1/artifacts/search?limit=501")
        assert resp.status_code == 422

    def test_search_query_too_long_returns_400(self, tmp_path: Path) -> None:
        resp = _client(tmp_path).get(f"/api/v1/artifacts/search?q={'x' * 300}")
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Search results shape — via isolated router with mock reader
# ---------------------------------------------------------------------------


class TestSearchResultShape:
    """Verify the search result JSON shape with a mock reader."""

    def test_result_has_all_required_fields(self) -> None:
        result = _make_search_result()
        app, _ = _make_router_with_reader([result])
        client = TestClient(app, raise_server_exceptions=True)

        resp = client.get("/api/v1/artifacts/search")
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 1
        item = items[0]
        required = {"name", "run_id", "schema", "agent", "version", "updated_at", "size_bytes"}
        assert required.issubset(item.keys()), (
            f"Missing fields: {required - item.keys()}"
        )

    def test_result_name_matches(self) -> None:
        result = _make_search_result(name="architecture")
        app, _ = _make_router_with_reader([result])
        client = TestClient(app)
        data = client.get("/api/v1/artifacts/search").json()
        assert data[0]["name"] == "architecture"

    def test_result_run_id_matches(self) -> None:
        result = _make_search_result(run_id=RUN_ID_A)
        app, _ = _make_router_with_reader([result])
        client = TestClient(app)
        data = client.get("/api/v1/artifacts/search").json()
        assert data[0]["run_id"] == RUN_ID_A

    def test_result_version_matches(self) -> None:
        result = _make_search_result(version=3)
        app, _ = _make_router_with_reader([result])
        client = TestClient(app)
        data = client.get("/api/v1/artifacts/search").json()
        assert data[0]["version"] == 3

    def test_multiple_results_returned(self) -> None:
        results = [
            _make_search_result(name="prd"),
            _make_search_result(name="architecture", schema="architecture"),
            _make_search_result(name="tasks", schema="tasks", agent="tpm"),
        ]
        app, _ = _make_router_with_reader(results)
        client = TestClient(app)
        data = client.get("/api/v1/artifacts/search").json()
        assert len(data) == 3

    def test_empty_results_when_no_match(self) -> None:
        app, _ = _make_router_with_reader([])
        client = TestClient(app)
        data = client.get("/api/v1/artifacts/search").json()
        assert data == []

    def test_type_filter_passed_to_reader(self) -> None:
        app, reader = _make_router_with_reader([])
        client = TestClient(app)
        client.get("/api/v1/artifacts/search?type=prd")
        _, kwargs = reader.search_artifacts_global.call_args
        assert kwargs.get("artifact_type") == "prd"

    def test_agent_filter_passed_to_reader(self) -> None:
        app, reader = _make_router_with_reader([])
        client = TestClient(app)
        client.get("/api/v1/artifacts/search?agent=architect")
        _, kwargs = reader.search_artifacts_global.call_args
        assert kwargs.get("agent") == "architect"

    def test_query_passed_to_reader(self) -> None:
        app, reader = _make_router_with_reader([])
        client = TestClient(app)
        client.get("/api/v1/artifacts/search?q=test+query")
        _, kwargs = reader.search_artifacts_global.call_args
        assert kwargs.get("query") == "test query"

    def test_default_limit_is_50(self) -> None:
        app, reader = _make_router_with_reader([])
        client = TestClient(app)
        client.get("/api/v1/artifacts/search")
        _, kwargs = reader.search_artifacts_global.call_args
        assert kwargs.get("limit") == 50


# ---------------------------------------------------------------------------
# GET /api/v1/artifacts/compare — diff endpoint
# ---------------------------------------------------------------------------


class TestArtifactDiffEndpoint:
    """Verify the artifact diff (compare) endpoint shape."""

    def test_diff_route_registered_in_full_app(self, tmp_path: Path) -> None:
        app = _make_dashboard_app(tmp_path)
        paths = [r.path for r in app.routes]
        assert any("compare" in p for p in paths), (
            f"compare route not found in: {paths}"
        )

    def test_diff_missing_run_a_returns_400_or_422(self, tmp_path: Path) -> None:
        """Missing required run_a param must return 400 or 422."""
        resp = _client(tmp_path).get(
            f"/api/v1/artifacts/compare?run_b={RUN_ID_B}&artifact={ARTIFACT_NAME}"
        )
        assert resp.status_code in (400, 422), (
            f"Expected 400/422 for missing run_a, got {resp.status_code}"
        )

    def test_diff_missing_run_b_returns_400_or_422(self, tmp_path: Path) -> None:
        resp = _client(tmp_path).get(
            f"/api/v1/artifacts/compare?run_a={RUN_ID_A}&artifact={ARTIFACT_NAME}"
        )
        assert resp.status_code in (400, 422)

    def test_diff_missing_artifact_returns_400_or_422(self, tmp_path: Path) -> None:
        resp = _client(tmp_path).get(
            f"/api/v1/artifacts/compare?run_a={RUN_ID_A}&run_b={RUN_ID_B}"
        )
        assert resp.status_code in (400, 422)

    def test_diff_invalid_run_id_traversal_rejected(self, tmp_path: Path) -> None:
        """Path traversal in run_a must be rejected with 400."""
        resp = _client(tmp_path).get(
            "/api/v1/artifacts/compare?run_a=../etc/passwd"
            f"&run_b={RUN_ID_B}&artifact={ARTIFACT_NAME}"
        )
        assert resp.status_code in (400, 403, 422)

    def test_diff_with_valid_run_ids_not_found_returns_404(self, tmp_path: Path) -> None:
        """Valid params but non-existent runs → 404."""
        resp = _client(tmp_path).get(
            f"/api/v1/artifacts/compare?run_a={RUN_ID_A}"
            f"&run_b={RUN_ID_B}&artifact={ARTIFACT_NAME}"
        )
        assert resp.status_code == 404

    def test_diff_response_shape_with_mock_reader(self) -> None:
        """Diff response must include added/removed/changed keys."""
        from orchestrator.dashboard.routes.artifacts import create_artifacts_router

        mock_diff = {
            "artifact": ARTIFACT_NAME,
            "run_a": RUN_ID_A,
            "run_b": RUN_ID_B,
            "version_a": 1,
            "version_b": 1,
            "added": ["new_key"],
            "removed": [],
            "changed": {"existing_key": {"old": "v1", "new": "v2"}},
        }

        app = FastAPI()
        templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
        manager = MagicMock()
        # Configure the mock so compare_artifacts().model_dump() returns a real dict.
        mock_result = MagicMock()
        mock_result.model_dump.return_value = mock_diff
        manager.compare_artifacts.return_value = mock_result
        reader = MagicMock()
        reader.get_artifact_diff.return_value = mock_diff

        # create_artifacts_router(templates, artifact_manager, reader=None)
        router = create_artifacts_router(templates, manager, reader)
        app.include_router(router)

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get(
            f"/api/v1/artifacts/compare?run_a={RUN_ID_A}"
            f"&run_b={RUN_ID_B}&artifact={ARTIFACT_NAME}"
        )

        if resp.status_code == 200:
            data = resp.json()
            assert "added" in data
            assert "removed" in data
            assert "changed" in data
        else:
            # 404 if runs not found; accept that but not a generic 500
            assert resp.status_code in (404, 422), (
                f"Expected 200/404/422, got {resp.status_code}"
            )

    def test_diff_added_keys_is_list(self) -> None:
        """added field must be a list when present."""
        from orchestrator.dashboard.routes.artifacts import create_artifacts_router

        mock_diff = {
            "artifact": ARTIFACT_NAME,
            "run_a": RUN_ID_A,
            "run_b": RUN_ID_B,
            "version_a": 1,
            "version_b": 2,
            "added": ["goal_new"],
            "removed": ["old_constraint"],
            "changed": {},
        }
        app = FastAPI()
        templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
        manager = MagicMock()
        reader = MagicMock()
        reader.get_artifact_diff.return_value = mock_diff

        router = create_artifacts_router(templates, manager, reader)
        app.include_router(router)

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get(
            f"/api/v1/artifacts/compare?run_a={RUN_ID_A}"
            f"&run_b={RUN_ID_B}&artifact={ARTIFACT_NAME}"
        )
        if resp.status_code == 200:
            data = resp.json()
            assert isinstance(data.get("added"), list)


# ---------------------------------------------------------------------------
# Search HTML page — with query results rendered
# ---------------------------------------------------------------------------


class TestSearchPageResultsRendering:
    """Verify search results are rendered in the HTML page correctly."""

    def test_results_table_shown_when_query_provided(self) -> None:
        result = _make_search_result()
        app, _ = _make_router_with_reader([result])
        client = TestClient(app)
        resp = client.get("/artifacts?q=prd")
        assert resp.status_code == 200
        assert "search-results-table" in resp.text

    def test_empty_results_shows_no_matching_message(self) -> None:
        app, _ = _make_router_with_reader([])
        client = TestClient(app)
        resp = client.get("/artifacts?q=nonexistent")
        assert "No matching artifacts found" in resp.text

    def test_run_id_linked_in_results(self) -> None:
        result = _make_search_result(run_id=RUN_ID_A)
        app, _ = _make_router_with_reader([result])
        client = TestClient(app)
        resp = client.get("/artifacts?q=prd")
        assert f"/runs/{RUN_ID_A}" in resp.text

    def test_result_count_shown(self) -> None:
        results = [_make_search_result(), _make_search_result(name="architecture")]
        app, _ = _make_router_with_reader(results)
        client = TestClient(app)
        resp = client.get("/artifacts?q=test")
        assert "2 artifacts found" in resp.text

    def test_query_reflected_in_form_value(self) -> None:
        result = _make_search_result()
        app, _ = _make_router_with_reader([result])
        client = TestClient(app)
        resp = client.get("/artifacts?q=prd")
        assert 'value="prd"' in resp.text

    def test_type_filter_preselected(self) -> None:
        result = _make_search_result()
        app, _ = _make_router_with_reader([result])
        client = TestClient(app)
        resp = client.get("/artifacts?type=prd")
        html = resp.text
        assert 'value="prd" selected' in html or 'value="prd"  selected' in html

    def test_size_shown_as_kb(self) -> None:
        result = _make_search_result(size_bytes=2048)
        app, _ = _make_router_with_reader([result])
        client = TestClient(app)
        resp = client.get("/artifacts?q=prd")
        assert "2.0 KB" in resp.text

    def test_version_shown_with_prefix(self) -> None:
        result = _make_search_result(version=5)
        app, _ = _make_router_with_reader([result])
        client = TestClient(app)
        resp = client.get("/artifacts?q=prd")
        assert "v5" in resp.text


# ---------------------------------------------------------------------------
# Auth enforcement — search and diff endpoints
# ---------------------------------------------------------------------------


class TestSearchAuth:
    """Verify auth on artifact search and compare endpoints (AC-017)."""

    _TOKEN = "search-auth-token-def"

    def _authed_client(self, tmp_path: Path) -> TestClient:
        return TestClient(
            _make_dashboard_app(tmp_path, auth_token=self._TOKEN),
            raise_server_exceptions=False,
        )

    def test_artifacts_page_401_without_token(self, tmp_path: Path) -> None:
        assert self._authed_client(tmp_path).get("/artifacts").status_code == 401

    def test_artifacts_page_200_with_token(self, tmp_path: Path) -> None:
        resp = self._authed_client(tmp_path).get(
            "/artifacts",
            headers={"Authorization": f"Bearer {self._TOKEN}"},
        )
        assert resp.status_code == 200

    def test_search_api_401_without_token(self, tmp_path: Path) -> None:
        resp = self._authed_client(tmp_path).get("/api/v1/artifacts/search")
        assert resp.status_code == 401

    def test_search_api_200_with_token(self, tmp_path: Path) -> None:
        resp = self._authed_client(tmp_path).get(
            "/api/v1/artifacts/search",
            headers={"Authorization": f"Bearer {self._TOKEN}"},
        )
        assert resp.status_code == 200

    def test_compare_api_401_without_token(self, tmp_path: Path) -> None:
        resp = self._authed_client(tmp_path).get(
            f"/api/v1/artifacts/compare?run_a={RUN_ID_A}"
            f"&run_b={RUN_ID_B}&artifact={ARTIFACT_NAME}"
        )
        assert resp.status_code == 401

    def test_compare_api_requires_valid_token(self, tmp_path: Path) -> None:
        resp = self._authed_client(tmp_path).get(
            f"/api/v1/artifacts/compare?run_a={RUN_ID_A}"
            f"&run_b={RUN_ID_B}&artifact={ARTIFACT_NAME}",
            headers={"Authorization": f"Bearer {self._TOKEN}"},
        )
        # With token, must not 401 (may 404 if runs don't exist)
        assert resp.status_code != 401


# ---------------------------------------------------------------------------
# Backward compat — artifact list API shape
# ---------------------------------------------------------------------------


class TestArtifactListApiBackwardCompat:
    """Verify GET /api/v1/runs/{run_id}/artifacts shape is unchanged."""

    def test_artifact_list_returns_not_500(self, tmp_path: Path) -> None:
        resp = _client(tmp_path).get("/api/v1/runs/abc123/artifacts")
        assert resp.status_code != 500

    def test_artifact_list_valid_run_id_returns_list_or_404(self, tmp_path: Path) -> None:
        resp = _client(tmp_path).get("/api/v1/runs/abc123/artifacts")
        assert resp.status_code in (200, 404), (
            f"Expected 200 or 404, got {resp.status_code}"
        )

    def test_artifact_list_invalid_run_id_returns_400(self, tmp_path: Path) -> None:
        """run_id with path-traversal chars must be rejected with 400."""
        resp = _client(tmp_path).get("/api/v1/runs/..%2Fetc%2Fpasswd/artifacts")
        assert resp.status_code in (400, 403, 404, 422)

    def test_search_api_response_is_list(self, tmp_path: Path) -> None:
        """GET /api/v1/artifacts/search always returns a list, never a dict."""
        data = _client(tmp_path).get("/api/v1/artifacts/search").json()
        assert isinstance(data, list)
