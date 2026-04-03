"""Tests for TASK-016: Mobile API GET /api/v1/artifacts/search endpoint.

Acceptance criteria verified:
  - GET /api/v1/artifacts/search?q=prd returns 200 with results array containing
    artifact_name, run_id, schema, agent, version, updated_at (AC-003, REQ-006)
  - Missing 'q' parameter returns 400
  - Auth required: 401 for unauthenticated requests
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orchestrator.mobile_api.routes.artifact_search import router


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SAMPLE_RESULTS = [
    {
        "name": "prd",
        "run_id": "run-abc123",
        "schema_name": "prd",
        "agent": "pm",
        "current_version": 2,
        "updated_at": "2024-01-15T11:00:00Z",
    },
    {
        "artifact_name": "architecture",
        "run_id": "run-abc123",
        "schema": "architecture",
        "agent": "architect",
        "version": 1,
        "updated_at": "2024-01-15T11:30:00Z",
    },
]


def _make_reader(results: list[dict[str, Any]] | None = None) -> MagicMock:
    reader = MagicMock()
    reader.search_artifacts_global.return_value = results if results is not None else list(_SAMPLE_RESULTS)
    return reader


def _make_app(reader: MagicMock | None = None) -> FastAPI:
    app = FastAPI()
    app.state.reader = reader or _make_reader()
    app.include_router(router, prefix="/api/v1")
    return app


def _client(reader: MagicMock | None = None) -> TestClient:
    return TestClient(_make_app(reader), raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# Happy path tests
# ---------------------------------------------------------------------------

class TestArtifactSearchEndpoint:
    """Tests for GET /api/v1/artifacts/search."""

    def test_returns_200_with_query(self) -> None:
        resp = _client().get("/api/v1/artifacts/search?q=prd")
        assert resp.status_code == 200

    def test_response_has_required_keys(self) -> None:
        resp = _client().get("/api/v1/artifacts/search?q=prd")
        data = resp.json()
        assert "query" in data
        assert "results" in data
        assert "total" in data

    def test_query_echoed_in_response(self) -> None:
        resp = _client().get("/api/v1/artifacts/search?q=prd")
        data = resp.json()
        assert data["query"] == "prd"

    def test_results_count_matches_reader(self) -> None:
        resp = _client().get("/api/v1/artifacts/search?q=prd")
        data = resp.json()
        assert data["total"] == len(_SAMPLE_RESULTS)
        assert len(data["results"]) == len(_SAMPLE_RESULTS)

    def test_result_items_have_required_fields(self) -> None:
        resp = _client().get("/api/v1/artifacts/search?q=prd")
        data = resp.json()
        for result in data["results"]:
            assert "artifact_name" in result, "Missing 'artifact_name'"
            assert "run_id" in result, "Missing 'run_id'"
            assert "schema" in result, "Missing 'schema'"
            assert "agent" in result, "Missing 'agent'"
            assert "version" in result, "Missing 'version'"
            assert "updated_at" in result, "Missing 'updated_at'"

    def test_type_filter_passed_to_reader(self) -> None:
        reader = _make_reader()
        _client(reader).get("/api/v1/artifacts/search?q=prd&type=architecture")
        reader.search_artifacts_global.assert_called_once_with(
            query="prd",
            artifact_type="architecture",
            agent=None,
        )

    def test_agent_filter_passed_to_reader(self) -> None:
        reader = _make_reader()
        _client(reader).get("/api/v1/artifacts/search?q=prd&agent=pm")
        reader.search_artifacts_global.assert_called_once_with(
            query="prd",
            artifact_type=None,
            agent="pm",
        )

    def test_empty_results_returns_200_with_empty_list(self) -> None:
        reader = _make_reader(results=[])
        resp = _client(reader).get("/api/v1/artifacts/search?q=nonexistent")
        assert resp.status_code == 200
        data = resp.json()
        assert data["results"] == []
        assert data["total"] == 0

    def test_missing_q_returns_400(self) -> None:
        resp = _client().get("/api/v1/artifacts/search")
        assert resp.status_code == 400
        data = resp.json()
        assert "error" in data

    def test_empty_q_returns_400(self) -> None:
        resp = _client().get("/api/v1/artifacts/search?q=")
        assert resp.status_code == 400

    def test_q_too_long_returns_400(self) -> None:
        long_query = "x" * 300
        resp = _client().get(f"/api/v1/artifacts/search?q={long_query}")
        assert resp.status_code == 400
        assert "error" in resp.json()

    def test_reader_exception_returns_500(self) -> None:
        reader = MagicMock()
        reader.search_artifacts_global.side_effect = RuntimeError("Search backend error")
        resp = _client(reader).get("/api/v1/artifacts/search?q=prd")
        assert resp.status_code == 500
        assert "error" in resp.json()

    def test_alternate_field_names_normalized(self) -> None:
        """Results using 'artifact_name'/'schema'/'version' are handled."""
        results = [{
            "artifact_name": "prd",
            "run_id": "run-001",
            "schema": "prd",
            "agent": "pm",
            "version": 3,
            "updated_at": "2024-01-01T00:00:00Z",
        }]
        reader = _make_reader(results)
        resp = _client(reader).get("/api/v1/artifacts/search?q=prd")
        assert resp.status_code == 200
        data = resp.json()
        item = data["results"][0]
        assert item["artifact_name"] == "prd"
        assert item["version"] == 3


# ---------------------------------------------------------------------------
# Auth tests
# ---------------------------------------------------------------------------

class TestArtifactSearchAuth:
    """Verify auth requirement on full mobile app."""

    def test_returns_401_without_token(self, tmp_path: "Path") -> None:
        import os
        os.environ.setdefault("ORCHESTRATOR_API_KEY", "test-artifact-search-key-xN8q")

        from orchestrator.mobile_api.app import create_mobile_app
        app = create_mobile_app(workspace_dir=tmp_path)

        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/api/v1/artifacts/search?q=prd")
        assert resp.status_code == 401
