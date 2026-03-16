"""Schema validation for inter-agent artifacts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import jsonschema
from pydantic import ValidationError

from orchestrator.models import ARTIFACT_MODELS

SCHEMAS_DIR = Path(__file__).resolve().parents[1] / "schemas"


@dataclass
class ValidationResult:
    """Result of validating an artifact."""

    valid: bool
    errors: list[str]
    artifact_name: str


def validate_artifact_file(artifact_path: Path, artifact_name: str) -> ValidationResult:
    """Validate an artifact file against its JSON schema and Pydantic model.

    Performs two-layer validation:
    1. JSON Schema validation (structural)
    2. Pydantic model validation (semantic)
    """
    errors: list[str] = []

    if not artifact_path.exists():
        return ValidationResult(
            valid=False,
            errors=[f"Artifact file not found: {artifact_path}"],
            artifact_name=artifact_name,
        )

    try:
        with open(artifact_path) as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        return ValidationResult(
            valid=False,
            errors=[f"Invalid JSON: {e}"],
            artifact_name=artifact_name,
        )

    # Layer 1: JSON Schema validation
    schema_errors = _validate_json_schema(data, artifact_name)
    errors.extend(schema_errors)

    # Layer 2: Pydantic model validation
    pydantic_errors = _validate_pydantic(data, artifact_name)
    errors.extend(pydantic_errors)

    return ValidationResult(
        valid=len(errors) == 0,
        errors=errors,
        artifact_name=artifact_name,
    )


def _validate_json_schema(data: dict, artifact_name: str) -> list[str]:
    """Validate data against the JSON schema file."""
    schema_path = SCHEMAS_DIR / f"{artifact_name}.schema.json"
    if not schema_path.exists():
        return [f"No JSON schema found for artifact: {artifact_name}"]

    with open(schema_path) as f:
        schema = json.load(f)

    validator = jsonschema.Draft202012Validator(schema)
    return [
        f"Schema: {err.message} (at {'.'.join(str(p) for p in err.absolute_path)})"
        if err.absolute_path
        else f"Schema: {err.message}"
        for err in validator.iter_errors(data)
    ]


def _validate_pydantic(data: dict, artifact_name: str) -> list[str]:
    """Validate data against the Pydantic model."""
    model_cls = ARTIFACT_MODELS.get(artifact_name)
    if model_cls is None:
        return []

    try:
        model_cls.model_validate(data)
        return []
    except ValidationError as e:
        return [f"Model: {err['msg']} (at {'.'.join(str(l) for l in err['loc'])})" for err in e.errors()]


def validate_all_artifacts(artifacts_dir: Path, expected: list[str]) -> dict[str, ValidationResult]:
    """Validate all expected artifacts in a directory."""
    results = {}
    for name in expected:
        artifact_path = artifacts_dir / f"{name}.json"
        results[name] = validate_artifact_file(artifact_path, name)
    return results
