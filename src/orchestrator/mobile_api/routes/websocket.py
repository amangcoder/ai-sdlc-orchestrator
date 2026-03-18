"""WebSocket streaming endpoint for real-time run event delivery.

Protocol:
  1. Client connects to WS /api/v1/runs/{run_id}/stream?after_line=N
  2. Server accepts the connection (websocket.accept())
  3. Server awaits first frame (max 5s timeout) — must be auth JSON:
       {type: 'auth', token: str, after_line?: int}
  4. Invalid/missing token → close with code 4001 (no data frames before close)
  5. Valid auth → stream JSONL events starting from after_line
  6. Heartbeat {event: 'heartbeat', ts: ISO8601} after 20s of no new events
  7. run_complete event → send stream_end frame → close code 1000

Security: API key MUST NOT appear in the WebSocket URL.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from orchestrator.mobile_api.auth import verify_token

logger = logging.getLogger(__name__)

router = APIRouter()

# Heartbeat interval (seconds of idle time before sending keepalive)
_HEARTBEAT_INTERVAL = 20.0

# Auth timeout (seconds to wait for first frame before closing)
_AUTH_TIMEOUT = 5.0

# Polling interval between JSONL checks
_POLL_INTERVAL = 0.5


def _validate_ws_token(token: str) -> bool:
    """Validate WebSocket auth token using hmac.compare_digest.

    Exported for testing (test_auth_uses_hmac_compare_digest_not_string_equality
    imports this function by name to verify timing-safe comparison is used).
    """
    return verify_token(token)


@router.websocket("/runs/{run_id}/stream")
async def ws_stream(
    websocket: WebSocket,
    run_id: str,
    after_line: int = Query(default=0),
) -> None:
    """Stream run events to the connected WebSocket client.

    First-frame auth protocol:
      - await websocket.accept() first (FastAPI/Starlette requirement)
      - Receive first text frame within 5s (asyncio.wait_for + receive_text)
      - Parse JSON, extract 'token' key
      - Validate with hmac.compare_digest via verify_token()
      - Invalid/timeout → close(4001) and return immediately
    """
    reader = websocket.app.state.reader

    # Step 1: Accept the WebSocket connection (required before any other operation)
    await websocket.accept()

    # Step 2: Wait for auth frame with timeout
    try:
        raw_frame = await asyncio.wait_for(
            websocket.receive_text(),
            timeout=_AUTH_TIMEOUT,
        )
    except (asyncio.TimeoutError, Exception):
        # Timeout or connection error — close with auth failure code
        try:
            await websocket.close(code=4001)
        except RuntimeError:
            pass
        return

    # Step 3: Parse the auth frame JSON
    try:
        auth_data = json.loads(raw_frame)
    except (json.JSONDecodeError, ValueError):
        try:
            await websocket.close(code=4001)
        except RuntimeError:
            pass
        return

    # Step 4: Validate the token
    token = auth_data.get("token", "")
    if not _validate_ws_token(token):
        try:
            await websocket.close(code=4001)
        except RuntimeError:
            pass
        return

    # Step 5: Determine starting line from auth frame or URL query param
    cursor = int(auth_data.get("after_line", after_line))

    # Step 6: Streaming loop
    last_send_time = asyncio.get_event_loop().time()

    while True:
        try:
            # Poll for new events
            events = reader.tail_events(run_id, after_line=cursor)

            has_run_complete = False
            run_complete_event: dict | None = None

            for event in events:
                try:
                    await websocket.send_text(json.dumps(event))
                except (WebSocketDisconnect, RuntimeError):
                    return

                cursor += 1
                last_send_time = asyncio.get_event_loop().time()

                # Check for run completion
                if event.get("event") == "run_complete":
                    has_run_complete = True
                    run_complete_event = event

            # If run completed, send stream_end and close
            if has_run_complete and run_complete_event is not None:
                stream_end = {
                    "event": "stream_end",
                    "run_id": run_complete_event.get("run_id", run_id),
                    "final_status": run_complete_event.get("status", "completed"),
                }
                try:
                    await websocket.send_text(json.dumps(stream_end))
                    await websocket.close(1000)
                except (WebSocketDisconnect, RuntimeError):
                    pass
                return

            # Check if heartbeat is needed (20s of no sent events)
            elapsed = asyncio.get_event_loop().time() - last_send_time
            if elapsed >= _HEARTBEAT_INTERVAL:
                heartbeat = {
                    "event": "heartbeat",
                    "ts": datetime.now(timezone.utc).isoformat(),
                }
                try:
                    await websocket.send_text(json.dumps(heartbeat))
                except (WebSocketDisconnect, RuntimeError):
                    return
                last_send_time = asyncio.get_event_loop().time()

            # Wait before next poll
            await asyncio.sleep(_POLL_INTERVAL)

        except asyncio.CancelledError:
            # Re-raise CancelledError — never catch and suppress it
            raise
        except WebSocketDisconnect:
            return
        except Exception as exc:
            logger.debug("WebSocket streaming error for run %s: %s", run_id, exc)
            return
