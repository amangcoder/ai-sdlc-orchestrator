"""Tests for TASK-005 — Enhanced live run view.

Acceptance criteria verified
------------------------------
AC-1: Phase stepper renders all phases with correct color coding
      (completed=green checkmark, current=amber pulsing, pending=grey)
AC-2: Event log container auto-scroll behaviour is present
      (floating "Scroll to Bottom" button exists in the template)
AC-3: Prompt card section exists in the template with agent name, question,
      textarea, and Submit button
AC-4: Cancel and Resume buttons are present in the metadata sidebar
AC-5: Phase stepper updates dynamically on phase_transition SSE events
      (verified via JS presence: updatePhaseStep() function in rendered HTML)
AC-6: Route passes workflow_phases list to the template context
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TEMPLATES_DIR = Path(__file__).parent.parent / "src" / "orchestrator" / "dashboard" / "templates"


def _make_dashboard_app(workspace_root: Path, project_name: str = "test-project"):
    """Create a full dashboard app with auth disabled."""
    import orchestrator.dashboard.app as _app_mod
    from orchestrator.dashboard.app import create_app

    saved_token = _app_mod.DASHBOARD_TOKEN
    _app_mod.DASHBOARD_TOKEN = None
    try:
        return create_app(workspace_root, project_name)
    finally:
        _app_mod.DASHBOARD_TOKEN = saved_token


def _write_state_file(workspace: Path, run_id: str, state: dict) -> Path:
    """Write a state JSON file for a run and return its path."""
    state_path = workspace / f"state-{run_id}.json"
    state_path.write_text(json.dumps(state))
    return state_path


def _minimal_state(run_id: str, **overrides) -> dict:
    """Return a minimal valid run state dict."""
    base = {
        "run_id": run_id,
        "feature_request": "Test feature request",
        "workflow_type": "feature_development",
        "status": "running",
        "current_step": "architecture",
        "completed_steps": ["UX Specification", "PRD"],
        "phases": {
            "ux_specification": {"status": "completed"},
            "prd": {"status": "completed"},
            "architecture": {"status": "running"},
        },
        "total_cost_usd": 0.0042,
        "start_time": "2026-03-31T10:00:00+00:00",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

RUN_ID = "abc123def456"


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Workspace with a valid run state file."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    _write_state_file(ws, RUN_ID, _minimal_state(RUN_ID))
    return tmp_path


@pytest.fixture
def client(workspace: Path):
    """TestClient for the dashboard app with a valid run state file."""
    app = _make_dashboard_app(workspace)
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# AC-6: Route passes workflow_phases to template context
# ---------------------------------------------------------------------------

class TestBuildWorkflowPhases:
    """Unit tests for the _build_workflow_phases helper."""

    def test_returns_list(self, workspace: Path):
        from orchestrator.dashboard.app import _build_workflow_phases
        from orchestrator.dashboard.data import RunDataReader, RunDetail
        from orchestrator.workspace_manager import WorkspaceManager

        mgr = WorkspaceManager(workspace, "test-project")
        run = RunDetail(
            run_id=RUN_ID,
            feature_request="Test",
            workflow_type="feature_development",
            status="running",
            total_cost_usd=0.0,
            phases={
                "ux_specification": {"status": "completed"},
                "prd": {"status": "completed"},
                "architecture": {"status": "running"},
            },
            events=[],
            timeline=None,
            interrupt_history=[],
        )
        phases = _build_workflow_phases(RUN_ID, run, mgr)
        assert isinstance(phases, list)
        assert len(phases) >= 3

    def test_phase_dict_keys(self, workspace: Path):
        from orchestrator.dashboard.app import _build_workflow_phases
        from orchestrator.dashboard.data import RunDetail
        from orchestrator.workspace_manager import WorkspaceManager

        mgr = WorkspaceManager(workspace, "test-project")
        run = RunDetail(
            run_id=RUN_ID,
            feature_request="Test",
            workflow_type="feature_development",
            status="running",
            total_cost_usd=0.0,
            phases={"ux_specification": {"status": "completed"}},
            events=[],
            timeline=None,
            interrupt_history=[],
        )
        phases = _build_workflow_phases(RUN_ID, run, mgr)
        for p in phases:
            assert "key" in p
            assert "label" in p
            assert "status" in p

    def test_completed_phase_has_completed_status(self, workspace: Path):
        from orchestrator.dashboard.app import _build_workflow_phases
        from orchestrator.dashboard.data import RunDetail
        from orchestrator.workspace_manager import WorkspaceManager

        mgr = WorkspaceManager(workspace, "test-project")
        run = RunDetail(
            run_id=RUN_ID,
            feature_request="Test",
            workflow_type="feature_development",
            status="running",
            total_cost_usd=0.0,
            phases={
                "ux_specification": {"status": "completed"},
                "prd": {"status": "running"},
            },
            events=[],
            timeline=None,
            interrupt_history=[],
        )
        phases = _build_workflow_phases(RUN_ID, run, mgr)
        statuses = {p["key"]: p["status"] for p in phases}
        assert statuses.get("ux_specification") == "completed"

    def test_running_phase_has_running_status(self, workspace: Path):
        from orchestrator.dashboard.app import _build_workflow_phases
        from orchestrator.dashboard.data import RunDetail
        from orchestrator.workspace_manager import WorkspaceManager

        mgr = WorkspaceManager(workspace, "test-project")
        run = RunDetail(
            run_id=RUN_ID,
            feature_request="Test",
            workflow_type="feature_development",
            status="running",
            total_cost_usd=0.0,
            phases={
                "ux_specification": {"status": "completed"},
                "architecture": {"status": "running"},
            },
            events=[],
            timeline=None,
            interrupt_history=[],
        )
        phases = _build_workflow_phases(RUN_ID, run, mgr)
        statuses = {p["key"]: p["status"] for p in phases}
        assert statuses.get("architecture") == "running"

    def test_fallback_when_no_state_file(self, tmp_path: Path):
        """Falls back to phases dict when state file is absent."""
        from orchestrator.dashboard.app import _build_workflow_phases
        from orchestrator.dashboard.data import RunDetail
        from orchestrator.workspace_manager import WorkspaceManager

        empty_ws = tmp_path / "empty"
        empty_ws.mkdir()
        mgr = WorkspaceManager(empty_ws, "proj")
        run = RunDetail(
            run_id="notexist",
            feature_request="",
            workflow_type="feature_development",
            status="running",
            total_cost_usd=0.0,
            phases={"prd": {"status": "completed"}},
            events=[],
            timeline=None,
            interrupt_history=[],
        )
        phases = _build_workflow_phases("notexist", run, mgr)
        assert len(phases) >= 1
        assert phases[0]["key"] == "prd"
        assert phases[0]["status"] == "completed"


# ---------------------------------------------------------------------------
# AC-1: Phase stepper rendered in response HTML
# ---------------------------------------------------------------------------

class TestLivePageHtml:
    """Integration tests for GET /runs/{run_id}/live HTML rendering."""

    def _get_live_html(self, client: TestClient) -> str:
        resp = client.get(f"/runs/{RUN_ID}/live")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        return resp.text

    def test_page_returns_200(self, client: TestClient):
        resp = client.get(f"/runs/{RUN_ID}/live")
        assert resp.status_code == 200

    def test_404_for_unknown_run(self, client: TestClient):
        resp = client.get("/runs/nonexistent_run_id/live")
        assert resp.status_code == 404

    def test_phase_stepper_present(self, client: TestClient):
        """AC-1: Phase stepper container is present in the HTML."""
        html = self._get_live_html(client)
        assert "phase-stepper" in html

    def test_phase_stepper_shows_completed_class(self, client: TestClient):
        """AC-1: Completed phases have phase-step-completed CSS class."""
        html = self._get_live_html(client)
        assert "phase-step-completed" in html

    def test_phase_stepper_shows_running_class(self, client: TestClient):
        """AC-1: Running (current) phase has phase-step-running CSS class."""
        html = self._get_live_html(client)
        assert "phase-step-running" in html

    def test_phase_stepper_shows_pending_class(self, client: TestClient):
        """AC-1: Pending phases have phase-step-pending CSS class."""
        html = self._get_live_html(client)
        # Pending is expected for phases after 'architecture' in feature_development
        assert "phase-step-pending" in html

    def test_phase_stepper_checkmark_for_completed(self, client: TestClient):
        """AC-1: Completed phase circles contain an SVG checkmark."""
        html = self._get_live_html(client)
        # The SVG path we embed for checkmarks
        assert "M2 7l3.5 3.5L12 4" in html

    def test_phase_stepper_pulse_for_running(self, client: TestClient):
        """AC-1: Running phase contains the pulse-dot span."""
        html = self._get_live_html(client)
        assert "phase-pulse-dot" in html

    # ---- AC-2: Auto-scroll / scroll-to-bottom ----

    def test_scroll_to_bottom_button_present(self, client: TestClient):
        """AC-2: Floating 'Scroll to Bottom' button exists in the page."""
        html = self._get_live_html(client)
        assert "scroll-btn" in html
        assert "scroll-to-bottom-btn" in html

    def test_auto_scroll_js_logic_present(self, client: TestClient):
        """AC-2: JS contains auto-scroll logic (isNearBottom / scrollToBottom)."""
        html = self._get_live_html(client)
        assert "autoScroll" in html
        assert "scrollToBottom" in html
        assert "isNearBottom" in html

    # ---- AC-3: Prompt card ----

    def test_prompt_card_present(self, client: TestClient):
        """AC-3: Prompt card element exists in the page."""
        html = self._get_live_html(client)
        assert 'id="prompt-card"' in html

    def test_prompt_card_has_agent_name_slot(self, client: TestClient):
        """AC-3: Prompt card contains agent name placeholder."""
        html = self._get_live_html(client)
        assert 'id="prompt-agent-name"' in html

    def test_prompt_card_has_question_slot(self, client: TestClient):
        """AC-3: Prompt card contains question text placeholder."""
        html = self._get_live_html(client)
        assert 'id="prompt-question"' in html

    def test_prompt_card_has_textarea(self, client: TestClient):
        """AC-3: Prompt card has a response textarea."""
        html = self._get_live_html(client)
        assert 'id="prompt-response"' in html

    def test_prompt_card_has_submit_button(self, client: TestClient):
        """AC-3: Prompt card has a submit button."""
        html = self._get_live_html(client)
        assert 'id="prompt-submit-btn"' in html

    def test_prompt_polling_js_present(self, client: TestClient):
        """AC-3: JS contains prompt polling logic."""
        html = self._get_live_html(client)
        assert "checkPendingPrompt" in html
        assert "submitPromptResponse" in html

    # ---- AC-4: Cancel and Resume buttons ----

    def test_cancel_button_present(self, client: TestClient):
        """AC-4: Cancel Run button is in the sidebar."""
        html = self._get_live_html(client)
        assert 'id="cancel-btn"' in html
        assert "Cancel Run" in html

    def test_resume_button_present(self, client: TestClient):
        """AC-4: Resume Run button is in the sidebar."""
        html = self._get_live_html(client)
        assert 'id="resume-btn"' in html
        assert "Resume Run" in html

    def test_cancel_visible_when_running(self, workspace: Path):
        """AC-4: Cancel button is visible when run status is 'running'."""
        # Default fixture state is 'running' — cancel button should not have display:none
        app = _make_dashboard_app(workspace)
        cl = TestClient(app, raise_server_exceptions=False)
        html = cl.get(f"/runs/{RUN_ID}/live").text
        # The cancel button should not have display:none inline style when status=running
        import re
        cancel_block = re.search(r'id="cancel-btn"[^>]*>', html)
        assert cancel_block is not None
        assert "display:none" not in cancel_block.group(0)

    def test_resume_hidden_when_running(self, workspace: Path):
        """AC-4: Resume button is hidden when run is active."""
        app = _make_dashboard_app(workspace)
        cl = TestClient(app, raise_server_exceptions=False)
        html = cl.get(f"/runs/{RUN_ID}/live").text
        import re
        resume_block = re.search(r'id="resume-btn"[^>]*>', html)
        assert resume_block is not None
        assert "display:none" in resume_block.group(0)

    def test_resume_visible_when_cancelled(self, tmp_path: Path):
        """AC-4: Resume button is shown when run status is 'cancelled'."""
        ws = tmp_path / "ws"
        ws.mkdir()
        _write_state_file(ws, RUN_ID, _minimal_state(RUN_ID, status="cancelled"))
        app = _make_dashboard_app(tmp_path)
        cl = TestClient(app, raise_server_exceptions=False)
        html = cl.get(f"/runs/{RUN_ID}/live").text
        import re
        resume_block = re.search(r'id="resume-btn"[^>]*>', html)
        assert resume_block is not None
        assert "display:none" not in resume_block.group(0)

    # ---- AC-5: Phase stepper updates on SSE events ----

    def test_update_phase_step_js_present(self, client: TestClient):
        """AC-5: JS contains the updatePhaseStep() function."""
        html = self._get_live_html(client)
        assert "updatePhaseStep" in html

    def test_phase_transition_handled_in_sse_handler(self, client: TestClient):
        """AC-5: SSE onmessage handler processes 'phase_transition' events."""
        html = self._get_live_html(client)
        assert "phase_transition" in html
        assert "updatePhaseStep" in html

    def test_data_phase_attribute_on_step_elements(self, client: TestClient):
        """AC-5: Phase step elements carry data-phase attribute for JS targeting."""
        html = self._get_live_html(client)
        assert 'data-phase=' in html

    # ---- General ----

    def test_sidebar_shows_run_id(self, client: TestClient):
        html = self._get_live_html(client)
        assert RUN_ID in html

    def test_sidebar_shows_cost(self, client: TestClient):
        html = self._get_live_html(client)
        assert "sidebar-cost" in html

    def test_sidebar_shows_workflow_type(self, client: TestClient):
        html = self._get_live_html(client)
        assert "feature_development" in html

    def test_sse_stream_url_in_html(self, client: TestClient):
        """The SSE EventSource URL is present in the page script."""
        html = self._get_live_html(client)
        assert "/stream" in html

    def test_live_layout_class_present(self, client: TestClient):
        """Two-column layout wrapper is present."""
        html = self._get_live_html(client)
        assert "live-layout" in html

    def test_metadata_sidebar_element_present(self, client: TestClient):
        html = self._get_live_html(client)
        assert "live-sidebar" in html


# ---------------------------------------------------------------------------
# AC-6: workflow_phases context injection
# ---------------------------------------------------------------------------

class TestWorkflowPhasesContextInjection:
    """Verify that the route handler passes workflow_phases to the template."""

    def test_phases_rendered_as_steps(self, client: TestClient):
        """Each phase from the state file appears as a step in the HTML."""
        resp = client.get(f"/runs/{RUN_ID}/live")
        html = resp.text
        # 'ux_specification' phase key should appear as data-phase attribute
        assert 'data-phase="ux_specification"' in html

    def test_prd_phase_rendered(self, client: TestClient):
        resp = client.get(f"/runs/{RUN_ID}/live")
        assert 'data-phase="prd"' in resp.text

    def test_architecture_phase_rendered(self, client: TestClient):
        resp = client.get(f"/runs/{RUN_ID}/live")
        assert 'data-phase="architecture"' in resp.text

    def test_run_id_injected_into_js(self, client: TestClient):
        """RUN_ID constant is injected into the page JS."""
        resp = client.get(f"/runs/{RUN_ID}/live")
        assert RUN_ID in resp.text
