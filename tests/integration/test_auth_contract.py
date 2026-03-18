"""Integration tests: Auth Middleware ↔ API Endpoint Boundary.

Boundary tested: HTTP client presents (or omits) Authorization header
→ AuthMiddleware validates → endpoint reached or 401 returned.

Contract assertions:
  1. Valid Bearer token → request passes through to handler
  2. Missing Authorization header → 401 {error: 'Unauthorized'}
  3. Wrong token value → 401 {error: 'Unauthorized'}
  4. Wrong scheme ('Token' not 'Bearer') → 401
  5. Empty Bearer value → 401
  6. /health endpoint bypasses auth entirely
  7. /docs endpoint bypasses auth entirely
  8. Server startup fails (RuntimeError) when ORCHESTRATOR_API_KEY env var is unset
  9. verify_token() uses hmac.compare_digest, not string equality
"""

from __future__ import annotations

import hmac
import importlib
import os
from unittest.mock import patch

import pytest

from .conftest import TEST_API_KEY, WRONG_API_KEY


class TestAuthMiddlewareContract:
    """AuthMiddleware correctly gates all /api/v1/* paths."""

    async def test_valid_bearer_token_grants_access(self, client):
        """Valid Bearer token → handler reached → 200 (not 401)."""
        response = await client.get("/api/v1/runs")
        assert response.status_code != 401, (
            "Valid Bearer token should NOT be rejected by auth middleware"
        )

    async def test_missing_authorization_header_returns_401(self, unauthed_client):
        """Completely absent Authorization header → 401."""
        response = await unauthed_client.get("/api/v1/runs")
        assert response.status_code == 401
        body = response.json()
        assert "error" in body
        assert body["error"] == "Unauthorized"

    async def test_wrong_token_returns_401(self, unauthed_client):
        """Correct scheme but wrong key value → 401."""
        response = await unauthed_client.get(
            "/api/v1/runs",
            headers={"Authorization": f"Bearer {WRONG_API_KEY}"},
        )
        assert response.status_code == 401
        assert response.json()["error"] == "Unauthorized"

    async def test_wrong_scheme_returns_401(self, unauthed_client):
        """'Token' scheme instead of 'Bearer' → 401 (scheme-sensitive)."""
        response = await unauthed_client.get(
            "/api/v1/runs",
            headers={"Authorization": f"Token {TEST_API_KEY}"},
        )
        assert response.status_code == 401

    async def test_empty_bearer_value_returns_401(self, unauthed_client):
        """'Bearer ' with no token following → 401."""
        response = await unauthed_client.get(
            "/api/v1/runs",
            headers={"Authorization": "Bearer "},
        )
        assert response.status_code == 401

    async def test_401_response_has_expected_json_shape(self, unauthed_client):
        """401 response body must be JSON with exactly {error: string}."""
        response = await unauthed_client.get("/api/v1/runs")
        assert response.headers["content-type"].startswith("application/json")
        body = response.json()
        assert isinstance(body, dict)
        assert set(body.keys()) >= {"error"}
        assert isinstance(body["error"], str)


class TestAuthExemptPaths:
    """Certain paths must bypass the auth middleware entirely."""

    async def test_health_endpoint_accessible_without_token(self, unauthed_client):
        """/health returns 200 without any Authorization header."""
        response = await unauthed_client.get("/health")
        assert response.status_code == 200

    async def test_health_response_shape(self, unauthed_client):
        """/health response has {status, version, active_runs} structure."""
        response = await unauthed_client.get("/health")
        body = response.json()
        assert body["status"] == "ok"
        assert "version" in body
        assert "active_runs" in body
        assert isinstance(body["active_runs"], int)

    async def test_docs_endpoint_accessible_without_token(self, unauthed_client):
        """/docs (Swagger UI) is reachable without auth."""
        response = await unauthed_client.get("/docs")
        # FastAPI serves 200 or 307 (redirect) for /docs — both are non-401
        assert response.status_code != 401

    async def test_openapi_json_accessible_without_token(self, unauthed_client):
        """/openapi.json schema is reachable without auth (supports /docs)."""
        response = await unauthed_client.get("/openapi.json")
        assert response.status_code != 401


class TestAuthFailClosedGuarantee:
    """Server must refuse to start if ORCHESTRATOR_API_KEY is not set."""

    def test_missing_api_key_env_raises_runtime_error(self):
        """
        Importing orchestrator.mobile_api.auth with ORCHESTRATOR_API_KEY unset
        must raise RuntimeError immediately — never silently disable auth.

        This is the 'fail-closed' guarantee: the server is safer to crash
        than to run without authentication.
        """
        original = os.environ.pop("ORCHESTRATOR_API_KEY", None)
        try:
            import orchestrator.mobile_api.auth as auth_mod

            with pytest.raises(RuntimeError, match="ORCHESTRATOR_API_KEY"):
                importlib.reload(auth_mod)
        finally:
            # Restore env var so other tests keep working
            if original is not None:
                os.environ["ORCHESTRATOR_API_KEY"] = original
            else:
                os.environ["ORCHESTRATOR_API_KEY"] = TEST_API_KEY
            # Re-reload with key present so module state is clean
            import orchestrator.mobile_api.auth as auth_mod
            importlib.reload(auth_mod)

    def test_api_key_restored_after_fail_closed_test(self):
        """Sanity check: env var is still present after the fail-closed test."""
        assert os.environ.get("ORCHESTRATOR_API_KEY") == TEST_API_KEY


class TestTimingSafeTokenValidation:
    """verify_token() must use hmac.compare_digest, not string equality."""

    def test_verify_token_calls_hmac_compare_digest(self):
        """
        Patch hmac.compare_digest and assert it is invoked by verify_token().
        This guards against developers accidentally using == which is
        vulnerable to timing side-channel attacks.
        """
        from orchestrator.mobile_api.auth import verify_token

        with patch("hmac.compare_digest", wraps=hmac.compare_digest) as mock_cd:
            verify_token(TEST_API_KEY)
            mock_cd.assert_called_once()

    def test_verify_token_returns_true_for_correct_key(self):
        """verify_token(correct_key) == True."""
        from orchestrator.mobile_api.auth import verify_token

        assert verify_token(TEST_API_KEY) is True

    def test_verify_token_returns_false_for_wrong_key(self):
        """verify_token(wrong_key) == False."""
        from orchestrator.mobile_api.auth import verify_token

        assert verify_token(WRONG_API_KEY) is False

    def test_verify_token_returns_false_for_empty_string(self):
        """verify_token('') == False (empty token is not valid)."""
        from orchestrator.mobile_api.auth import verify_token

        assert verify_token("") is False


class TestAuthAcrossAllProtectedEndpoints:
    """All /api/v1/* routes must require auth — no accidental exemptions."""

    @pytest.mark.parametrize("method,path", [
        ("GET",  "/api/v1/runs"),
        ("POST", "/api/v1/runs"),
        ("GET",  "/api/v1/runs/nonexistent/artifacts"),
        ("GET",  "/api/v1/config"),
        ("PUT",  "/api/v1/config"),
    ])
    async def test_protected_endpoint_without_token_returns_401(
        self, unauthed_client, method: str, path: str
    ):
        """Every /api/v1/* path without auth returns 401."""
        response = await unauthed_client.request(method, path)
        assert response.status_code == 401, (
            f"{method} {path} should require authentication but returned {response.status_code}"
        )
