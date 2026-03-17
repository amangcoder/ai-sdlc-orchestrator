"""Audit artifact registration consistency across config, models, and schemas."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import yaml

from orchestrator.models import ARTIFACT_MODELS
from orchestrator.validation import SCHEMAS_DIR

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "config" / "default.yaml"


def audit_artifacts(config_path: Path | None = None) -> list[str]:
    """Return list of inconsistency messages. Empty means all clear."""
    issues: list[str] = []

    if config_path is None:
        config_path = _DEFAULT_CONFIG
    with open(config_path) as f:
        config = yaml.safe_load(f)

    # Collect all output artifacts declared in config
    config_artifacts: set[str] = set()
    for agent_key, agent_cfg in config.get("agents", {}).items():
        for art in agent_cfg.get("output_artifacts", []):
            config_artifacts.add(art)

    model_artifacts = set(ARTIFACT_MODELS.keys())
    schema_artifacts = {p.stem.replace(".schema", "") for p in SCHEMAS_DIR.glob("*.schema.json")}

    # Check 1: config artifacts missing from ARTIFACT_MODELS
    for art in sorted(config_artifacts - model_artifacts):
        issues.append(f"Config output_artifact '{art}' has no ARTIFACT_MODELS entry")

    # Check 2: config artifacts missing schemas
    for art in sorted(config_artifacts - schema_artifacts):
        issues.append(f"Config output_artifact '{art}' has no schema file")

    # Check 3: ARTIFACT_MODELS entries missing schemas
    for art in sorted(model_artifacts - schema_artifacts):
        issues.append(f"ARTIFACT_MODELS entry '{art}' has no schema file")

    # Check 4: schema files missing ARTIFACT_MODELS entries
    for art in sorted(schema_artifacts - model_artifacts):
        issues.append(f"Schema file '{art}.schema.json' has no ARTIFACT_MODELS entry")

    return issues


if __name__ == "__main__":
    issues = audit_artifacts()
    if issues:
        print(f"Found {len(issues)} artifact consistency issue(s):")
        for issue in issues:
            print(f"  - {issue}")
        sys.exit(1)
    else:
        print("All artifact registrations are consistent.")
