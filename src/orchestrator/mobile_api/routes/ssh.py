"""SSH configuration probe endpoint for the Mobile API.

Endpoints:
  GET /api/v1/ssh/config  — probe SSH daemon reachability (no auth required)

This endpoint is AUTH-EXEMPT — it must be added to the _EXEMPT_PATHS set in
auth.py and/or excluded via the middleware before being called.

SECURITY:
  - No credentials, usernames, private keys, or sensitive server info are
    ever included in the response.
  - The TCP probe connects only to localhost (not to an arbitrary host
    supplied by the client).
  - A short timeout prevents the endpoint from hanging on unreachable ports.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Request

from orchestrator.mobile_api.models import SshConfigResponse

logger = logging.getLogger(__name__)

router = APIRouter()

# Timeout for the SSH port TCP probe in seconds
_SSH_PROBE_TIMEOUT_SECONDS = 2.0


# ── GET /api/v1/ssh/config ────────────────────────────────────────────────


@router.get("/ssh/config", response_model=SshConfigResponse)
async def get_ssh_config(request: Request):
    """Return SSH configuration status and host reachability.

    Performs a non-blocking TCP connect to localhost:ssh_port with a 2-second
    timeout to check if the SSH daemon is listening.

    Returns HTTP 200 in all cases — host_reachable=false when the port is
    unreachable or timed out (never a 5xx error).

    No authentication is required for this endpoint.
    No credentials or sensitive data are included in the response.
    """
    config = getattr(request.app.state, "config", None)

    # Determine whether SSH / projects_root is explicitly configured
    ssh_port: int = 22
    configured: bool = False

    if config is not None:
        ssh_port = getattr(config, "ssh_port", 22)
        projects_root = getattr(config, "projects_root", None)
        # Consider "configured" when either projects_root or a non-default ssh_port is set
        configured = (projects_root is not None) or (ssh_port != 22)

    # Probe the SSH port via non-blocking TCP connect to localhost
    host_reachable = await _probe_tcp_port("127.0.0.1", ssh_port, _SSH_PROBE_TIMEOUT_SECONDS)

    return SshConfigResponse(
        configured=configured,
        host_reachable=host_reachable,
    )


async def _probe_tcp_port(host: str, port: int, timeout: float) -> bool:
    """Attempt a non-blocking TCP connection to host:port.

    Returns True if the connection succeeds within the timeout,
    False otherwise (connection refused, timeout, network error).

    SECURITY: Only connects to the specified host — callers must ensure
    this is localhost or an explicitly trusted address.
    """
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=timeout,
        )
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass  # Best-effort close; ignore errors
        return True
    except (asyncio.TimeoutError, ConnectionRefusedError, OSError):
        return False
    except Exception as exc:
        logger.debug("SSH probe to %s:%d failed: %s", host, port, exc)
        return False
