"""Tests for two-layer artifact validation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from orchestrator.validation import validate_all_artifacts, validate_artifact_file


class TestValidateArtifactFile:
    def test_missing_file(self, tmp_workspace):
        result = validate_artifact_file(tmp_workspace / "artifacts" / "prd.json", "prd")
        assert not result.valid
        assert any("not found" in e for e in result.errors)

    def test_invalid_json(self, tmp_workspace):
        path = tmp_workspace / "artifacts" / "prd.json"
        path.write_text("{invalid json}")
        result = validate_artifact_file(path, "prd")
        assert not result.valid
        assert any("Invalid JSON" in e for e in result.errors)

    def test_valid_prd(self, tmp_workspace, valid_prd_data):
        path = tmp_workspace / "artifacts" / "prd.json"
        path.write_text(json.dumps(valid_prd_data))
        result = validate_artifact_file(path, "prd")
        assert result.valid, result.errors

    def test_missing_required_field(self, tmp_workspace, valid_prd_data):
        del valid_prd_data["title"]
        path = tmp_workspace / "artifacts" / "prd.json"
        path.write_text(json.dumps(valid_prd_data))
        result = validate_artifact_file(path, "prd")
        assert not result.valid

    def test_short_overview_pydantic_fails(self, tmp_workspace, valid_prd_data):
        valid_prd_data["overview"] = "Too short"
        path = tmp_workspace / "artifacts" / "prd.json"
        path.write_text(json.dumps(valid_prd_data))
        result = validate_artifact_file(path, "prd")
        assert not result.valid
        assert any("Model:" in e for e in result.errors)

    def test_unknown_artifact_name(self, tmp_workspace):
        path = tmp_workspace / "artifacts" / "unknown.json"
        path.write_text("{}")
        result = validate_artifact_file(path, "unknown")
        # Unknown artifacts now produce a warning (not error) and fall back to Pydantic
        assert result.valid
        assert any("No JSON schema" in w for w in result.warnings)


class TestValidateAllArtifacts:
    def test_partial_artifacts(self, tmp_workspace, valid_prd_data):
        # prd present, architecture missing
        prd_path = tmp_workspace / "artifacts" / "prd.json"
        prd_path.write_text(json.dumps(valid_prd_data))

        results = validate_all_artifacts(tmp_workspace / "artifacts", ["prd", "architecture"])
        assert results["prd"].valid
        assert not results["architecture"].valid
