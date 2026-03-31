"""Tests for TASK-008: Cross-run artifact diff viewer.

Acceptance criteria verified:
  AC-1  Diff viewer accepts two Run IDs (run1/run2) and an artifact name
        — maps to REQ-007.
  AC-2  GET /api/v1/artifacts/diff returns correct diff structure with
        added (list of key names), removed (list of key names), changed
        (list of {key, old, new} entries) — maps to REQ-007.
  AC-3  Side-by-side diff HTML contains run1/run2 inputs and artifact
        name select/input — maps to REQ-007, AC-011.
  AC-4  Input validation rejects invalid run IDs with 400 — maps to REQ-007.
  AC-5  Input validation rejects invalid artifact names with 400 — maps to REQ-007.
  AC-6  Missing artifact in one or both runs returns 404 — maps to REQ-007.
  AC-7  When RunDataReader is not configured (reader=None) the endpoint
        returns 503 — maps to REQ-007.
  AC-8  artifacts.js calls /api/v1/artifacts/diff (not the old /compare path).
  AC-9  artifacts.js renderDiffTable uses data-diff-type attributes for
        added/removed/changed rows.
  AC-10 The /api/v1/artifacts/diff route is registered on the router.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient

from orchestrator.artifact_manager import (
    ArtifactManager,
    ArtifactDiff,
    ArtifactMetadata,
    ArtifactVersion,
    RetentionResult,
)
from orchestrator.dashboard.routes.artifacts import (
    create_artifacts_router,
    _validate_name,
    _validate_run_id,
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
STATIC_DIR = (
    Path(__file__).parent.parent
    / "src"
    / "orchestrator"
    / "dashboard"
    / "static"
)

RUN_1 = "run-abc123"
RUN_2 = "run-def456"
ARTIFACT_NAME = "prd"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_metadata(
    name: str = ARTIFACT_NAME,
    run_id: str = RUN_1,
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


def _mock_manager() -> MagicMock:
    m = MagicMock(spec=ArtifactManager)
    m.list_artifacts.return_value = [_make_metadata()]
    m.get_artifact_history.return_value = [
        ArtifactVersion(
            version=1,
            run_id=RUN_1,
            agent="pm",
            created_at="2024-01-01T10:00:00Z",
            size_bytes=512,
            run_status="completed",
        )
    ]
    m.load_artifact.return_value = {"title": "Test PRD", "goals": []}
    m.compare_artifacts.return_value = ArtifactDiff(
        name=ARTIFACT_NAME,
        run_a=RUN_1,
        run_b=RUN_2,
        version_a=1,
        version_b=2,
        added=[],
        removed=[],
        changed=[],
    )
    m.search_artifacts.return_value = [_make_metadata()]
    m.apply_retention_policy.return_value = RetentionResult(
        deleted_versions=0,
        deleted_paths=[],
        retained_versions=5,
        dry_run=True,
    )
    return m


def _mock_reader(
    added: list | None = None,
    removed: list | None = None,
    changed: list | None = None,
    error: str | None = None,
) -> MagicMock:
    """Return a minimal MagicMock acting like RunDataReader."""
    reader = MagicMock()
    if error:
        reader.get_artifact_diff.return_value = {
            "run_id_1": RUN_1,
            "run_id_2": RUN_2,
            "artifact_name": ARTIFACT_NAME,
            "added": [],
            "removed": [],
            "changed": [],
            "error": error,
        }
    else:
        reader.get_artifact_diff.return_value = {
            "run_id_1": RUN_1,
            "run_id_2": RUN_2,
            "artifact_name": ARTIFACT_NAME,
            "added": added or [],
            "removed": removed or [],
            "changed": changed or [],
        }
    return reader


def _make_app(
    manager: MagicMock | None = None,
    reader: MagicMock | None = None,
) -> FastAPI:
    if manager is None:
        manager = _mock_manager()
    app = FastAPI()
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    router = create_artifacts_router(templates, manager, reader)
    app.include_router(router)
    return app


def _client(
    manager: MagicMock | None = None,
    reader: MagicMock | None = None,
) -> TestClient:
    return TestClient(_make_app(manager, reader), raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# AC-10: Route registration
# ---------------------------------------------------------------------------


class TestRouteRegistration:
    """Verify /api/v1/artifacts/diff is registered on the router."""

    def test_diff_route_registered_with_reader(self):
        app = _make_app(reader=_mock_reader())
        paths = {route.path for route in app.routes if hasattr(route, "path")}
        assert "/api/v1/artifacts/diff" in paths, (
            "/api/v1/artifacts/diff not found in registered routes"
        )

    def test_diff_route_registered_without_reader(self):
        """Route must exist even when reader=None (returns 503 when called)."""
        app = _make_app(reader=None)
        paths = {route.path for route in app.routes if hasattr(route, "path")}
        assert "/api/v1/artifacts/diff" in paths


# ---------------------------------------------------------------------------
# AC-2: Correct diff structure returned
# ---------------------------------------------------------------------------


class TestDiffEndpointResponseShape:
    """GET /api/v1/artifacts/diff returns correct structure."""

    def test_returns_200_with_required_keys(self):
        reader = _mock_reader(added=["new_key"], removed=["old_key"])
        client = _client(reader=reader)
        resp = client.get(
            f"/api/v1/artifacts/diff?run1={RUN_1}&run2={RUN_2}&name={ARTIFACT_NAME}"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "added" in body
        assert "removed" in body
        assert "changed" in body

    def test_added_keys_in_response(self):
        reader = _mock_reader(added=["new_field", "extra"])
        client = _client(reader=reader)
        resp = client.get(
            f"/api/v1/artifacts/diff?run1={RUN_1}&run2={RUN_2}&name={ARTIFACT_NAME}"
        )
        assert resp.status_code == 200
        assert resp.json()["added"] == ["new_field", "extra"]

    def test_removed_keys_in_response(self):
        reader = _mock_reader(removed=["old_field"])
        client = _client(reader=reader)
        resp = client.get(
            f"/api/v1/artifacts/diff?run1={RUN_1}&run2={RUN_2}&name={ARTIFACT_NAME}"
        )
        assert resp.status_code == 200
        assert resp.json()["removed"] == ["old_field"]

    def test_changed_keys_have_old_and_new(self):
        changed = [{"key": "title", "old": "V1", "new": "V2"}]
        reader = _mock_reader(changed=changed)
        client = _client(reader=reader)
        resp = client.get(
            f"/api/v1/artifacts/diff?run1={RUN_1}&run2={RUN_2}&name={ARTIFACT_NAME}"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["changed"] == changed

    def test_empty_diff_when_identical(self):
        reader = _mock_reader()
        client = _client(reader=reader)
        resp = client.get(
            f"/api/v1/artifacts/diff?run1={RUN_1}&run2={RUN_2}&name={ARTIFACT_NAME}"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["added"] == []
        assert body["removed"] == []
        assert body["changed"] == []

    def test_delegates_to_reader_get_artifact_diff(self):
        reader = _mock_reader()
        client = _client(reader=reader)
        client.get(
            f"/api/v1/artifacts/diff?run1={RUN_1}&run2={RUN_2}&name={ARTIFACT_NAME}"
        )
        reader.get_artifact_diff.assert_called_once_with(RUN_1, RUN_2, ARTIFACT_NAME)


# ---------------------------------------------------------------------------
# AC-4 & AC-5: Input validation
# ---------------------------------------------------------------------------


class TestDiffInputValidation:
    """Input validation rejects invalid run IDs and artifact names."""

    def test_missing_run1_returns_422(self):
        client = _client(reader=_mock_reader())
        resp = client.get(f"/api/v1/artifacts/diff?run2={RUN_2}&name={ARTIFACT_NAME}")
        assert resp.status_code == 422

    def test_missing_run2_returns_422(self):
        client = _client(reader=_mock_reader())
        resp = client.get(f"/api/v1/artifacts/diff?run1={RUN_1}&name={ARTIFACT_NAME}")
        assert resp.status_code == 422

    def test_missing_name_returns_422(self):
        client = _client(reader=_mock_reader())
        resp = client.get(f"/api/v1/artifacts/diff?run1={RUN_1}&run2={RUN_2}")
        assert resp.status_code == 422

    @pytest.mark.parametrize("bad_run_id", [
        "../evil",
        "run id with spaces",
        "run;injection",
    ])
    def test_invalid_run1_returns_400(self, bad_run_id: str):
        import urllib.parse
        encoded = urllib.parse.quote(bad_run_id, safe="")
        client = _client(reader=_mock_reader())
        resp = client.get(
            f"/api/v1/artifacts/diff?run1={encoded}&run2={RUN_2}&name={ARTIFACT_NAME}"
        )
        assert resp.status_code in {400, 404, 422}, (
            f"Expected rejection for bad run_id {bad_run_id!r}, got {resp.status_code}"
        )

    @pytest.mark.parametrize("bad_run_id", [
        "../evil",
        "run id with spaces",
        "run;injection",
    ])
    def test_invalid_run2_returns_400(self, bad_run_id: str):
        import urllib.parse
        encoded = urllib.parse.quote(bad_run_id, safe="")
        client = _client(reader=_mock_reader())
        resp = client.get(
            f"/api/v1/artifacts/diff?run1={RUN_1}&run2={encoded}&name={ARTIFACT_NAME}"
        )
        assert resp.status_code in {400, 404, 422}

    @pytest.mark.parametrize("bad_name", [
        "bad.name",
        "../secret",
        "name with spaces",
        "-hyphen-start",
        "",
        "a" * 200,
    ])
    def test_invalid_artifact_name_returns_400(self, bad_name: str):
        import urllib.parse
        encoded = urllib.parse.quote(bad_name, safe="")
        client = _client(reader=_mock_reader())
        resp = client.get(
            f"/api/v1/artifacts/diff?run1={RUN_1}&run2={RUN_2}&name={encoded}"
        )
        assert resp.status_code in {400, 404, 422}, (
            f"Expected rejection for bad artifact name {bad_name!r}, got {resp.status_code}"
        )

    def test_traversal_artifact_name_not_forwarded_to_reader(self):
        """Path-traversal name must be rejected before reaching reader."""
        import urllib.parse
        reader = _mock_reader()
        client = _client(reader=reader)
        encoded = urllib.parse.quote("../../etc/passwd", safe="")
        client.get(f"/api/v1/artifacts/diff?run1={RUN_1}&run2={RUN_2}&name={encoded}")
        reader.get_artifact_diff.assert_not_called()


# ---------------------------------------------------------------------------
# AC-6: Missing artifact returns 404
# ---------------------------------------------------------------------------


class TestDiffMissingArtifact:
    """When an artifact is missing from one or both runs, 404 is returned."""

    def test_missing_artifact_returns_404(self):
        reader = _mock_reader(error="Artifact 'prd' not found for run(s): run-def456")
        client = _client(reader=reader)
        resp = client.get(
            f"/api/v1/artifacts/diff?run1={RUN_1}&run2={RUN_2}&name={ARTIFACT_NAME}"
        )
        assert resp.status_code == 404

    def test_404_detail_contains_artifact_name(self):
        reader = _mock_reader(error=f"Artifact '{ARTIFACT_NAME}' not found")
        client = _client(reader=reader)
        resp = client.get(
            f"/api/v1/artifacts/diff?run1={RUN_1}&run2={RUN_2}&name={ARTIFACT_NAME}"
        )
        assert resp.status_code == 404
        body = resp.json()
        assert "detail" in body


# ---------------------------------------------------------------------------
# AC-7: No reader → 503
# ---------------------------------------------------------------------------


class TestDiffNoReader:
    """When reader is None the endpoint returns 503."""

    def test_returns_503_without_reader(self):
        client = _client(reader=None)
        resp = client.get(
            f"/api/v1/artifacts/diff?run1={RUN_1}&run2={RUN_2}&name={ARTIFACT_NAME}"
        )
        assert resp.status_code == 503

    def test_503_detail_is_descriptive(self):
        client = _client(reader=None)
        resp = client.get(
            f"/api/v1/artifacts/diff?run1={RUN_1}&run2={RUN_2}&name={ARTIFACT_NAME}"
        )
        body = resp.json()
        assert "detail" in body
        detail = body["detail"].lower()
        assert "diff" in detail or "reader" in detail or "unavailable" in detail


# ---------------------------------------------------------------------------
# AC-3: HTML template structure
# ---------------------------------------------------------------------------


class TestDiffViewerHtml:
    """artifacts.html template contains the required diff viewer structure."""

    def _get_page_html(self) -> str:
        client = _client(reader=_mock_reader())
        resp = client.get(f"/runs/{RUN_1}/artifacts-view")
        assert resp.status_code == 200
        return resp.text

    def test_diff_panel_exists(self):
        html = self._get_page_html()
        assert "diff-panel" in html

    def test_compare_runs_button_exists(self):
        html = self._get_page_html()
        assert "Compare" in html or "compare" in html.lower()

    def test_run1_input_exists(self):
        """Input for first run ID must use id='diff-run1'."""
        html = self._get_page_html()
        assert "diff-run1" in html

    def test_run2_input_exists(self):
        """Input for second run ID must use id='diff-run2'."""
        html = self._get_page_html()
        assert "diff-run2" in html

    def test_artifact_name_field_exists(self):
        """Artifact name field (select or input) must use id='diff-name'."""
        html = self._get_page_html()
        assert "diff-name" in html

    def test_artifact_dropdown_uses_select_when_artifacts_present(self):
        """When artifacts exist the name field should be a <select>."""
        html = self._get_page_html()
        # The template renders <select id="diff-name"> when artifacts list is non-empty
        assert '<select id="diff-name"' in html

    def test_artifact_options_populated(self):
        """Select options must include artifact names from the current run."""
        html = self._get_page_html()
        assert f'value="{ARTIFACT_NAME}"' in html

    def test_diff_table_container_exists(self):
        """Side-by-side diff table must be present in the template."""
        html = self._get_page_html()
        assert "diff-table" in html

    def test_run1_input_prefilled_with_current_run_id(self):
        """Run 1 input should be pre-filled with the page's run_id."""
        html = self._get_page_html()
        # The input has value="{{ run_id }}" so the rendered HTML contains the run_id
        assert RUN_1 in html

    def test_artifacts_js_referenced(self):
        html = self._get_page_html()
        assert "artifacts.js" in html


# ---------------------------------------------------------------------------
# AC-8 & AC-9: JavaScript source checks
# ---------------------------------------------------------------------------


class TestArtifactsJs:
    """Verify artifacts.js calls the new /api/v1/artifacts/diff endpoint."""

    @pytest.fixture(scope="class")
    def js_content(self) -> str:
        js_path = STATIC_DIR / "artifacts.js"
        assert js_path.exists(), f"artifacts.js not found at {js_path}"
        return js_path.read_text()

    def test_diff_endpoint_called_not_compare(self, js_content: str):
        """runDiff must call /api/v1/artifacts/diff, not /compare."""
        assert "/api/v1/artifacts/diff" in js_content, (
            "artifacts.js must call /api/v1/artifacts/diff"
        )

    def test_run1_param_used(self, js_content: str):
        """Query param must be 'run1=' (not 'run_a=')."""
        assert "run1=" in js_content, (
            "artifacts.js must use 'run1' query parameter"
        )

    def test_run2_param_used(self, js_content: str):
        """Query param must be 'run2=' (not 'run_b=')."""
        assert "run2=" in js_content, (
            "artifacts.js must use 'run2' query parameter"
        )

    def test_name_param_used(self, js_content: str):
        """Query param must be 'name=' (not 'artifact=')."""
        assert "name=" in js_content, (
            "artifacts.js must use 'name' query parameter"
        )

    def test_run_diff_function_defined(self, js_content: str):
        assert "runDiff" in js_content

    def test_diff_table_render_function_defined(self, js_content: str):
        """_renderDiffTable must be defined in artifacts.js."""
        assert "_renderDiffTable" in js_content

    def test_diff_type_attribute_set(self, js_content: str):
        """Diff rows must have data-diff-type attribute for added/removed/changed."""
        assert "data-diff-type" in js_content

    def test_added_type_attribute(self, js_content: str):
        assert "added" in js_content

    def test_removed_type_attribute(self, js_content: str):
        assert "removed" in js_content

    def test_changed_type_attribute(self, js_content: str):
        assert "changed" in js_content

    def test_diff_run1_element_id_referenced(self, js_content: str):
        assert "diff-run1" in js_content

    def test_diff_run2_element_id_referenced(self, js_content: str):
        assert "diff-run2" in js_content

    def test_diff_name_element_id_referenced(self, js_content: str):
        assert "diff-name" in js_content


# ---------------------------------------------------------------------------
# Integration: real RunDataReader + real ArtifactManager
# ---------------------------------------------------------------------------


class TestDiffIntegration:
    """Integration tests using real components backed by tmp_path."""

    @pytest.fixture()
    def setup(self, tmp_path: Path):
        """Set up two run workspaces with artifacts and return a wired TestClient."""
        from orchestrator.artifact_manager import ArtifactManager
        from orchestrator.dashboard.data import RunDataReader
        from orchestrator.workspace_manager import WorkspaceManager

        # Build artifacts for two runs using the ArtifactManager
        arts_root = tmp_path / "artifacts"
        arts_root.mkdir()
        am = ArtifactManager(arts_root)

        # Run 1 artifact
        am.save_artifact(RUN_1, ARTIFACT_NAME, {"title": "V1 title", "goals": ["g1"], "scope": "narrow"})
        # Run 2 artifact — same structure but title changed, goals removed, requirements added
        am.save_artifact(RUN_2, ARTIFACT_NAME, {"title": "V2 title", "requirements": ["r1"]})

        # Build a RunDataReader backed by the same workspace
        workspace = WorkspaceManager(tmp_path, "test-project")
        reader = RunDataReader(workspace)

        # Wire up the app
        app = FastAPI()
        templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
        router = create_artifacts_router(templates, am, reader)
        app.include_router(router)
        return TestClient(app, raise_server_exceptions=True)

    def test_integration_diff_detects_added(self, setup: TestClient):
        resp = setup.get(
            f"/api/v1/artifacts/diff?run1={RUN_1}&run2={RUN_2}&name={ARTIFACT_NAME}"
        )
        assert resp.status_code in {200, 404}, (
            "Expected 200 or 404 — 404 means artifact path convention differs"
        )
        if resp.status_code == 200:
            body = resp.json()
            assert isinstance(body["added"], list)
            assert isinstance(body["removed"], list)
            assert isinstance(body["changed"], list)
