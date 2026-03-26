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

from orchestrator.models import ARTIFACT_MODELS, _VERDICT_NORMALIZE

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


def _fix_invalid_json_escapes(raw: str) -> str:
    r"""Fix invalid backslash escape sequences in JSON strings.

    LLMs sometimes produce escapes like ``\e``, ``\s``, ``\a`` etc. that
    are not valid in JSON.  This doubles the backslash so the literal
    character is preserved (e.g. ``\e`` → ``\\e``).

    The regex consumes *valid* escape sequences first (``\\``, ``\n``,
    ``\uXXXX``, etc.) so that already-doubled backslashes are not
    corrupted.  Without this two-alternative approach the naïve regex
    ``\\(?![...])`` would match the second backslash in a ``\\s``
    sequence and turn it into ``\\\s`` (still invalid).
    """
    return re.sub(
        r'\\(\\|["\\/bfnrt]|u[0-9a-fA-F]{4})|\\(.)',
        lambda m: m.group(0) if m.group(1) is not None else '\\\\' + m.group(2),
        raw,
    )


def _normalize_artifact_values(data: dict, artifact_name: str) -> dict:
    """Normalize artifact-specific values before validation.

    Handles common LLM output variations that are semantically correct
    but don't match the exact enum values in the Pydantic models.
    For example, review verdicts "pass"/"fail" → "approve"/"reject".
    """
    if artifact_name == "review" and "verdict" in data:
        original = data["verdict"]
        mapped = _VERDICT_NORMALIZE.get(original)
        if mapped:
            data = {**data, "verdict": mapped}
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
    except json.JSONDecodeError:
        # LLMs often produce invalid escape sequences — try to fix them
        try:
            raw = artifact_path.read_text()
            fixed = _fix_invalid_json_escapes(raw)
            data = json.loads(fixed)
            logger.info(
                "Auto-fixed invalid escape sequences in %s", artifact_path.name
            )
            artifact_path.write_text(fixed)
        except (json.JSONDecodeError, OSError) as e:
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

    # Normalize artifact-specific values (e.g. review verdict "pass"→"approve")
    if auto_normalize and isinstance(data, dict):
        fixed = _normalize_artifact_values(data, artifact_name)
        if fixed != data:
            logger.info("Auto-normalized values in %s", artifact_path.name)
            data = fixed
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
