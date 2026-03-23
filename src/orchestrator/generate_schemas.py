"""Generate JSON Schema files from Pydantic models."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from orchestrator.models import ARTIFACT_MODELS

SCHEMAS_DIR = Path(__file__).resolve().parents[1] / "schemas"


def generate_missing_schemas() -> list[str]:
    """Generate .schema.json files for any ARTIFACT_MODELS that lack one.

    Returns list of newly generated schema names.
    """
    SCHEMAS_DIR.mkdir(parents=True, exist_ok=True)
    generated = []
    for name, model_cls in ARTIFACT_MODELS.items():
        schema_path = SCHEMAS_DIR / f"{name}.schema.json"
        if not schema_path.exists():
            schema = model_cls.model_json_schema()
            schema_path.write_text(json.dumps(schema, indent=2) + "\n")
            generated.append(name)
    return generated


def regenerate_all_schemas() -> list[str]:
    """Regenerate ALL .schema.json files from Pydantic models, overwriting existing ones.

    Returns list of regenerated schema names.
    """
    SCHEMAS_DIR.mkdir(parents=True, exist_ok=True)
    regenerated = []
    for name, model_cls in ARTIFACT_MODELS.items():
        schema_path = SCHEMAS_DIR / f"{name}.schema.json"
        schema = model_cls.model_json_schema()
        schema_path.write_text(json.dumps(schema, indent=2) + "\n")
        regenerated.append(name)
    return regenerated


if __name__ == "__main__":
    if "--regenerate" in sys.argv:
        result = regenerate_all_schemas()
        print(f"Regenerated {len(result)} schemas: {', '.join(result)}")
    else:
        result = generate_missing_schemas()
        if result:
            print(f"Generated {len(result)} schemas: {', '.join(result)}")
        else:
            print("All schemas already exist.")
