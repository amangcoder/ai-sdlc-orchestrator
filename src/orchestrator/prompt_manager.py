"""File-based prompt/response IPC for interactive orchestration.

Provides a mechanism for the orchestrator engine to pause at clarification
checkpoints, write a prompt file, and poll for a user response delivered
via the mobile API.

File conventions:
  - .prompt-{run_id}.json  — written by the engine when a question is posed
  - .response-{run_id}.json — written by the mobile API when the user responds

The engine (not the mobile API) owns the prompt file lifecycle.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def write_prompt(
    run_id: str,
    workspace: Path,
    question: str,
    prompt_type: str,
    options: list[str] | None = None,
) -> str:
    """Write a prompt file for the given run, requesting user input.

    Uses atomic write (temp-file + os.replace()) to prevent partial reads
    by the mobile API or WebSocket watcher.

    Args:
        run_id: The active run's identifier.
        workspace: Path to the workspace directory.
        question: The question text to present to the user.
        prompt_type: Either 'free_text' or 'single_choice'.
        options: List of option strings (required for single_choice).

    Returns:
        The generated prompt_id (UUID v4 hex string).
    """
    prompt_id = uuid.uuid4().hex
    payload = {
        "prompt_id": prompt_id,
        "question": question,
        "type": prompt_type,
        "options": options,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    prompt_file = workspace / f".prompt-{run_id}.json"
    tmp_file = workspace / f".prompt-{run_id}.json.tmp"

    try:
        tmp_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(str(tmp_file), str(prompt_file))
    except OSError as exc:
        logger.error("Failed to write prompt file for run %s: %s", run_id, exc)
        raise

    logger.info("Wrote prompt %s for run %s: %s", prompt_id, run_id, question[:80])
    return prompt_id


def poll_for_response(
    run_id: str,
    workspace: Path,
    prompt_id: str,
    timeout_seconds: int = 600,
    poll_interval: float = 2.0,
) -> str | None:
    """Poll for a user response to a pending prompt.

    Blocks until .response-{run_id}.json appears containing a matching
    prompt_id, or until the timeout elapses.

    Args:
        run_id: The active run's identifier.
        workspace: Path to the workspace directory.
        prompt_id: The prompt_id to match in the response file.
        timeout_seconds: Maximum seconds to wait (default 600 = 10 minutes).
        poll_interval: Seconds between filesystem checks (default 2.0).

    Returns:
        The user's response string, or None if the timeout elapsed.
    """
    response_file = workspace / f".response-{run_id}.json"
    deadline = time.monotonic() + timeout_seconds

    while time.monotonic() < deadline:
        if response_file.exists():
            try:
                data = json.loads(response_file.read_text(encoding="utf-8"))
                if data.get("prompt_id") == prompt_id:
                    response = data.get("response", "")
                    logger.info(
                        "Received response for prompt %s on run %s",
                        prompt_id,
                        run_id,
                    )
                    return response
            except (json.JSONDecodeError, OSError) as exc:
                logger.debug(
                    "Error reading response file for run %s: %s", run_id, exc
                )

        time.sleep(poll_interval)

    logger.warning(
        "Timeout waiting for response to prompt %s on run %s (waited %ds)",
        prompt_id,
        run_id,
        timeout_seconds,
    )
    return None


def cleanup_prompt_files(run_id: str, workspace: Path) -> None:
    """Remove prompt and response files for a completed/cancelled run.

    Safe to call even if files do not exist.

    Args:
        run_id: The run identifier.
        workspace: Path to the workspace directory.
    """
    for pattern in (f".prompt-{run_id}.json", f".response-{run_id}.json"):
        filepath = workspace / pattern
        try:
            filepath.unlink(missing_ok=True)
        except OSError as exc:
            logger.debug("Could not remove %s: %s", filepath, exc)
