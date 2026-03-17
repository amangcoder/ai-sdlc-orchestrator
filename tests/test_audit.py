"""Regression test: ensure config, models, and schemas stay in sync."""

from orchestrator.audit import audit_artifacts


def test_artifact_consistency():
    issues = audit_artifacts()
    assert issues == [], f"Artifact registration mismatches:\n" + "\n".join(f"  - {i}" for i in issues)
