"""Schema validation for inter-agent artifacts."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema
from pydantic import ValidationError

from orchestrator.models import ARTIFACT_MODELS

logger = logging.getLogger(__name__)

SCHEMAS_DIR = Path(__file__).resolve().parents[1] / "schemas"


@dataclass
class ValidationResult:
    """Result of validating an artifact."""

    valid: bool
    errors: list[str]
    warnings: list[str] = field(default_factory=list)
    artifact_name: str = ""
    schema_errors: list[str] = field(default_factory=list)
    pydantic_errors: list[str] = field(default_factory=list)


def _camel_to_snake(name: str) -> str:
    """Convert camelCase, PascalCase, or kebab-case to snake_case."""
    # Handle kebab-case
    name = name.replace("-", "_")
    # Insert underscores before uppercase runs
    s1 = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
    return re.sub(r"([a-z\d])([A-Z])", r"\1_\2", s1).lower()


def normalize_artifact_keys(data: Any) -> Any:
    """Recursively normalize all dict keys to snake_case.

    Handles camelCase, PascalCase, and kebab-case keys so that
    agents producing non-snake_case output still pass validation.
    """
    if isinstance(data, dict):
        return {_camel_to_snake(k): normalize_artifact_keys(v) for k, v in data.items()}
    if isinstance(data, list):
        return [normalize_artifact_keys(item) for item in data]
    return data


# ---------------------------------------------------------------------------
# Coercion: convert rich LLM output to schema-expected types
# ---------------------------------------------------------------------------

def _coerce_data_flow(value: Any) -> Any:
    """Coerce a list-of-objects data_flow into a prose string."""
    if not isinstance(value, list):
        return value
    parts: list[str] = []
    for entry in value:
        if isinstance(entry, dict):
            src = entry.get("from", "?")
            dst = entry.get("to", "?")
            data = entry.get("data", "")
            trigger = entry.get("trigger", "")
            line = f"{src} → {dst}: {data}"
            if trigger:
                line += f" (trigger: {trigger})"
            parts.append(line)
        else:
            parts.append(str(entry))
    return "\n".join(parts)


def _coerce_directory_structure(value: Any) -> Any:
    """Coerce a flat list of path strings into a nested dir_node dict."""
    if not isinstance(value, list):
        return value
    tree: dict[str, Any] = {}
    for path in value:
        if not isinstance(path, str):
            continue
        segments = [s for s in path.split("/") if s]
        node = tree
        for i, seg in enumerate(segments):
            if i == len(segments) - 1:
                node.setdefault(seg, "")
            else:
                if seg not in node or not isinstance(node.get(seg), dict):
                    node[seg] = {}
                node = node[seg]
    return tree


def _coerce_testing_strategy(value: Any) -> Any:
    """Coerce a dict testing_strategy into a labeled prose string."""
    if not isinstance(value, dict):
        return value
    parts: list[str] = []
    for key, desc in value.items():
        label = key.replace("_", " ").title()
        if isinstance(desc, str):
            parts.append(f"{label}: {desc}")
        else:
            parts.append(f"{label}: {json.dumps(desc)}")
    return "\n".join(parts)


# Registry of artifact-specific field coercers
_COERCION_RULES: dict[str, dict[str, Any]] = {
    "architecture": {
        "data_flow": _coerce_data_flow,
        "directory_structure": _coerce_directory_structure,
    },
    "engineering_plan": {
        "testing_strategy": _coerce_testing_strategy,
    },
}


def _get_schema_string_fields(artifact_name: str) -> set[str]:
    """Return top-level property names where the schema declares type=string."""
    schema_path = SCHEMAS_DIR / f"{artifact_name}.schema.json"
    if not schema_path.exists():
        return set()
    try:
        with open(schema_path) as f:
            schema = json.load(f)
    except (json.JSONDecodeError, OSError):
        return set()
    result: set[str] = set()
    for prop_name, prop_def in schema.get("properties", {}).items():
        if prop_def.get("type") == "string":
            result.add(prop_name)
    return result


def coerce_artifact_data(data: dict, artifact_name: str) -> dict:
    """Coerce rich LLM output to match schema-expected types.

    Two mechanisms:
    1. Artifact-specific coercers for known problematic fields.
    2. Generic fallback: if schema declares a field as string but the
       value is a dict or list, JSON-serialize it.
    """
    if not isinstance(data, dict):
        return data

    data = dict(data)  # shallow copy to avoid mutating caller's data

    # Apply artifact-specific coercers
    rules = _COERCION_RULES.get(artifact_name, {})
    for field_name, coercer in rules.items():
        if field_name in data:
            data[field_name] = coercer(data[field_name])

    # Generic fallback: JSON-serialize any dict/list in a string-typed field
    string_fields = _get_schema_string_fields(artifact_name)
    for field_name in string_fields:
        if field_name in data and isinstance(data[field_name], (dict, list)):
            # Only apply if no specific coercer already handled it
            if field_name not in rules:
                data[field_name] = json.dumps(data[field_name], indent=2)

    return data


def validate_artifact_file(
    artifact_path: Path, artifact_name: str, *, auto_normalize: bool = True,
) -> ValidationResult:
    """Validate an artifact file against its JSON schema and Pydantic model.

    Performs two-layer validation:
    1. JSON Schema validation (structural)
    2. Pydantic model validation (semantic)

    When *auto_normalize* is True (the default), dict keys are normalized
    to snake_case before validation so that camelCase/kebab-case variants
    from agents are accepted transparently.  If normalization changes the
    data the file on disk is rewritten with the corrected keys.
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

    # Normalize keys before validation (handles camelCase/kebab-case from agents)
    if auto_normalize and isinstance(data, dict):
        normalized = normalize_artifact_keys(data)
        if normalized != data:
            logger.info(
                "Auto-normalized keys in %s (camelCase/kebab-case → snake_case)",
                artifact_path.name,
            )
            data = normalized
            # Rewrite file with corrected keys so downstream consumers see clean data
            try:
                artifact_path.write_text(json.dumps(data, indent=2))
            except OSError:
                pass

    # Coerce rich LLM output to match schema-expected types
    coerced = coerce_artifact_data(data, artifact_name)
    if coerced != data:
        logger.info("Coerced fields in %s to match schema types", artifact_path.name)
        data = coerced
        try:
            artifact_path.write_text(json.dumps(data, indent=2))
        except OSError:
            pass

    # Layer 1: JSON Schema validation
    schema_errs, schema_warnings = _validate_json_schema(data, artifact_name)
    errors.extend(schema_errs)

    # Layer 2: Pydantic model validation
    pydantic_errs = _validate_pydantic(data, artifact_name)
    errors.extend(pydantic_errs)

    return ValidationResult(
        valid=len(errors) == 0,
        errors=errors,
        warnings=schema_warnings,
        artifact_name=artifact_name,
        schema_errors=schema_errs,
        pydantic_errors=pydantic_errs,
    )


def _validate_json_schema(data: dict, artifact_name: str) -> tuple[list[str], list[str]]:
    """Validate data against the JSON schema file.

    Returns a tuple of (errors, warnings).  When no schema file exists the
    missing-schema message is returned as a *warning* instead of an error so
    that Pydantic validation can still gate the artifact.
    """
    schema_path = SCHEMAS_DIR / f"{artifact_name}.schema.json"
    if not schema_path.exists():
        msg = f"No JSON schema found for artifact: {artifact_name} — falling back to Pydantic validation only"
        logger.warning(msg)
        return [], [msg]

    with open(schema_path) as f:
        schema = json.load(f)

    validator = jsonschema.Draft202012Validator(schema)
    errors = [
        f"Schema: {err.message} (at {'.'.join(str(p) for p in err.absolute_path)})"
        if err.absolute_path
        else f"Schema: {err.message}"
        for err in validator.iter_errors(data)
    ]
    return errors, []


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
