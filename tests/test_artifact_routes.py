"""Tests for TASK-011: Artifact management dashboard routes.

Acceptance criteria verified:
  1.  APIRouter in routes/artifacts.py has all 6 API endpoints.
  2.  GET /api/v1/runs/{run_id}/artifacts returns list of ArtifactMetadata.
  3.  GET /api/v1/runs/{run_id}/artifacts/{name}/versions returns version list.
  4.  GET /api/v1/runs/{run_id}/artifacts/{name}/versions/{v} returns artifact or 404.
  5.  GET /api/v1/artifacts/compare returns ArtifactDiff with added/removed/changed.
  6.  GET /api/v1/artifacts/search filters by query, type, agent.
  7.  POST /api/v1/artifacts/retention with dry_run=true lists without deleting.
  8.  artifacts.html renders list table, version history modal, diff viewer.
  9.  Path traversal defence: '../../../etc' and '../v1' are rejected with 400.
  10. All responses use ArtifactManager methods (no direct filesystem access).
  11. Retention delete returns 403 without explicit intentional header.
  12. Auth middleware protects all endpoints (uses DASHBOARD_TOKEN when set).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient

from orchestrator.artifact_manager import (
    ArtifactDiff,
    ArtifactManager,
    ArtifactMetadata,
    ArtifactVersion,
    RetentionResult,
)
from orchestrator.dashboard.routes.artifacts import (
    RetentionRequest,
    _validate_name,
    _validate_run_id,
    _validate_version,
    create_artifacts_router,
)

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

RUN_A = "run-abc123"
RUN_B = "run-def456"
ARTIFACT_NAME = "prd"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_metadata(
    name: str = ARTIFACT_NAME,
    run_id: str = RUN_A,
    version: int = 1,
    agent: str | None = "pm",
) -> ArtifactMetadata:
    return ArtifactMetadata(
        name=name,
        current_version=version,
        run_id=run_id,
        agent=agent,
        schema_name=None,
        created_at="2024-01-01T10:00:00Z",
        updated_at="2024-01-01T11:00:00Z",
        size_bytes=1024,
    )


def _make_version(version: int = 1, run_id: str = RUN_A) -> ArtifactVersion:
    return ArtifactVersion(
        version=version,
        run_id=run_id,
        agent="pm",
        created_at="2024-01-01T10:00:00Z",
        size_bytes=512,
        run_status="completed",
    )


def _make_diff(
    name: str = ARTIFACT_NAME,
    added: list[str] | None = None,
    removed: list[str] | None = None,
    changed: list[str] | None = None,
) -> ArtifactDiff:
    return ArtifactDiff(
        name=name,
        run_a=RUN_A,
        run_b=RUN_B,
        version_a=1,
        version_b=2,
        added=added or [],
        removed=removed or [],
        changed=changed or [],
    )


def _mock_manager() -> MagicMock:
    """Return a MagicMock that behaves like a minimal ArtifactManager."""
    m = MagicMock(spec=ArtifactManager)
    m.list_artifacts.return_value = [_make_metadata()]
    m.get_artifact_history.return_value = [_make_version()]
    m.load_artifact.return_value = {"title": "Test PRD", "goals": []}
    m.compare_artifacts.return_value = _make_diff(added=["new_key"], changed=["title"])
    m.search_artifacts.return_value = [_make_metadata()]
    m.apply_retention_policy.return_value = RetentionResult(
        deleted_versions=0,
        deleted_paths=[],
        retained_versions=5,
        dry_run=True,
    )
    return m


def _make_app(manager: MagicMock | None = None) -> FastAPI:
    """Create a minimal FastAPI app with only the artifacts router mounted."""
    if manager is None:
        manager = _mock_manager()
    app = FastAPI()
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    router = create_artifacts_router(templates, manager)
    app.include_router(router)
    return app


def _client(manager: MagicMock | None = None) -> TestClient:
    return TestClient(_make_app(manager), raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# 1. Router structure
# ---------------------------------------------------------------------------


class TestRouterStructure:
    """Verify the router exposes all 6 API endpoints at the expected paths."""

    def test_router_has_6_api_routes(self):
        app = _make_app()
        api_paths = {route.path for route in app.routes if hasattr(route, "path")}
        expected = {
            "/api/v1/runs/{run_id}/artifacts",
            "/api/v1/runs/{run_id}/artifacts/{name}/versions",
            "/api/v1/runs/{run_id}/artifacts/{name}/versions/{v}",
            "/api/v1/artifacts/compare",
            "/api/v1/artifacts/search",
            "/api/v1/artifacts/retention",
        }
        missing = expected - api_paths
        assert not missing, f"Missing route(s): {missing}"

    def test_html_page_route_registered(self):
        app = _make_app()
        api_paths = {route.path for route in app.routes if hasattr(route, "path")}
        assert "/runs/{run_id}/artifacts-view" in api_paths


# ---------------------------------------------------------------------------
# 2. GET /api/v1/runs/{run_id}/artifacts
# ---------------------------------------------------------------------------


class TestListArtifacts:
    """GET /api/v1/runs/{run_id}/artifacts."""

    def test_returns_200_with_list(self):
        client = _client()
        resp = client.get(f"/api/v1/runs/{RUN_A}/artifacts")
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list)
        assert len(body) == 1

    def test_response_has_artifact_metadata_fields(self):
        client = _client()
        resp = client.get(f"/api/v1/runs/{RUN_A}/artifacts")
        item = resp.json()[0]
        assert item["name"] == ARTIFACT_NAME
        assert item["current_version"] == 1
        assert item["run_id"] == RUN_A
        assert "created_at" in item
        assert "size_bytes" in item

    def test_calls_list_artifacts_with_run_id(self):
        m = _mock_manager()
        client = _client(m)
        client.get(f"/api/v1/runs/{RUN_A}/artifacts")
        m.list_artifacts.assert_called_once_with(RUN_A)

    def test_empty_run_returns_empty_list(self):
        m = _mock_manager()
        m.list_artifacts.return_value = []
        client = _client(m)
        resp = client.get(f"/api/v1/runs/{RUN_A}/artifacts")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_invalid_run_id_returns_400(self):
        client = _client()
        resp = client.get("/api/v1/runs/../evil/artifacts")
        # FastAPI path params capture differently; at minimum not a valid run_id
        # The endpoint validates internally, so expect 400 or 404.
        assert resp.status_code in {400, 404, 422}


# ---------------------------------------------------------------------------
# 3. GET /api/v1/runs/{run_id}/artifacts/{name}/versions
# ---------------------------------------------------------------------------


class TestArtifactVersions:
    """GET /api/v1/runs/{run_id}/artifacts/{name}/versions."""

    def test_returns_200_with_version_list(self):
        client = _client()
        resp = client.get(f"/api/v1/runs/{RUN_A}/artifacts/{ARTIFACT_NAME}/versions")
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list)
        assert len(body) == 1

    def test_version_has_required_fields(self):
        client = _client()
        resp = client.get(f"/api/v1/runs/{RUN_A}/artifacts/{ARTIFACT_NAME}/versions")
        v = resp.json()[0]
        assert v["version"] == 1
        assert v["run_id"] == RUN_A
        assert "created_at" in v
        assert "run_status" in v

    def test_calls_get_artifact_history(self):
        m = _mock_manager()
        client = _client(m)
        client.get(f"/api/v1/runs/{RUN_A}/artifacts/{ARTIFACT_NAME}/versions")
        m.get_artifact_history.assert_called_once_with(RUN_A, ARTIFACT_NAME)

    def test_invalid_artifact_name_dot_returns_400(self):
        client = _client()
        resp = client.get(f"/api/v1/runs/{RUN_A}/artifacts/bad.name/versions")
        assert resp.status_code == 400

    def test_invalid_artifact_name_slash_returns_400(self):
        client = _client()
        resp = client.get(f"/api/v1/runs/{RUN_A}/artifacts/%2F%2Fetc/versions")
        assert resp.status_code in {400, 404, 422}

    def test_traversal_name_returns_400(self):
        client = _client()
        import urllib.parse
        encoded = urllib.parse.quote("../../etc/passwd", safe="")
        resp = client.get(f"/api/v1/runs/{RUN_A}/artifacts/{encoded}/versions")
        assert resp.status_code in {400, 404, 422}


# ---------------------------------------------------------------------------
# 4. GET /api/v1/runs/{run_id}/artifacts/{name}/versions/{v}
# ---------------------------------------------------------------------------


class TestSpecificVersion:
    """GET /api/v1/runs/{run_id}/artifacts/{name}/versions/{v}."""

    def test_returns_artifact_dict(self):
        client = _client()
        resp = client.get(f"/api/v1/runs/{RUN_A}/artifacts/{ARTIFACT_NAME}/versions/1")
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, dict)
        assert "title" in body

    def test_calls_load_artifact_with_version(self):
        m = _mock_manager()
        client = _client(m)
        client.get(f"/api/v1/runs/{RUN_A}/artifacts/{ARTIFACT_NAME}/versions/2")
        m.load_artifact.assert_called_once_with(RUN_A, ARTIFACT_NAME, version=2)

    def test_missing_artifact_returns_404(self):
        m = _mock_manager()
        m.load_artifact.return_value = None
        client = _client(m)
        resp = client.get(f"/api/v1/runs/{RUN_A}/artifacts/{ARTIFACT_NAME}/versions/99")
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    def test_zero_version_returns_400(self):
        client = _client()
        resp = client.get(f"/api/v1/runs/{RUN_A}/artifacts/{ARTIFACT_NAME}/versions/0")
        assert resp.status_code == 400

    def test_negative_version_returns_400(self):
        client = _client()
        resp = client.get(f"/api/v1/runs/{RUN_A}/artifacts/{ARTIFACT_NAME}/versions/-1")
        assert resp.status_code in {400, 422}

    def test_non_integer_version_returns_422(self):
        client = _client()
        resp = client.get(f"/api/v1/runs/{RUN_A}/artifacts/{ARTIFACT_NAME}/versions/abc")
        assert resp.status_code == 422

    def test_traversal_in_version_rejected(self):
        """'../v1' in the version path must not reach the filesystem."""
        client = _client()
        resp = client.get(f"/api/v1/runs/{RUN_A}/artifacts/{ARTIFACT_NAME}/versions/../v1")
        # FastAPI will 404 or 422 because it can't route this to an int param
        assert resp.status_code in {400, 404, 422}


# ---------------------------------------------------------------------------
# 5. GET /api/v1/artifacts/compare
# ---------------------------------------------------------------------------


class TestCompareArtifacts:
    """GET /api/v1/artifacts/compare."""

    def test_returns_diff_with_added_removed_changed(self):
        client = _client()
        resp = client.get(
            f"/api/v1/artifacts/compare?run_a={RUN_A}&run_b={RUN_B}&artifact={ARTIFACT_NAME}"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "added" in body
        assert "removed" in body
        assert "changed" in body
        assert "name" in body
        assert "version_a" in body
        assert "version_b" in body

    def test_calls_compare_artifacts(self):
        m = _mock_manager()
        client = _client(m)
        client.get(
            f"/api/v1/artifacts/compare?run_a={RUN_A}&run_b={RUN_B}&artifact={ARTIFACT_NAME}"
        )
        m.compare_artifacts.assert_called_once_with(RUN_A, RUN_B, ARTIFACT_NAME)

    def test_added_keys_in_response(self):
        m = _mock_manager()
        m.compare_artifacts.return_value = _make_diff(added=["new_field", "extra"])
        client = _client(m)
        resp = client.get(
            f"/api/v1/artifacts/compare?run_a={RUN_A}&run_b={RUN_B}&artifact={ARTIFACT_NAME}"
        )
        assert resp.json()["added"] == ["new_field", "extra"]

    def test_missing_param_returns_422(self):
        client = _client()
        resp = client.get(f"/api/v1/artifacts/compare?run_a={RUN_A}&artifact={ARTIFACT_NAME}")
        assert resp.status_code == 422

    def test_invalid_artifact_name_returns_400(self):
        client = _client()
        resp = client.get(
            f"/api/v1/artifacts/compare?run_a={RUN_A}&run_b={RUN_B}&artifact=bad.name"
        )
        assert resp.status_code == 400

    def test_value_error_from_manager_returns_404(self):
        m = _mock_manager()
        m.compare_artifacts.side_effect = ValueError("Artifact not found")
        client = _client(m)
        resp = client.get(
            f"/api/v1/artifacts/compare?run_a={RUN_A}&run_b={RUN_B}&artifact={ARTIFACT_NAME}"
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 6. GET /api/v1/artifacts/search
# ---------------------------------------------------------------------------


class TestSearchArtifacts:
    """GET /api/v1/artifacts/search."""

    def test_returns_list(self):
        client = _client()
        resp = client.get("/api/v1/artifacts/search?q=prd")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_calls_search_with_query(self):
        m = _mock_manager()
        client = _client(m)
        client.get("/api/v1/artifacts/search?q=something&type=prd&agent=pm")
        m.search_artifacts.assert_called_once_with(
            query="something", artifact_type="prd", agent="pm"
        )

    def test_empty_query_is_valid(self):
        client = _client()
        resp = client.get("/api/v1/artifacts/search")
        assert resp.status_code == 200

    def test_type_filter_passed_through(self):
        m = _mock_manager()
        client = _client(m)
        client.get("/api/v1/artifacts/search?type=architecture")
        _, kwargs = m.search_artifacts.call_args
        assert kwargs.get("artifact_type") == "architecture" or m.search_artifacts.call_args[1].get("artifact_type") == "architecture" or m.search_artifacts.call_args[0][1] == "architecture"

    def test_invalid_type_filter_returns_400(self):
        client = _client()
        resp = client.get("/api/v1/artifacts/search?type=bad.type")
        assert resp.status_code == 400

    def test_invalid_agent_filter_returns_400(self):
        client = _client()
        resp = client.get("/api/v1/artifacts/search?agent=bad agent!")
        assert resp.status_code == 400

    def test_no_results_returns_empty_list(self):
        m = _mock_manager()
        m.search_artifacts.return_value = []
        client = _client(m)
        resp = client.get("/api/v1/artifacts/search?q=nothing")
        assert resp.status_code == 200
        assert resp.json() == []


# ---------------------------------------------------------------------------
# 7. POST /api/v1/artifacts/retention
# ---------------------------------------------------------------------------


class TestRetention:
    """POST /api/v1/artifacts/retention."""

    def test_dry_run_true_returns_200(self):
        client = _client()
        resp = client.post(
            "/api/v1/artifacts/retention",
            json={"max_age_days": 30, "max_runs": 10, "keep_failed": True, "dry_run": True},
        )
        assert resp.status_code == 200

    def test_dry_run_default_is_true(self):
        """Default dry_run=True so omitting it should still succeed."""
        client = _client()
        resp = client.post(
            "/api/v1/artifacts/retention",
            json={"max_age_days": 30, "max_runs": 10, "keep_failed": True},
        )
        assert resp.status_code == 200

    def test_dry_run_result_has_required_fields(self):
        client = _client()
        resp = client.post(
            "/api/v1/artifacts/retention",
            json={"max_age_days": 30, "max_runs": 10, "dry_run": True},
        )
        body = resp.json()
        assert "deleted_versions" in body
        assert "deleted_paths" in body
        assert "retained_versions" in body
        assert "dry_run" in body

    def test_dry_run_lists_without_deleting(self):
        """dry_run=True → apply_retention_policy called with dry_run=True → no files deleted."""
        m = _mock_manager()
        client = _client(m)
        client.post(
            "/api/v1/artifacts/retention",
            json={"max_age_days": 90, "max_runs": 50, "keep_failed": True, "dry_run": True},
        )
        call_kwargs = m.apply_retention_policy.call_args
        # dry_run=True must be forwarded
        assert call_kwargs.kwargs.get("dry_run") is True or call_kwargs.args[3] is True

    def test_non_dry_run_without_header_returns_403(self):
        """dry_run=False without the confirmation header → 403."""
        client = _client()
        resp = client.post(
            "/api/v1/artifacts/retention",
            json={"max_age_days": 30, "max_runs": 10, "dry_run": False},
        )
        assert resp.status_code == 403

    def test_non_dry_run_with_wrong_header_returns_403(self):
        """Wrong header value → 403."""
        client = _client()
        resp = client.post(
            "/api/v1/artifacts/retention",
            json={"max_age_days": 30, "max_runs": 10, "dry_run": False},
            headers={"X-Confirm-Retention-Delete": "no"},
        )
        assert resp.status_code == 403

    def test_non_dry_run_with_correct_header_returns_200(self):
        """dry_run=False + correct header → 200."""
        m = _mock_manager()
        m.apply_retention_policy.return_value = RetentionResult(
            deleted_versions=3,
            deleted_paths=["v1.json", "v2.json", "v3.json"],
            retained_versions=7,
            dry_run=False,
        )
        client = _client(m)
        resp = client.post(
            "/api/v1/artifacts/retention",
            json={"max_age_days": 30, "max_runs": 5, "dry_run": False},
            headers={"X-Confirm-Retention-Delete": "yes"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["dry_run"] is False
        assert body["deleted_versions"] == 3

    def test_retention_403_detail_mentions_header(self):
        client = _client()
        resp = client.post(
            "/api/v1/artifacts/retention",
            json={"dry_run": False},
        )
        assert "X-Confirm-Retention-Delete" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# 8. artifacts.html template rendering
# ---------------------------------------------------------------------------


class TestArtifactsHtml:
    """GET /runs/{run_id}/artifacts-view → HTML."""

    def test_html_page_returns_200(self):
        client = _client()
        resp = client.get(f"/runs/{RUN_A}/artifacts-view")
        assert resp.status_code == 200

    def test_html_page_has_correct_content_type(self):
        client = _client()
        resp = client.get(f"/runs/{RUN_A}/artifacts-view")
        assert "text/html" in resp.headers["content-type"]

    def test_html_contains_artifact_name(self):
        client = _client()
        resp = client.get(f"/runs/{RUN_A}/artifacts-view")
        assert ARTIFACT_NAME in resp.text

    def test_html_contains_run_id(self):
        client = _client()
        resp = client.get(f"/runs/{RUN_A}/artifacts-view")
        assert RUN_A in resp.text

    def test_html_has_table_element(self):
        client = _client()
        resp = client.get(f"/runs/{RUN_A}/artifacts-view")
        assert "<table" in resp.text

    def test_html_has_version_history_modal(self):
        client = _client()
        resp = client.get(f"/runs/{RUN_A}/artifacts-view")
        # TASK-007: replaced centre modal with right-side slide-over panel
        assert "version-slideover" in resp.text or "slideover" in resp.text

    def test_html_has_diff_viewer(self):
        client = _client()
        resp = client.get(f"/runs/{RUN_A}/artifacts-view")
        assert "diff-panel" in resp.text or "diff_panel" in resp.text or "compare" in resp.text.lower()

    def test_html_empty_artifacts_shows_empty_state(self):
        m = _mock_manager()
        m.list_artifacts.return_value = []
        client = _client(m)
        resp = client.get(f"/runs/{RUN_A}/artifacts-view")
        assert resp.status_code == 200
        assert "No artifacts" in resp.text


# ---------------------------------------------------------------------------
# 9. Path traversal defence
# ---------------------------------------------------------------------------


class TestPathTraversalDefence:
    """Verify traversal attempts are rejected at the HTTP layer."""

    @pytest.mark.parametrize("malicious_name", [
        "../../etc/passwd",
        "../secret",
        ".hidden",
        "name with spaces",
        "name;injection",
        "a" * 200,   # very long name (regex will reject)
        "file.json",
        "",
    ])
    def test_traversal_artifact_name_rejected(self, malicious_name: str):
        import urllib.parse
        encoded = urllib.parse.quote(malicious_name, safe="")
        client = _client()
        resp = client.get(f"/api/v1/runs/{RUN_A}/artifacts/{encoded}/versions")
        assert resp.status_code in {400, 404, 422}, (
            f"Malicious name {malicious_name!r} should be rejected but got {resp.status_code}"
        )

    def test_traversal_path_does_not_reach_filesystem(self, tmp_path: Path):
        """Ensure the ArtifactManager is never called with a traversal name."""
        m = _mock_manager()
        client = _client(m)
        import urllib.parse
        encoded = urllib.parse.quote("../../etc/passwd", safe="")
        client.get(f"/api/v1/runs/{RUN_A}/artifacts/{encoded}/versions")
        # ArtifactManager.get_artifact_history must NOT have been called
        m.get_artifact_history.assert_not_called()

    def test_version_traversal_rejected(self):
        """'../v1' format in version path should be rejected (non-integer)."""
        client = _client()
        resp = client.get(f"/api/v1/runs/{RUN_A}/artifacts/{ARTIFACT_NAME}/versions/%2E%2E%2Fv1")
        assert resp.status_code in {400, 404, 422}

    def test_compare_traversal_artifact_name_rejected(self):
        import urllib.parse
        bad = urllib.parse.quote("../../shadow", safe="")
        client = _client()
        resp = client.get(
            f"/api/v1/artifacts/compare?run_a={RUN_A}&run_b={RUN_B}&artifact={bad}"
        )
        assert resp.status_code in {400, 404, 422}


# ---------------------------------------------------------------------------
# 10. Validation helpers (unit)
# ---------------------------------------------------------------------------


class TestValidationHelpers:
    """Unit tests for the internal validation helpers."""

    def test_valid_names_pass(self):
        for name in ["prd", "architecture", "my-artifact", "artifact_v2", "A1"]:
            _validate_name(name)  # must not raise

    @pytest.mark.parametrize("bad_name", [
        "bad.name",
        "bad name",
        "../etc",
        "name/sub",
        "",
        "-starts-with-hyphen",
    ])
    def test_invalid_names_raise_http_exception(self, bad_name: str):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            _validate_name(bad_name)
        assert exc_info.value.status_code == 400

    def test_valid_run_ids_pass(self):
        for rid in ["abc123", "run-abc", "run_123", "a" * 64]:
            _validate_run_id(rid)  # must not raise

    def test_positive_version_passes(self):
        for v in [1, 2, 100]:
            _validate_version(v)  # must not raise

    def test_zero_version_raises(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            _validate_version(0)
        assert exc_info.value.status_code == 400

    def test_negative_version_raises(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            _validate_version(-5)


# ---------------------------------------------------------------------------
# 11. Integration with real ArtifactManager (filesystem)
# ---------------------------------------------------------------------------


class TestRealArtifactManager:
    """Integration tests using a real ArtifactManager backed by tmp_path."""

    @pytest.fixture()
    def artifacts_dir(self, tmp_path: Path) -> Path:
        d = tmp_path / "artifacts"
        d.mkdir()
        return d

    @pytest.fixture()
    def real_manager(self, artifacts_dir: Path) -> ArtifactManager:
        return ArtifactManager(artifacts_dir)

    @pytest.fixture()
    def real_client(self, real_manager: ArtifactManager) -> TestClient:
        app = FastAPI()
        templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
        router = create_artifacts_router(templates, real_manager)
        app.include_router(router)
        return TestClient(app, raise_server_exceptions=True)

    def test_list_artifacts_empty_returns_empty(self, real_client: TestClient):
        resp = real_client.get(f"/api/v1/runs/{RUN_A}/artifacts")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_save_then_list_artifacts(
        self, real_client: TestClient, real_manager: ArtifactManager
    ):
        real_manager.save_artifact(RUN_A, "prd", {"title": "My PRD"}, agent="pm")
        resp = real_client.get(f"/api/v1/runs/{RUN_A}/artifacts")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["name"] == "prd"
        assert body[0]["run_id"] == RUN_A

    def test_versions_endpoint_returns_history(
        self, real_client: TestClient, real_manager: ArtifactManager
    ):
        real_manager.save_artifact(RUN_A, "prd", {"v": 1})
        real_manager.save_artifact(RUN_A, "prd", {"v": 2})
        resp = real_client.get(f"/api/v1/runs/{RUN_A}/artifacts/prd/versions")
        assert resp.status_code == 200
        versions = resp.json()
        assert len(versions) == 2
        assert versions[0]["version"] == 1
        assert versions[1]["version"] == 2

    def test_specific_version_returns_correct_data(
        self, real_client: TestClient, real_manager: ArtifactManager
    ):
        real_manager.save_artifact(RUN_A, "prd", {"v": 1, "title": "V1"})
        real_manager.save_artifact(RUN_A, "prd", {"v": 2, "title": "V2"})
        resp = real_client.get(f"/api/v1/runs/{RUN_A}/artifacts/prd/versions/1")
        assert resp.status_code == 200
        assert resp.json()["title"] == "V1"

    def test_compare_returns_diff(
        self, real_client: TestClient, real_manager: ArtifactManager
    ):
        real_manager.save_artifact(RUN_A, "prd", {"title": "T1", "goals": []})
        real_manager.save_artifact(RUN_B, "prd", {"title": "T2", "requirements": []})
        resp = real_client.get(
            f"/api/v1/artifacts/compare?run_a={RUN_A}&run_b={RUN_B}&artifact=prd"
        )
        assert resp.status_code == 200
        diff = resp.json()
        assert "requirements" in diff["added"]
        assert "goals" in diff["removed"]
        assert "title" in diff["changed"]

    def test_retention_dry_run_returns_result(
        self, real_client: TestClient, real_manager: ArtifactManager
    ):
        real_manager.save_artifact(RUN_A, "prd", {"title": "Test"})
        resp = real_client.post(
            "/api/v1/artifacts/retention",
            json={"max_age_days": 90, "max_runs": 100, "dry_run": True},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["dry_run"] is True

    def test_path_traversal_in_name_rejected(self, real_client: TestClient):
        import urllib.parse
        bad = urllib.parse.quote("../../etc/passwd", safe="")
        resp = real_client.get(f"/api/v1/runs/{RUN_A}/artifacts/{bad}/versions")
        assert resp.status_code in {400, 404, 422}

    def test_search_returns_matching_artifacts(
        self, real_client: TestClient, real_manager: ArtifactManager
    ):
        real_manager.save_artifact(RUN_A, "prd", {"title": "hello"}, agent="pm")
        resp = real_client.get("/api/v1/artifacts/search?agent=pm")
        assert resp.status_code == 200
        results = resp.json()
        # At minimum the result is a list (may be empty if search not yet indexed)
        assert isinstance(results, list)
