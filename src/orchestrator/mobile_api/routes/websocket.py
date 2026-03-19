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
from pathlib import Path

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from orchestrator.mobile_api.auth import verify_token
from orchestrator.mobile_api.models import _RUN_ID_RE, PROMPT_FILE_PATTERN

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


def _sanitize_ws_prompt(raw: dict) -> dict:
    """Sanitize prompt content for WebSocket emission.

    Reuses the same logic as the REST endpoint sanitizer:
    - Truncates question to 500 chars
    - Truncates each option to 100 chars
    - Strips Unicode bidi override chars
    """
    import re
    bidi_re = re.compile(r"[\u202a-\u202e\u2066-\u2069]")

    sanitized = dict(raw)

    question = str(sanitized.get("question", ""))
    question = bidi_re.sub("", question)[:500]
    sanitized["question"] = question

    options = sanitized.get("options")
    if options is not None and isinstance(options, list):
        sanitized["options"] = [
            bidi_re.sub("", str(opt))[:100] for opt in options
        ]

    return sanitized


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

    Security:
      - run_id is validated against _RUN_ID_RE before accept()
      - after_line is clamped to max(0, value)
    """
    # Validate run_id format before accepting the connection
    if not _RUN_ID_RE.match(run_id):
        # Close with 4001 to signal invalid run_id
        await websocket.accept()
        try:
            await websocket.close(code=4001)
        except RuntimeError:
            pass
        return

    # Clamp after_line to non-negative
    after_line = max(0, after_line)

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

    # Prompt file watcher state
    last_seen_prompt_id: str | None = None
    # Counter for prompt poll cadence (check every ~2 seconds = 4 poll cycles at 0.5s)
    _prompt_poll_counter = 0
    _PROMPT_POLL_EVERY = 4  # check prompt file every 4 iterations (2 seconds)

    workspace = reader.workspace

    # Resolve the project workspace for this run (subprocess runs write
    # prompt files to the project dir, not the app workspace).
    _project_prompt_dir: Path | None = None
    try:
        _state_path = workspace / f"state-{run_id}.json"
        if _state_path.exists():
            _run_state = json.loads(_state_path.read_text(encoding="utf-8"))
            _ws_id = _run_state.get("workspace_id")
            if _ws_id is not None:
                from orchestrator.mobile_api.directory_service import resolve_workspace_id
                frozen_dir_map: dict = websocket.app.state.frozen_dir_map
                resolved = resolve_workspace_id(_ws_id, frozen_dir_map)
                if resolved is None:
                    projects_root = getattr(websocket.app.state, "projects_root", None)
                    if projects_root is not None:
                        from orchestrator.mobile_api.dynamic_directory_service import resolve_dynamic_id
                        salt = websocket.app.state.directory_salt
                        resolved = resolve_dynamic_id(_ws_id, projects_root, salt)
                _project_prompt_dir = resolved
    except (json.JSONDecodeError, OSError, AttributeError):
        pass

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

            # ── Prompt file watcher (every ~2 seconds) ────────────────────
            _prompt_poll_counter += 1
            if _prompt_poll_counter >= _PROMPT_POLL_EVERY:
                _prompt_poll_counter = 0
                # Search app workspace first, then project workspace
                prompt_file = workspace / PROMPT_FILE_PATTERN.format(run_id=run_id)
                if not prompt_file.exists() and _project_prompt_dir is not None:
                    prompt_file = _project_prompt_dir / PROMPT_FILE_PATTERN.format(run_id=run_id)
                if prompt_file.exists():
                    try:
                        prompt_data = json.loads(
                            prompt_file.read_text(encoding="utf-8")
                        )
                        prompt_id = prompt_data.get("prompt_id")
                        if (
                            prompt_id
                            and prompt_id != last_seen_prompt_id
                        ):
                            # Check if run is still active: in-process tracker
                            # OR subprocess state file (subprocess runs aren't
                            # tracked by RunTracker).
                            tracker = websocket.app.state.tracker
                            run_active = tracker.is_active(run_id)
                            if not run_active:
                                try:
                                    _st = json.loads(
                                        (workspace / f"state-{run_id}.json")
                                        .read_text(encoding="utf-8")
                                    )
                                    run_active = _st.get("status") == "running"
                                except (json.JSONDecodeError, OSError):
                                    pass
                            if run_active:
                                sanitized = _sanitize_ws_prompt(prompt_data)
                                prompt_event = {
                                    "event": "prompt_pending",
                                    "run_id": run_id,
                                    "prompt": {
                                        "prompt_id": sanitized.get("prompt_id", ""),
                                        "question": sanitized.get("question", ""),
                                        "type": sanitized.get("type", "free_text"),
                                        "options": sanitized.get("options"),
                                        "created_at": sanitized.get("created_at", ""),
                                    },
                                }
                                try:
                                    await websocket.send_text(
                                        json.dumps(prompt_event)
                                    )
                                    last_send_time = asyncio.get_event_loop().time()
                                except (WebSocketDisconnect, RuntimeError):
                                    return
                                last_seen_prompt_id = prompt_id
                    except (json.JSONDecodeError, OSError):
                        pass
                else:
                    # Prompt file gone — reset tracking
                    last_seen_prompt_id = None

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
