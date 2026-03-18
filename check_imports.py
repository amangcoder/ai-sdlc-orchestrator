#!/usr/bin/env python3
"""Check imports work correctly."""
import os
import sys

# Set the env var required by auth module
os.environ["ORCHESTRATOR_API_KEY"] = "test-key"

# Add src to path
sys.path.insert(0, "/Users/amangupta/Projects/orchestrator-for-mobile-ui/src")

errors = []

try:
    from orchestrator.mobile_api.routes.config import _redact_sensitive
    print("OK: _redact_sensitive importable from config")
except Exception as e:
    errors.append(f"FAIL: _redact_sensitive: {e}")
    print(f"FAIL: _redact_sensitive: {e}")

try:
    from orchestrator.mobile_api.routes.artifacts import resolve_artifact_path
    print("OK: resolve_artifact_path importable from artifacts")
except Exception as e:
    errors.append(f"FAIL: resolve_artifact_path: {e}")
    print(f"FAIL: resolve_artifact_path: {e}")

try:
    from orchestrator.mobile_api.app import create_mobile_app
    print("OK: create_mobile_app importable")
except Exception as e:
    errors.append(f"FAIL: create_mobile_app: {e}")
    print(f"FAIL: create_mobile_app: {e}")

try:
    from orchestrator.mobile_api.auth import verify_token, AuthMiddleware
    print("OK: auth module imports fine")
except Exception as e:
    errors.append(f"FAIL: auth: {e}")
    print(f"FAIL: auth: {e}")

try:
    from orchestrator.models import ARTIFACT_MODELS
    print(f"OK: ARTIFACT_MODELS has {len(ARTIFACT_MODELS)} entries: {list(ARTIFACT_MODELS.keys())[:5]}")
except Exception as e:
    errors.append(f"FAIL: ARTIFACT_MODELS: {e}")
    print(f"FAIL: ARTIFACT_MODELS: {e}")

try:
    from orchestrator.mobile_api.routes.websocket import _validate_ws_token
    print("OK: _validate_ws_token importable from websocket")
except Exception as e:
    errors.append(f"FAIL: _validate_ws_token: {e}")
    print(f"FAIL: _validate_ws_token: {e}")

if errors:
    print(f"\n{len(errors)} ERRORS found")
    sys.exit(1)
else:
    print("\nAll imports OK")
