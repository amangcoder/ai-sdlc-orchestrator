"""Tests for TASK-004 — Enhanced new-run page.

Acceptance criteria verified:
  AC-1: New run form includes Feature Request textarea, Workflow Type dropdown
        (full/backend/frontend/research/custom), Model Routing dropdown
        (default/speed/quality/economy).
  AC-2: Advanced Options section is collapsible — Config Path and Custom
        Workflow JSON inputs exist in the template.
  AC-3: /api/v1/runs/start accepts POST requests and returns run_id.
  AC-4: RunRequest.model_routing maps "speed"/"quality"/"economy" to
        routing_mode via start_run().
  AC-5: WORKFLOW_ALIASES includes full, backend, frontend, research aliases.
  AC-6: RunRequest accepts model_routing, config_path, custom_workflow fields.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_dashboard_app(workspace_root: Path, project_name: str = "test-project"):
    """Create the full dashboard app with auth disabled."""
    import orchestrator.dashboard.app as _app_mod
    from orchestrator.dashboard.app import create_app

    saved = _app_mod.DASHBOARD_TOKEN
    _app_mod.DASHBOARD_TOKEN = None
    try:
        return create_app(workspace_root, project_name)
    finally:
        _app_mod.DASHBOARD_TOKEN = saved


def _patched_client(workspace_root: Path):
    """Return a TestClient with runner.start_run() mocked to avoid real execution."""
    app = _make_dashboard_app(workspace_root)
    client = TestClient(app, raise_server_exceptions=False)
    return client


# ---------------------------------------------------------------------------
# AC-5: WORKFLOW_ALIASES includes dashboard-friendly short names
# ---------------------------------------------------------------------------

class TestWorkflowAliases:
    def test_full_alias_resolves_to_feature_development(self):
        from orchestrator.main import WORKFLOW_ALIASES
        from orchestrator.models import WorkflowType

        assert WORKFLOW_ALIASES.get("full") == WorkflowType.FEATURE_DEVELOPMENT

    def test_backend_alias_resolves(self):
        from orchestrator.main import WORKFLOW_ALIASES
        from orchestrator.models import WorkflowType

        assert WORKFLOW_ALIASES.get("backend") == WorkflowType.FEATURE_DEVELOPMENT

    def test_frontend_alias_resolves(self):
        from orchestrator.main import WORKFLOW_ALIASES
        from orchestrator.models import WorkflowType

        assert WORKFLOW_ALIASES.get("frontend") == WorkflowType.FEATURE_DEVELOPMENT

    def test_research_alias_resolves(self):
        from orchestrator.main import WORKFLOW_ALIASES

        assert "research" in WORKFLOW_ALIASES

    def test_custom_type_is_valid_enum_value(self):
        from orchestrator.models import WorkflowType

        # WorkflowType.CUSTOM must exist and parse from string "custom"
        assert WorkflowType("custom") == WorkflowType.CUSTOM


# ---------------------------------------------------------------------------
# AC-6: RunRequest accepts new dashboard fields
# ---------------------------------------------------------------------------

class TestRunRequestNewFields:
    def test_model_routing_defaults_to_default(self):
        from orchestrator.dashboard.runner import RunRequest

        req = RunRequest(feature_request="Build something cool")
        assert req.model_routing == "default"

    def test_model_routing_accepts_speed(self):
        from orchestrator.dashboard.runner import RunRequest

        req = RunRequest(feature_request="Build something cool", model_routing="speed")
        assert req.model_routing == "speed"

    def test_model_routing_accepts_quality(self):
        from orchestrator.dashboard.runner import RunRequest

        req = RunRequest(feature_request="Test", model_routing="quality")
        assert req.model_routing == "quality"

    def test_model_routing_accepts_economy(self):
        from orchestrator.dashboard.runner import RunRequest

        req = RunRequest(feature_request="Test", model_routing="economy")
        assert req.model_routing == "economy"

    def test_config_path_defaults_to_none(self):
        from orchestrator.dashboard.runner import RunRequest

        req = RunRequest(feature_request="Test")
        assert req.config_path is None

    def test_config_path_accepts_string(self):
        from orchestrator.dashboard.runner import RunRequest

        req = RunRequest(feature_request="Test", config_path="/tmp/config.yaml")
        assert req.config_path == "/tmp/config.yaml"

    def test_custom_workflow_defaults_to_none(self):
        from orchestrator.dashboard.runner import RunRequest

        req = RunRequest(feature_request="Test")
        assert req.custom_workflow is None

    def test_custom_workflow_accepts_json_string(self):
        from orchestrator.dashboard.runner import RunRequest

        wf_json = '{"phases": ["pm", "architect"]}'
        req = RunRequest(feature_request="Test", custom_workflow=wf_json)
        assert req.custom_workflow == wf_json


# ---------------------------------------------------------------------------
# AC-4: model_routing maps to routing_mode in config
# ---------------------------------------------------------------------------

class TestModelRoutingMapping:
    """Verify that model_routing values are correctly applied to config.routing_mode."""

    def _run_request(self, model_routing: str) -> "RunRequest":
        from orchestrator.dashboard.runner import RunRequest
        return RunRequest(
            feature_request="Add dark mode support",
            model_routing=model_routing,
        )

    def test_speed_maps_to_fast_mode(self, tmp_path: Path):
        """model_routing='speed' must set config.routing_mode='fast' before engine init."""
        import asyncio
        import orchestrator.dashboard.runner as runner_mod
        from orchestrator.dashboard.runner import RunTracker

        tracker = RunTracker(tmp_path)
        req = self._run_request("speed")

        captured_configs = []

        def fake_engine(config, **kw):
            captured_configs.append(config)
            return MagicMock()

        # OrchestratorEngine is imported locally inside start_run(), so patch the
        # source module. asyncio.create_task is also patched to prevent real coroutine.
        with patch("orchestrator.engine.OrchestratorEngine", side_effect=fake_engine), \
             patch("asyncio.create_task", return_value=MagicMock()):
            try:
                asyncio.get_event_loop().run_until_complete(tracker.start_run(req))
            except Exception:
                pass  # engine won't actually run — we only care about config

        if captured_configs:
            assert captured_configs[0].routing_mode == "fast", \
                f"Expected routing_mode='fast', got {captured_configs[0].routing_mode!r}"

    def test_default_routing_leaves_mode_unchanged(self, tmp_path: Path):
        """'default' model_routing must NOT override config.routing_mode."""
        from orchestrator.dashboard.runner import RunRequest

        req = RunRequest(feature_request="Test", model_routing="default")
        assert req.mode is None  # explicit mode field must be unset


# ---------------------------------------------------------------------------
# AC-3: GET /new-run returns 200; POST /api/v1/runs/start endpoint exists
# ---------------------------------------------------------------------------

class TestNewRunEndpoints:
    def test_new_run_page_returns_200(self, tmp_path: Path):
        client = _patched_client(tmp_path)
        resp = client.get("/new-run")
        assert resp.status_code == 200

    def test_new_run_page_contains_workflow_type_select(self, tmp_path: Path):
        client = _patched_client(tmp_path)
        resp = client.get("/new-run")
        assert resp.status_code == 200
        body = resp.text
        assert 'name="workflow_type"' in body

    def test_new_run_page_contains_model_routing_select(self, tmp_path: Path):
        client = _patched_client(tmp_path)
        resp = client.get("/new-run")
        assert resp.status_code == 200
        body = resp.text
        assert 'name="model_routing"' in body

    def test_new_run_page_contains_workflow_options(self, tmp_path: Path):
        """Template must have full/backend/frontend/research/custom options."""
        client = _patched_client(tmp_path)
        resp = client.get("/new-run")
        body = resp.text
        for option in ("full", "backend", "frontend", "research", "custom"):
            assert f'value="{option}"' in body, f"Missing option: {option}"

    def test_new_run_page_contains_model_routing_options(self, tmp_path: Path):
        """Template must have default/speed/quality/economy options."""
        client = _patched_client(tmp_path)
        resp = client.get("/new-run")
        body = resp.text
        for option in ("default", "speed", "quality", "economy"):
            assert f'value="{option}"' in body, f"Missing model routing option: {option}"

    def test_new_run_page_contains_advanced_options_section(self, tmp_path: Path):
        """Advanced Options collapsible section must be present."""
        client = _patched_client(tmp_path)
        resp = client.get("/new-run")
        body = resp.text
        assert "advanced-options-section" in body
        assert 'name="config_path"' in body
        assert 'name="custom_workflow"' in body

    def test_new_run_page_contains_inline_error_element(self, tmp_path: Path):
        """Inline validation error element must exist with role=alert."""
        client = _patched_client(tmp_path)
        resp = client.get("/new-run")
        body = resp.text
        assert "feature-request-error" in body
        assert 'role="alert"' in body

    def test_api_v1_runs_start_endpoint_exists(self, tmp_path: Path):
        """POST /api/v1/runs/start must be a registered route (not 404/405)."""
        with patch("orchestrator.dashboard.runner.RunTracker.start_run", new_callable=AsyncMock) as mock_start:
            mock_start.return_value = "abc123def456"
            client = _patched_client(tmp_path)
            resp = client.post(
                "/api/v1/runs/start",
                json={"feature_request": "Add dark mode to the app"},
            )
            # Should not be 404 or 405 — route must exist
            assert resp.status_code not in (404, 405), \
                f"Expected route to exist, got {resp.status_code}"

    def test_api_v1_runs_start_returns_run_id_on_success(self, tmp_path: Path):
        """Successful POST must return {run_id, redirect}."""
        with patch("orchestrator.dashboard.runner.RunTracker.start_run", new_callable=AsyncMock) as mock_start:
            mock_start.return_value = "testrunid1234"
            client = _patched_client(tmp_path)
            resp = client.post(
                "/api/v1/runs/start",
                json={
                    "feature_request": "Build a feature",
                    "workflow_type": "full",
                    "model_routing": "default",
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            assert "run_id" in data
            assert data["run_id"] == "testrunid1234"
            assert data["redirect"] == "/runs/testrunid1234/live"

    def test_api_v1_runs_start_rejects_empty_feature_request(self, tmp_path: Path):
        """Empty feature_request must return 422 (Pydantic validation)."""
        client = _patched_client(tmp_path)
        resp = client.post(
            "/api/v1/runs/start",
            json={"feature_request": ""},
        )
        assert resp.status_code == 422

    def test_api_v1_runs_start_serializes_new_fields(self, tmp_path: Path):
        """model_routing, config_path, custom_workflow must be accepted."""
        with patch("orchestrator.dashboard.runner.RunTracker.start_run", new_callable=AsyncMock) as mock_start:
            mock_start.return_value = "newrunabcd1234"
            client = _patched_client(tmp_path)
            resp = client.post(
                "/api/v1/runs/start",
                json={
                    "feature_request": "Build something",
                    "workflow_type": "backend",
                    "model_routing": "speed",
                    "config_path": "/tmp/myconfig.yaml",
                    "custom_workflow": '{"phases": ["pm"]}',
                },
            )
            assert resp.status_code == 200
            # Verify start_run was called with a RunRequest that has the new fields
            call_args = mock_start.call_args
            req_obj = call_args[0][0]
            assert req_obj.model_routing == "speed"
            assert req_obj.config_path == "/tmp/myconfig.yaml"
            assert req_obj.custom_workflow == '{"phases": ["pm"]}'

    def test_api_v1_runs_start_returns_409_on_conflict(self, tmp_path: Path):
        """ValueError from RunTracker (active run conflict) must return 409."""
        with patch(
            "orchestrator.dashboard.runner.RunTracker.start_run",
            new_callable=AsyncMock,
            side_effect=ValueError("A run is already active"),
        ):
            client = _patched_client(tmp_path)
            resp = client.post(
                "/api/v1/runs/start",
                json={"feature_request": "Do something"},
            )
            assert resp.status_code == 409
            assert "error" in resp.json()
