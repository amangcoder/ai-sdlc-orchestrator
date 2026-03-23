"""Tests for two-layer artifact validation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from orchestrator.models import ARTIFACT_MODELS
from orchestrator.validation import (
    SCHEMAS_DIR,
    validate_all_artifacts,
    validate_artifact_file,
)


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


    def test_review_verdict_normalization(self, tmp_workspace):
        """Review artifacts with 'pass'/'fail' verdicts should be auto-normalized."""
        path = tmp_workspace / "artifacts" / "review.json"
        data = {
            "verdict": "pass",
            "issues": [],
            "summary": "All checks passed, code looks good and meets requirements.",
        }
        path.write_text(json.dumps(data))
        result = validate_artifact_file(path, "review")
        assert result.valid, result.errors
        # Verify the file was rewritten with the normalized value
        reloaded = json.loads(path.read_text())
        assert reloaded["verdict"] == "approve"

    def test_review_verdict_fail_normalization(self, tmp_workspace):
        """Review 'fail' verdict should normalize to 'reject'."""
        path = tmp_workspace / "artifacts" / "review.json"
        data = {
            "verdict": "fail",
            "issues": [{"severity": "critical", "description": "Security vulnerability found"}],
            "summary": "Critical issues found that must be addressed before merging.",
        }
        path.write_text(json.dumps(data))
        result = validate_artifact_file(path, "review")
        assert result.valid, result.errors
        reloaded = json.loads(path.read_text())
        assert reloaded["verdict"] == "reject"


class TestSchemaDrift:
    """Ensure on-disk JSON schemas match Pydantic models.

    If these tests fail, run: python -m orchestrator.generate_schemas --regenerate
    """

    @pytest.mark.parametrize("artifact_name", list(ARTIFACT_MODELS.keys()))
    def test_schema_matches_pydantic_model(self, artifact_name: str):
        model_cls = ARTIFACT_MODELS[artifact_name]
        schema_path = SCHEMAS_DIR / f"{artifact_name}.schema.json"
        assert schema_path.exists(), (
            f"No schema file for '{artifact_name}'. "
            f"Run: python -m orchestrator.generate_schemas"
        )
        on_disk = json.loads(schema_path.read_text())
        from_model = model_cls.model_json_schema()
        assert on_disk == from_model, (
            f"Schema drift detected for '{artifact_name}'. On-disk schema does not match "
            f"Pydantic model. Run: python -m orchestrator.generate_schemas --regenerate"
        )


class TestValidateAllArtifacts:
    def test_partial_artifacts(self, tmp_workspace, valid_prd_data):
        # prd present, architecture missing
        prd_path = tmp_workspace / "artifacts" / "prd.json"
        prd_path.write_text(json.dumps(valid_prd_data))

        results = validate_all_artifacts(tmp_workspace / "artifacts", ["prd", "architecture"])
        assert results["prd"].valid
        assert not results["architecture"].valid
