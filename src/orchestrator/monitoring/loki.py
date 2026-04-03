"""Background Loki log shipper — batches enriched log events and pushes via HTTP POST.

The :class:`LokiLogShipper` runs a daemon thread that collects log events into a
thread-safe queue and flushes them to Loki on a configurable interval (default 1 s)
or when the batch reaches *batch_size* events (default 100), whichever comes first.

Usage::

    from orchestrator.monitoring.loki import LokiLogShipper

    shipper = LokiLogShipper("http://localhost:3100", auth_token="my-token")
    shipper.push({"event": "agent_result", "run_id": "abc", "level": "info"})
    # shutdown() is called automatically via atexit
"""

from __future__ import annotations

import atexit
import json
import logging
import queue
import re
import threading
import time
from typing import Any, Dict, List, Optional

from orchestrator.monitoring.config import validate_url

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional httpx import (preferred HTTP client)
# ---------------------------------------------------------------------------

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False
    httpx = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Log field scrubber
# ---------------------------------------------------------------------------

# Matches common secret key-value patterns: api_key=VALUE, password=VALUE, etc.
_SECRET_KV_PATTERN = re.compile(
    r"(?i)\b(api[_-]?key|password|passwd|secret|token|auth[_-]?token)\s*[=:]\s*\S+",
)

# Matches SECRET_* environment variable assignments: SECRET_FOO=bar
_SECRET_VAR_PATTERN = re.compile(
    r"(?i)(SECRET_\w+)\s*[=:]\s*\S*",
)

# Matches file paths under /home/ or /tmp/
_FILE_PATH_PATTERN = re.compile(r"(/home/|/tmp/)[^\s,\"'>\]\)]+")

# Detects the start of a Python stack trace
_STACK_TRACE_MARKER = re.compile(r"Traceback \(most recent call last\):")

# Maximum length for any single string field
_MAX_FIELD_LEN = 4096
# Maximum stack trace lines retained before truncation
_MAX_STACK_LINES = 3


def _scrub_string(value: str) -> str:
    """Scrub a single string value of stack traces, file paths, and secrets.

    Order of operations:
    1. Stack traces → truncate to first ``_MAX_STACK_LINES`` lines + marker
    2. File paths under ``/home/`` or ``/tmp/`` → ``[path redacted]``
    3. Secret key=value patterns → ``<key>=[REDACTED]``
    4. ``SECRET_*`` variable assignments → ``SECRET_NAME=[REDACTED]``
    5. Truncate to ``_MAX_FIELD_LEN`` characters
    """
    # 1. Truncate stack traces
    if _STACK_TRACE_MARKER.search(value):
        lines = value.splitlines()
        kept = lines[:_MAX_STACK_LINES]
        kept.append("[stack trace truncated by LokiLogShipper]")
        value = "\n".join(kept)

    # 2. Redact file paths
    value = _FILE_PATH_PATTERN.sub("[path redacted]", value)

    # 3. Redact secret key=value pairs (e.g. api_key=abc123, password=secret)
    def _redact_kv(m: re.Match) -> str:  # type: ignore[type-arg]
        # Keep the key name, replace the value
        full = m.group(0)
        sep_match = re.search(r"[=:]", full)
        if sep_match:
            key_part = full[: sep_match.start()]
            sep = full[sep_match.start()]
            return f"{key_part}{sep}[REDACTED]"
        return "[REDACTED]"

    value = _SECRET_KV_PATTERN.sub(_redact_kv, value)

    # 4. Redact SECRET_* variable assignments
    value = _SECRET_VAR_PATTERN.sub(r"\1=[REDACTED]", value)

    # 5. Truncate long values
    if len(value) > _MAX_FIELD_LEN:
        value = value[:_MAX_FIELD_LEN] + "...[truncated]"

    return value


def scrub_event(event: Dict[str, Any]) -> Dict[str, Any]:
    """Return a shallow-copy of *event* with sensitive string fields scrubbed.

    Recursively processes nested dicts and list elements.
    """
    result: Dict[str, Any] = {}
    for key, value in event.items():
        if isinstance(value, dict):
            result[key] = scrub_event(value)
        elif isinstance(value, list):
            result[key] = [
                _scrub_string(v) if isinstance(v, str) else v for v in value
            ]
        elif isinstance(value, str):
            result[key] = _scrub_string(value)
        else:
            result[key] = value
    return result


# ---------------------------------------------------------------------------
# LokiLogShipper
# ---------------------------------------------------------------------------

#: Loki push endpoint path appended to the base *endpoint* URL.
_LOKI_PUSH_PATH = "/loki/api/v1/push"

#: Labels extracted from event dicts and forwarded as Loki stream labels.
#: Includes run_id, phase, agent_name, event_type for structured querying (AC-020, REQ-025).
#: Legacy aliases (event→event_type, agent→agent_name) are resolved at push time.
_STREAM_LABEL_FIELDS = ("run_id", "event", "phase", "agent", "agent_name", "event_type", "level")

#: Maximum Loki label value length (Loki rejects very long label values).
_MAX_LABEL_LEN = 64


class LokiLogShipper:
    """Background daemon thread that batches enriched log events and pushes to Loki.

    The shipper uses a thread-safe :class:`queue.Queue` internally.  :meth:`push`
    is designed to be called from any thread with ≤ 1 ms latency (non-blocking).

    Batches are flushed every *flush_interval* seconds **or** when *batch_size*
    events accumulate, whichever comes first.

    ``httpx`` is used for HTTP POSTs when available; ``urllib.request`` is the
    fallback.  Both honour a 5-second timeout.  If Loki is unreachable the
    error is logged at WARNING level and the shipper continues — no exception
    is raised to callers.

    An :func:`atexit` handler is registered so that remaining events are flushed
    on clean process exit.

    Parameters
    ----------
    endpoint:
        Base URL of the Loki instance, e.g. ``"http://localhost:3100"``.
        Validated with :func:`~orchestrator.monitoring.config.validate_url`
        (private networks are **allowed** by default for docker/dev setups).
    flush_interval:
        Seconds between automatic flushes (default ``1.0``).
    batch_size:
        Maximum number of events per batch; triggers an early flush when
        reached (default ``100``).
    auth_token:
        Optional Bearer token forwarded as ``Authorization: Bearer <token>``
        in every HTTP request.
    """

    def __init__(
        self,
        endpoint: str,
        flush_interval: float = 1.0,
        batch_size: int = 100,
        auth_token: Optional[str] = None,
    ) -> None:
        # Validate the endpoint URL; private IPs are allowed (docker/dev envs)
        validate_url(endpoint, allow_private_networks=True)

        self._endpoint = endpoint.rstrip("/")
        self._push_url = f"{self._endpoint}{_LOKI_PUSH_PATH}"
        self._flush_interval = flush_interval
        self._batch_size = batch_size
        self._auth_token = auth_token

        # Unbounded queue — push() will never block; we log a warning on drop
        # only if we explicitly call put_nowait with maxsize set.  Here we keep
        # it unbounded and rely on the flush interval to drain it.
        self._queue: queue.Queue[Dict[str, Any]] = queue.Queue()

        # Signals the background thread to stop
        self._stop_event = threading.Event()

        # Background daemon thread (daemon=True prevents it from blocking exit)
        self._thread = threading.Thread(
            target=self._run,
            name="loki-log-shipper",
            daemon=True,
        )
        self._thread.start()

        # Ensure flush on clean process exit
        atexit.register(self.shutdown)

        logger.debug("LokiLogShipper started — pushing to %s", self._push_url)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def push(self, event: Dict[str, Any]) -> None:
        """Enqueue *event* for asynchronous delivery to Loki.

        Non-blocking — returns in ≤ 1 ms regardless of queue depth.
        If the shipper has already been shut down the event is silently dropped.
        """
        if self._stop_event.is_set():
            return
        self._queue.put_nowait(event)

    def flush(self) -> None:
        """Drain the in-memory queue and synchronously send all buffered events.

        Sends up to *batch_size* events per HTTP request until the queue is empty.
        Safe to call from any thread.
        """
        events: List[Dict[str, Any]] = []
        while True:
            try:
                events.append(self._queue.get_nowait())
            except queue.Empty:
                break
            if len(events) >= self._batch_size:
                self._send(events)
                events = []
        if events:
            self._send(events)

    def shutdown(self) -> None:
        """Stop the background thread and flush all remaining events.

        Idempotent — safe to call multiple times (subsequent calls are no-ops).
        Joins the background thread with a 5-second timeout.
        """
        if self._stop_event.is_set():
            return
        self._stop_event.set()
        # Drain any events that arrived before the stop signal
        self.flush()
        self._thread.join(timeout=5.0)
        logger.debug("LokiLogShipper shutdown complete")

    # ------------------------------------------------------------------
    # Background thread
    # ------------------------------------------------------------------

    def _run(self) -> None:
        """Background thread: collect events and flush on interval or batch size."""
        while not self._stop_event.is_set():
            try:
                events = self._collect_events()
                if events:
                    self._send(events)
            except Exception as exc:
                # Never let an exception kill the background thread
                logger.exception(
                    "LokiLogShipper thread encountered an error (continuing): %s", exc
                )
        # Final flush is handled by shutdown() — do not double-flush here.

    def _collect_events(self) -> List[Dict[str, Any]]:
        """Block until *batch_size* events are queued or *flush_interval* elapses.

        Uses 50 ms polling intervals so the background thread stays responsive
        to :attr:`_stop_event` without busy-waiting.
        """
        events: List[Dict[str, Any]] = []
        deadline = time.monotonic() + self._flush_interval
        _poll = 0.05  # seconds between queue polls

        while len(events) < self._batch_size and not self._stop_event.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                event = self._queue.get(timeout=min(remaining, _poll))
                events.append(event)
            except queue.Empty:
                if time.monotonic() >= deadline:
                    break
                # Still within the interval window — keep polling

        return events

    # ------------------------------------------------------------------
    # Payload construction
    # ------------------------------------------------------------------

    def _build_payload(self, events: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Convert a list of enriched events into a Loki push API payload.

        Payload shape::

            {
                "streams": [
                    {
                        "stream": {"job": "orchestrator", "run_id": "…", …},
                        "values": [["<timestamp_ns>", "<json_line>"], …]
                    },
                    …
                ]
            }

        Events with identical stream labels are grouped into the same stream
        entry to minimise the number of HTTP round-trips.
        """
        # Use an ordered dict of stream_key → stream entry for grouping
        streams: Dict[str, Dict[str, Any]] = {}

        for raw_event in events:
            scrubbed = scrub_event(raw_event)

            # Build Loki stream labels from selected fields
            labels: Dict[str, str] = {"job": "orchestrator"}
            for field in _STREAM_LABEL_FIELDS:
                val = scrubbed.get(field)
                if val and isinstance(val, str):
                    labels[field] = val[:_MAX_LABEL_LEN]

            # Normalise legacy field names → canonical label names (AC-020, REQ-025):
            #   event      → event_type  (if event_type not already present)
            #   agent      → agent_name  (if agent_name not already present)
            #   phase      is read directly from the event dict
            if "event_type" not in labels and "event" in labels:
                labels["event_type"] = labels["event"]
            if "agent_name" not in labels and "agent" in labels:
                labels["agent_name"] = labels["agent"]
            # Ensure phase label defaults to empty string placeholder when absent
            # (Loki streams with missing phase will have no phase label — that's fine)

            # Stable grouping key for identical label sets
            stream_key = json.dumps(labels, sort_keys=True)

            # Nanosecond timestamp required by Loki
            ts_ns = self._get_timestamp_ns(scrubbed)

            # Full event serialised as the log line
            line = json.dumps(scrubbed, default=str)

            if stream_key not in streams:
                streams[stream_key] = {"stream": labels, "values": []}
            streams[stream_key]["values"].append([str(ts_ns), line])

        return {"streams": list(streams.values())}

    @staticmethod
    def _get_timestamp_ns(event: Dict[str, Any]) -> int:
        """Return a nanosecond UNIX timestamp from *event* or fall back to now."""
        ts = event.get("timestamp") or event.get("ts") or event.get("time")
        if isinstance(ts, (int, float)):
            # Heuristic: values < 1e12 are assumed to be seconds; larger are ns
            if ts < 1_000_000_000_000:
                return int(ts * 1_000_000_000)
            return int(ts)
        return int(time.time() * 1_000_000_000)

    # ------------------------------------------------------------------
    # HTTP transport
    # ------------------------------------------------------------------

    def _send(self, events: List[Dict[str, Any]]) -> None:
        """Serialize *events* and POST them to Loki; swallow connection errors."""
        if not events:
            return

        payload = self._build_payload(events)
        body = json.dumps(payload).encode("utf-8")
        headers: Dict[str, str] = {"Content-Type": "application/json"}
        if self._auth_token:
            headers["Authorization"] = f"Bearer {self._auth_token}"

        if HAS_HTTPX:
            self._send_httpx(body, headers)
        else:
            self._send_urllib(body, headers)

    def _send_httpx(self, body: bytes, headers: Dict[str, str]) -> None:
        """POST via ``httpx`` (preferred — connection pooling, HTTP/2 support)."""
        try:
            response = httpx.post(  # type: ignore[union-attr]
                self._push_url,
                content=body,
                headers=headers,
                timeout=5.0,
                follow_redirects=False,
            )
            if response.status_code not in (200, 204):
                logger.warning(
                    "Loki push returned HTTP %d: %s",
                    response.status_code,
                    response.text[:200],
                )
        except Exception as exc:
            logger.warning("Loki unreachable (httpx): %s", exc)

    def _send_urllib(self, body: bytes, headers: Dict[str, str]) -> None:
        """POST via ``urllib.request`` (fallback when httpx is not installed)."""
        import urllib.error
        import urllib.request

        req = urllib.request.Request(
            self._push_url,
            data=body,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                status = resp.getcode()
                if status not in (200, 204):
                    logger.warning("Loki push returned HTTP %d", status)
        except urllib.error.HTTPError as exc:
            logger.warning("Loki push HTTP error: %d %s", exc.code, exc.reason)
        except Exception as exc:
            logger.warning("Loki unreachable (urllib): %s", exc)
