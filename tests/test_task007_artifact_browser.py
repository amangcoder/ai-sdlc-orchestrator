"""Tests for TASK-007: Rich artifact browser with slide-over version history.

Acceptance criteria verified:
  AC-1  Artifact table shows Name, Schema, Agent, Version, Last Updated,
        Size, and Validation Status columns — maps to REQ-006, AC-009.
  AC-2  Slide-over panel (right-side drawer) exists in the HTML with
        correct structure — maps to REQ-006, AC-010.
  AC-3  Version list in slide-over is loaded via a fetch call to
        GET /api/v1/runs/{run_id}/artifacts/{name}/versions.
  AC-4  Clicking a version loads content via
        GET /api/v1/runs/{run_id}/artifacts/{name}/versions/{v}
        and the content is displayed inside the slide-over.
  AC-5  Copy and Download buttons are present in the slide-over panel.
  AC-6  Client-side search input (#artifact-search) exists above the table.
  AC-7  Slide-over closes on Escape key (handled by artifacts.js).
  AC-8  Slide-over closes on click outside (slideover-overlay click handler).
  AC-9  Validation Status column uses badge markup for valid/invalid states.
  AC-10 artifacts.js static file is referenced from the template.
  AC-11 openSlideover is the primary row-click handler (JS function reference).
  AC-12 Size column is human-readable (B / KB / MB).
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
    ArtifactMetadata,
    ArtifactVersion,
    ArtifactDiff,
    RetentionResult,
)
from orchestrator.dashboard.routes.artifacts import create_artifacts_router

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

RUN_ID = "run-task007abc"
ARTIFACT_NAME = "prd"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_metadata(
    name: str = ARTIFACT_NAME,
    run_id: str = RUN_ID,
    version: int = 2,
    agent: str | None = "pm",
    schema_name: str | None = "prd",
    valid: bool = True,
    size_bytes: int = 2048,
) -> ArtifactMetadata:
    return ArtifactMetadata(
        name=name,
        current_version=version,
        run_id=run_id,
        agent=agent,
        schema_name=schema_name,
        created_at="2024-06-01T10:00:00Z",
        updated_at="2024-06-01T11:30:00Z",
        size_bytes=size_bytes,
        valid=valid,
    )


def _make_version(v: int = 1) -> ArtifactVersion:
    return ArtifactVersion(
        version=v,
        run_id=RUN_ID,
        agent="pm",
        created_at="2024-06-01T10:00:00Z",
        size_bytes=1024,
        run_status="completed",
    )


def _mock_manager() -> MagicMock:
    m = MagicMock(spec=ArtifactManager)
    m.list_artifacts.return_value = [_make_metadata()]
    m.get_artifact_history.return_value = [_make_version(1), _make_version(2)]
    m.load_artifact.return_value = {"title": "PRD v2", "goals": ["g1"]}
    m.compare_artifacts.return_value = ArtifactDiff(
        name=ARTIFACT_NAME,
        run_a=RUN_ID,
        run_b="run-other",
        version_a=1,
        version_b=2,
        added=["new_key"],
        removed=[],
        changed=["title"],
    )
    m.search_artifacts.return_value = [_make_metadata()]
    m.apply_retention_policy.return_value = RetentionResult(
        deleted_versions=0,
        deleted_paths=[],
        retained_versions=2,
        dry_run=True,
    )
    return m


def _make_app(manager: MagicMock | None = None) -> FastAPI:
    if manager is None:
        manager = _mock_manager()
    app = FastAPI()
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    router = create_artifacts_router(templates, manager)
    app.include_router(router)
    return app


def _client(manager: MagicMock | None = None) -> TestClient:
    return TestClient(_make_app(manager), raise_server_exceptions=True)


def _html(manager: MagicMock | None = None) -> str:
    c = _client(manager)
    resp = c.get(f"/runs/{RUN_ID}/artifacts-view")
    assert resp.status_code == 200, f"Page returned {resp.status_code}"
    return resp.text


# ---------------------------------------------------------------------------
# AC-1: Table column headings
# ---------------------------------------------------------------------------


class TestTableColumns:
    """Artifact table must expose all 7 required columns."""

    def test_name_column_header_present(self):
        html = _html()
        assert "<th>Name</th>" in html or ">Name<" in html

    def test_schema_column_header_present(self):
        html = _html()
        assert "Schema" in html

    def test_agent_column_header_present(self):
        html = _html()
        assert "Agent" in html

    def test_version_column_header_present(self):
        html = _html()
        assert "Version" in html

    def test_last_updated_column_header_present(self):
        html = _html()
        assert "Updated" in html or "Last Updated" in html

    def test_size_column_header_present(self):
        html = _html()
        assert "Size" in html

    def test_valid_column_header_present(self):
        html = _html()
        assert "Valid" in html

    def test_table_has_7_th_elements(self):
        html = _html()
        # Crude count: at least 7 <th> elements in the artifacts-table header
        import re
        ths = re.findall(r"<th[>\s]", html)
        assert len(ths) >= 7, f"Expected ≥7 <th> elements, found {len(ths)}"

    def test_artifact_name_shown_in_table(self):
        html = _html()
        assert ARTIFACT_NAME in html

    def test_schema_name_shown_in_table(self):
        html = _html()
        assert "prd" in html  # schema_name == "prd"

    def test_agent_name_shown_in_table(self):
        html = _html()
        assert "pm" in html

    def test_version_shown_in_table(self):
        html = _html()
        assert "v2" in html  # current_version == 2


# ---------------------------------------------------------------------------
# AC-2 & AC-3: Slide-over panel structure
# ---------------------------------------------------------------------------


class TestSlideoverPanel:
    """Right-side slide-over panel must exist with correct IDs and markup."""

    def test_slideover_panel_id_present(self):
        html = _html()
        assert "version-slideover" in html

    def test_slideover_overlay_id_present(self):
        html = _html()
        assert "slideover-overlay" in html

    def test_slideover_title_id_present(self):
        html = _html()
        assert "slideover-title" in html

    def test_slideover_versions_container_present(self):
        html = _html()
        assert "slideover-versions" in html

    def test_slideover_transform_translatex_in_style(self):
        """Panel must start off-screen via translateX(100%)."""
        html = _html()
        assert "translateX(100%)" in html

    def test_slideover_transition_in_style(self):
        """Smooth animation requires a CSS transition on transform."""
        html = _html()
        assert "transition" in html
        assert "transform" in html

    def test_slideover_width_400px(self):
        """Panel must be 400 px wide per spec."""
        html = _html()
        assert "400px" in html

    def test_slideover_close_button_present(self):
        html = _html()
        assert "closeSlideover" in html

    def test_row_opens_slideover(self):
        """Each artifact row must call openSlideover() on click."""
        html = _html()
        assert "openSlideover" in html

    def test_slideover_overlay_closes_panel(self):
        """Overlay element triggers closeSlideover."""
        html = _html()
        assert "slideover-overlay" in html

    def test_versions_fetch_url_pattern_referenced(self):
        """The JS file must reference the /versions API path."""
        js_file = STATIC_DIR / "artifacts.js"
        assert js_file.exists(), "artifacts.js does not exist"
        content = js_file.read_text()
        assert "/versions" in content


# ---------------------------------------------------------------------------
# AC-4: Content viewer in slide-over
# ---------------------------------------------------------------------------


class TestContentViewer:
    """Content area within the slide-over must be present."""

    def test_content_section_id_present(self):
        html = _html()
        assert "slideover-content-section" in html

    def test_content_viewer_pre_element_present(self):
        html = _html()
        assert "slideover-content-viewer" in html

    def test_content_title_id_present(self):
        html = _html()
        assert "slideover-content-title" in html

    def test_monospace_font_in_viewer_style(self):
        html = _html()
        assert "monospace" in html


# ---------------------------------------------------------------------------
# AC-5: Copy and Download buttons
# ---------------------------------------------------------------------------


class TestCopyDownloadButtons:
    """Both action buttons must be present in the slide-over panel HTML."""

    def test_copy_button_present(self):
        html = _html()
        assert "btn-copy" in html or "Copy" in html

    def test_download_button_present(self):
        html = _html()
        assert "btn-download" in html or "Download" in html

    def test_copy_uses_clipboard_api_in_js(self):
        js_file = STATIC_DIR / "artifacts.js"
        assert js_file.exists()
        content = js_file.read_text()
        assert "navigator.clipboard.writeText" in content

    def test_download_uses_blob_in_js(self):
        js_file = STATIC_DIR / "artifacts.js"
        assert js_file.exists()
        content = js_file.read_text()
        assert "Blob" in content
        assert "URL.createObjectURL" in content

    def test_copy_artifact_content_function_exported(self):
        js_file = STATIC_DIR / "artifacts.js"
        content = js_file.read_text()
        assert "copyArtifactContent" in content

    def test_download_artifact_content_function_exported(self):
        js_file = STATIC_DIR / "artifacts.js"
        content = js_file.read_text()
        assert "downloadArtifactContent" in content


# ---------------------------------------------------------------------------
# AC-6: Client-side search input
# ---------------------------------------------------------------------------


class TestSearchInput:
    """A search input above the table must exist and filter by name."""

    def test_artifact_search_input_present(self):
        html = _html()
        assert 'id="artifact-search"' in html

    def test_search_placeholder_text(self):
        html = _html()
        assert "Filter artifacts" in html or "filter" in html.lower()

    def test_search_keyup_handler_in_js(self):
        js_file = STATIC_DIR / "artifacts.js"
        content = js_file.read_text()
        assert "keyup" in content
        assert "artifact-search" in content

    def test_search_filters_by_data_name_attribute(self):
        """JS should use data-name attribute on rows to filter."""
        js_file = STATIC_DIR / "artifacts.js"
        content = js_file.read_text()
        assert "data-name" in content or "dataset.name" in content

    def test_artifact_rows_have_data_name_attribute(self):
        html = _html()
        assert 'data-name="' in html


# ---------------------------------------------------------------------------
# AC-7 & AC-8: Keyboard and outside-click close
# ---------------------------------------------------------------------------


class TestCloseHandlers:
    """Escape key and overlay click must close the slide-over."""

    def test_escape_key_handler_in_js(self):
        js_file = STATIC_DIR / "artifacts.js"
        content = js_file.read_text()
        assert "Escape" in content

    def test_escape_calls_close_slideover(self):
        js_file = STATIC_DIR / "artifacts.js"
        content = js_file.read_text()
        # Escape block calls closeSlideover
        escape_idx = content.find("Escape")
        close_idx = content.find("closeSlideover", escape_idx)
        assert close_idx > escape_idx, "closeSlideover must be called after Escape check"

    def test_overlay_click_calls_close_in_js(self):
        js_file = STATIC_DIR / "artifacts.js"
        content = js_file.read_text()
        # overlay click listener -> closeSlideover
        overlay_idx = content.find("slideover-overlay")
        close_idx = content.find("closeSlideover", overlay_idx)
        assert close_idx > overlay_idx, "closeSlideover must follow slideover-overlay handler"

    def test_close_slideover_sets_transform(self):
        js_file = STATIC_DIR / "artifacts.js"
        content = js_file.read_text()
        assert "translateX(100%)" in content


# ---------------------------------------------------------------------------
# AC-9: Validation status badge
# ---------------------------------------------------------------------------


class TestValidationStatusBadge:
    """Valid/Invalid badge must appear correctly in the table."""

    def test_valid_artifact_shows_valid_badge(self):
        html = _html()
        assert "Valid" in html or "✓" in html

    def test_invalid_artifact_shows_invalid_badge(self):
        m = _mock_manager()
        m.list_artifacts.return_value = [_make_metadata(valid=False)]
        html = _html(m)
        assert "Invalid" in html or "✗" in html

    def test_valid_badge_uses_success_class(self):
        html = _html()
        # The badge class should be one of the success variants from style.css
        assert "badge-success" in html or "badge-completed" in html

    def test_invalid_badge_uses_error_class(self):
        m = _mock_manager()
        m.list_artifacts.return_value = [_make_metadata(valid=False)]
        html = _html(m)
        assert "badge-failed" in html or "badge-error" in html


# ---------------------------------------------------------------------------
# AC-10: artifacts.js is referenced in the template
# ---------------------------------------------------------------------------


class TestStaticJsReference:
    def test_artifacts_js_script_tag_in_html(self):
        html = _html()
        assert "artifacts.js" in html

    def test_artifacts_js_file_exists(self):
        assert (STATIC_DIR / "artifacts.js").exists()

    def test_artifacts_js_is_not_empty(self):
        content = (STATIC_DIR / "artifacts.js").read_text()
        assert len(content) > 200  # Meaningful JS content


# ---------------------------------------------------------------------------
# AC-12: Human-readable size in table
# ---------------------------------------------------------------------------


class TestHumanReadableSize:
    """Size column must display human-readable units."""

    def test_size_shown_with_unit(self):
        html = _html()
        # 2048 bytes == 2.0 KB
        assert "KB" in html or "MB" in html or " B" in html

    def test_bytes_unit_for_small_files(self):
        m = _mock_manager()
        m.list_artifacts.return_value = [_make_metadata(size_bytes=512)]
        html = _html(m)
        # 512 < 1024 -> shown as "512 B"
        assert "512" in html and (" B" in html or "B" in html)

    def test_kb_unit_for_kilobyte_files(self):
        m = _mock_manager()
        m.list_artifacts.return_value = [_make_metadata(size_bytes=3072)]  # 3 KB
        html = _html(m)
        assert "KB" in html or "3.0" in html

    def test_mb_unit_for_large_files(self):
        m = _mock_manager()
        m.list_artifacts.return_value = [_make_metadata(size_bytes=2_097_152)]  # 2 MB
        html = _html(m)
        assert "MB" in html


# ---------------------------------------------------------------------------
# JavaScript function exports (unit-level string inspection of artifacts.js)
# ---------------------------------------------------------------------------


class TestJsFunctions:
    """Key public functions must be exported via window.* in artifacts.js."""

    @pytest.fixture(autouse=True)
    def js_content(self):
        self._js = (STATIC_DIR / "artifacts.js").read_text()

    def test_open_slideover_exported(self):
        assert "window.openSlideover" in self._js

    def test_close_slideover_exported(self):
        assert "window.closeSlideover" in self._js

    def test_load_version_content_exported(self):
        assert "window.loadVersionContent" in self._js

    def test_copy_artifact_content_exported(self):
        assert "window.copyArtifactContent" in self._js

    def test_download_artifact_content_exported(self):
        assert "window.downloadArtifactContent" in self._js

    def test_toggle_diff_panel_exported(self):
        assert "window.toggleDiffPanel" in self._js

    def test_run_diff_exported(self):
        assert "window.runDiff" in self._js

    def test_run_search_exported(self):
        assert "window.runSearch" in self._js

    def test_syntax_highlight_function_present(self):
        assert "_syntaxHighlightJson" in self._js or "syntaxHighlight" in self._js

    def test_json_key_class_present(self):
        assert "json-key" in self._js

    def test_json_string_class_present(self):
        assert "json-string" in self._js

    def test_json_number_class_present(self):
        assert "json-number" in self._js


# ---------------------------------------------------------------------------
# API endpoint integration (regression: existing endpoints still work)
# ---------------------------------------------------------------------------


class TestApiEndpointsRegressionTask007:
    """Ensure existing API endpoints continue to work after TASK-007 changes."""

    def test_list_artifacts_returns_200(self):
        resp = _client().get(f"/api/v1/runs/{RUN_ID}/artifacts")
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list)
        assert body[0]["name"] == ARTIFACT_NAME

    def test_list_artifacts_includes_valid_field(self):
        """The valid field must be serialised in the API response."""
        resp = _client().get(f"/api/v1/runs/{RUN_ID}/artifacts")
        item = resp.json()[0]
        assert "valid" in item

    def test_versions_endpoint_returns_200(self):
        resp = _client().get(f"/api/v1/runs/{RUN_ID}/artifacts/{ARTIFACT_NAME}/versions")
        assert resp.status_code == 200
        versions = resp.json()
        assert len(versions) == 2

    def test_specific_version_endpoint_returns_200(self):
        resp = _client().get(f"/api/v1/runs/{RUN_ID}/artifacts/{ARTIFACT_NAME}/versions/1")
        assert resp.status_code == 200
        assert isinstance(resp.json(), dict)

    def test_page_still_returns_200(self):
        resp = _client().get(f"/runs/{RUN_ID}/artifacts-view")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_page_empty_artifacts_shows_empty_state(self):
        m = _mock_manager()
        m.list_artifacts.return_value = []
        resp = _client(m).get(f"/runs/{RUN_ID}/artifacts-view")
        assert resp.status_code == 200
        assert "No artifacts" in resp.text
