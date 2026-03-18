"""Config REST router — read and update orchestrator YAML configuration.

Endpoints:
  GET /api/v1/config                  — read config with sensitive field redaction
  PUT /api/v1/config {updates: dict}  — update config with backup and validation

Security:
  - All GET responses redact any key containing 'key', 'token', 'secret', 'password'
  - PUT rejects updates containing sensitive field names
  - PUT strips '***REDACTED***' values before merging (prevents secret destruction)
  - workspace_dir update to dangerous system paths is rejected

Write sequence (on valid PUT):
  1. Validate: reject sensitive fields, policy caps, strip redacted values
  2. Read current YAML
  3. Deep-merge updates onto current config
  4. Apply policy cap checks
  5. Validate merged config with OrchestratorConfig(**merged)
  6. Write timestamped backup
  7. Write new config YAML
"""

from __future__ import annotations

import copy
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from orchestrator.mobile_api.models import (
    ConfigResponse,
    ConfigUpdateRequest,
    ConfigUpdateResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# Sensitive field patterns (case-insensitive substring match in key name)
_SENSITIVE_PATTERNS = frozenset({"key", "token", "secret", "password"})


def _is_sensitive_key(key: str) -> bool:
    """Return True if the key name contains any sensitive pattern."""
    key_lower = key.lower()
    return any(pattern in key_lower for pattern in _SENSITIVE_PATTERNS)


def _redact_sensitive(
    config: dict[str, Any],
    prefix: str = "",
) -> tuple[dict[str, Any], list[str]]:
    """Recursively redact sensitive fields in a config dict.

    Args:
        config: The configuration dictionary to redact.
        prefix: Dotted path prefix for nested keys (used in redacted_keys list).

    Returns:
        A tuple of (redacted_dict, list_of_redacted_key_paths).
    """
    result: dict[str, Any] = {}
    redacted_keys: list[str] = []

    for key, value in config.items():
        full_key = f"{prefix}.{key}" if prefix else key

        if _is_sensitive_key(key):
            # Redact this field
            result[key] = "***REDACTED***"
            redacted_keys.append(full_key)
        elif isinstance(value, dict):
            # Recurse into nested dicts
            sub_result, sub_keys = _redact_sensitive(value, full_key)
            result[key] = sub_result
            redacted_keys.extend(sub_keys)
        else:
            result[key] = value

    return result, redacted_keys


def _strip_redacted_values(updates: dict[str, Any]) -> dict[str, Any]:
    """Recursively strip key-value pairs where value is '***REDACTED***'.

    Prevents clients from accidentally destroying secrets by sending back
    the redacted placeholder value.
    """
    result: dict[str, Any] = {}
    for key, value in updates.items():
        if value == "***REDACTED***":
            continue  # Strip this entry entirely
        elif isinstance(value, dict):
            result[key] = _strip_redacted_values(value)
        else:
            result[key] = value
    return result


def _deep_merge(base: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge `updates` onto `base`, returning a new dict."""
    result = copy.deepcopy(base)
    for key, value in updates.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _check_policy_caps(updates: dict[str, Any]) -> str | None:
    """Check policy caps on specific config fields.

    Returns an error message string if a cap is exceeded, None if all OK.
    """
    if "max_budget_usd" in updates:
        val = updates["max_budget_usd"]
        if isinstance(val, (int, float)) and val > 500:
            return f"max_budget_usd {val} exceeds policy cap of 500"

    if "max_concurrent_agents" in updates:
        val = updates["max_concurrent_agents"]
        if isinstance(val, int) and val > 20:
            return f"max_concurrent_agents {val} exceeds policy cap of 20"

    if "workspace_dir" in updates:
        wd = updates["workspace_dir"]
        if isinstance(wd, str):
            wd_path = Path(wd)
            if wd_path.is_absolute():
                # Block well-known dangerous system paths
                dangerous_prefixes = (
                    "/etc", "/usr", "/bin", "/sbin", "/lib",
                    "/sys", "/proc", "/dev", "/root", "/boot",
                )
                wd_str = str(wd_path)
                for prefix in dangerous_prefixes:
                    if wd_str == prefix or wd_str.startswith(prefix + "/"):
                        return (
                            f"workspace_dir '{wd}' points to a system directory; "
                            "use a project-relative path or a safe absolute path"
                        )

    return None


# ── GET /api/v1/config ────────────────────────────────────────────────────

@router.get("/config")
async def get_config(request: Request):
    """Read orchestrator config with sensitive field redaction.

    Returns {config: dict, redacted_keys: list[str]}.
    All keys containing 'key', 'token', 'secret', or 'password' are replaced
    with '***REDACTED***' in the response.
    """
    config_path: Path | None = getattr(request.app.state, "config_path", None)

    try:
        from orchestrator.config import load_config

        config = load_config(config_path)
        raw_dict = config.model_dump()
    except Exception as exc:
        logger.warning("Failed to load config via Pydantic: %s; falling back to raw YAML", exc)
        # Fall back to raw YAML if Pydantic model fails
        try:
            from orchestrator.config import DEFAULT_CONFIG_PATH

            path = config_path or DEFAULT_CONFIG_PATH
            with open(path) as f:
                raw_dict = yaml.safe_load(f) or {}
        except Exception as exc2:
            return JSONResponse(
                status_code=500,
                content={"error": f"Failed to read config: {exc2}"},
            )

    redacted, redacted_keys = _redact_sensitive(raw_dict)
    return ConfigResponse(config=redacted, redacted_keys=redacted_keys).model_dump()


# ── PUT /api/v1/config ────────────────────────────────────────────────────

@router.put("/config")
async def update_config(update_req: ConfigUpdateRequest, request: Request):
    """Update orchestrator config with validation and timestamped backup.

    Write sequence:
      1. Strip '***REDACTED***' values from incoming updates
      2. Reject any updates containing sensitive field names (key/token/secret/password)
      3. Read current YAML
      4. Deep-merge updates onto current config
      5. Check policy caps
      6. Validate merged config via OrchestratorConfig(**merged)
      7. Write timestamped backup
      8. Write updated YAML
    """
    config_path: Path | None = getattr(request.app.state, "config_path", None)

    from orchestrator.config import DEFAULT_CONFIG_PATH

    live_path = config_path or DEFAULT_CONFIG_PATH

    updates = update_req.updates

    # Step 1: Strip ***REDACTED*** values to prevent secret destruction
    updates = _strip_redacted_values(updates)

    # Step 2: Reject sensitive field names in updates
    for key in updates.keys():
        if _is_sensitive_key(key):
            return JSONResponse(
                status_code=422,
                content={
                    "error": "Cannot update sensitive fields via API",
                    "detail": f"Key '{key}' contains a sensitive field pattern",
                },
            )

    # Step 3: Read current YAML
    try:
        with open(live_path) as f:
            current_raw = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError) as exc:
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to read config: {exc}"},
        )

    # Step 4: Deep-merge updates onto current config
    merged = _deep_merge(current_raw, updates)

    # Step 5: Policy cap checks
    cap_error = _check_policy_caps(updates)
    if cap_error:
        return JSONResponse(
            status_code=422,
            content={"error": "Validation failed", "detail": cap_error},
        )

    # Step 6: Validate merged config with OrchestratorConfig
    try:
        from orchestrator.models import OrchestratorConfig

        # Build a minimal valid merged dict for OrchestratorConfig validation
        # We don't construct the full nested models here — just validate top-level fields
        _validate_orchestrator_config(merged)
    except Exception as exc:
        return JSONResponse(
            status_code=422,
            content={"error": "Validation failed", "detail": str(exc)},
        )

    # Step 7: Write timestamped backup (only after validation passes)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    backup_path = live_path.parent / f"{live_path.name}.bak.{timestamp}"
    try:
        backup_path.write_bytes(live_path.read_bytes())
    except OSError as exc:
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to create backup: {exc}"},
        )

    # Step 8: Write updated YAML
    try:
        with open(live_path, "w") as f:
            yaml.dump(merged, f, default_flow_style=False, allow_unicode=True)
    except (OSError, yaml.YAMLError) as exc:
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to write config: {exc}"},
        )

    # Build redacted_keys for the response (what was redacted in current config)
    _, redacted_keys = _redact_sensitive(merged)

    return ConfigUpdateResponse(
        status="updated",
        backup_path=str(backup_path),
        redacted_keys=redacted_keys,
        note="Config changes take effect on the next run start",
    ).model_dump()


def _validate_orchestrator_config(raw: dict[str, Any]) -> None:
    """Validate a raw config dict against OrchestratorConfig.

    Raises ValueError or ValidationError if validation fails.
    """
    from orchestrator.config import load_config as _load_config
    import tempfile
    import os

    # Write to a temp file and load via load_config for full validation
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False, encoding="utf-8"
    ) as tmp:
        yaml.dump(raw, tmp, default_flow_style=False, allow_unicode=True)
        tmp_path = Path(tmp.name)

    try:
        _load_config(tmp_path)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
