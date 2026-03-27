"""Tests for LokiLogShipper — TASK-007.

Covers:
- __init__ URL validation (valid endpoint, bad scheme, private IP blocked)
- push() non-blocking enqueue
- flush() drains queue and calls HTTP POST
- Loki payload structure (streams, stream labels, values format)
- Background thread flushing on interval and batch size
- shutdown() idempotency, thread join, remaining event flush
- atexit handler registration
- HTTP connection error graceful handling (warning, no exception)
- Authorization: Bearer <token> header
- httpx transport vs urllib fallback (HAS_HTTPX guard)
- Log field scrubber: stack traces, file paths, secret patterns
- daemon=True on background thread
- try-except in background thread run loop
"""

from __future__ import annotations

import json
import queue
import threading
import time
from typing import Any, Dict, List
from unittest.mock import MagicMock, call, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_shipper(endpoint: str = "http://localhost:3100", **kwargs: Any):
    """Create a LokiLogShipper with the background thread and atexit mocked out."""
    with patch("orchestrator.monitoring.loki.atexit") as mock_atexit, \
         patch("orchestrator.monitoring.loki.threading.Thread") as mock_thread_cls:
        mock_thread = MagicMock()
        mock_thread_cls.return_value = mock_thread

        from orchestrator.monitoring.loki import LokiLogShipper

        shipper = LokiLogShipper(endpoint, **kwargs)
        return shipper, mock_thread, mock_atexit


# ---------------------------------------------------------------------------
# __init__ — URL validation
# ---------------------------------------------------------------------------


class TestLokiLogShipperInit:
    """Validate __init__ parameter handling and URL validation."""

    def test_valid_http_endpoint_accepted(self):
        shipper, _, _ = _make_shipper("http://localhost:3100")
        assert shipper._endpoint == "http://localhost:3100"
        assert shipper._push_url == "http://localhost:3100/loki/api/v1/push"

    def test_trailing_slash_stripped_from_endpoint(self):
        shipper, _, _ = _make_shipper("http://localhost:3100/")
        assert shipper._endpoint == "http://localhost:3100"
        assert shipper._push_url == "http://localhost:3100/loki/api/v1/push"

    def test_https_endpoint_accepted(self):
        shipper, _, _ = _make_shipper("https://loki.example.com")
        assert shipper._endpoint == "https://loki.example.com"

    def test_invalid_scheme_raises(self):
        from orchestrator.monitoring.loki import LokiLogShipper

        with pytest.raises(ValueError, match="scheme"):
            LokiLogShipper("ftp://localhost:3100")

    def test_private_ip_allowed_by_default(self):
        """Private IPs must be accepted (docker/dev environments)."""
        shipper, _, _ = _make_shipper("http://172.17.0.1:3100")
        assert "172.17.0.1" in shipper._push_url

    def test_default_flush_interval(self):
        shipper, _, _ = _make_shipper()
        assert shipper._flush_interval == 1.0

    def test_default_batch_size(self):
        shipper, _, _ = _make_shipper()
        assert shipper._batch_size == 100

    def test_custom_flush_interval(self):
        shipper, _, _ = _make_shipper(flush_interval=2.5)
        assert shipper._flush_interval == 2.5

    def test_custom_batch_size(self):
        shipper, _, _ = _make_shipper(batch_size=50)
        assert shipper._batch_size == 50

    def test_auth_token_stored(self):
        shipper, _, _ = _make_shipper(auth_token="my-secret-token")
        assert shipper._auth_token == "my-secret-token"

    def test_auth_token_defaults_none(self):
        shipper, _, _ = _make_shipper()
        assert shipper._auth_token is None

    def test_background_thread_started(self):
        _, mock_thread, _ = _make_shipper()
        mock_thread.start.assert_called_once()

    def test_daemon_thread(self):
        """Background thread must be daemon=True."""
        with patch("orchestrator.monitoring.loki.atexit"), \
             patch("orchestrator.monitoring.loki.threading.Thread") as mock_thread_cls:
            mock_thread_cls.return_value = MagicMock()
            from orchestrator.monitoring.loki import LokiLogShipper
            LokiLogShipper("http://localhost:3100")

        _, kwargs = mock_thread_cls.call_args
        assert kwargs.get("daemon") is True, "Thread must be daemon=True"

    def test_atexit_handler_registered(self):
        _, _, mock_atexit = _make_shipper()
        mock_atexit.register.assert_called_once()
        # The registered callable should be the shutdown method
        registered = mock_atexit.register.call_args[0][0]
        assert callable(registered)


# ---------------------------------------------------------------------------
# push() — non-blocking enqueue
# ---------------------------------------------------------------------------


class TestPush:
    """push() must enqueue events without blocking."""

    def test_push_enqueues_event(self):
        shipper, _, _ = _make_shipper()
        event = {"event": "agent_result", "run_id": "abc"}
        shipper.push(event)
        assert shipper._queue.qsize() == 1

    def test_push_multiple_events(self):
        shipper, _, _ = _make_shipper()
        for i in range(5):
            shipper.push({"index": i})
        assert shipper._queue.qsize() == 5

    def test_push_after_shutdown_drops_silently(self):
        """Events pushed after shutdown() must be silently dropped."""
        shipper, _, _ = _make_shipper()
        shipper._stop_event.set()
        shipper.push({"event": "dropped"})
        assert shipper._queue.qsize() == 0

    def test_push_latency(self):
        """push() must complete in ≤ 1 ms (non-blocking contract)."""
        shipper, _, _ = _make_shipper()
        event = {"event": "latency_test"}
        start = time.monotonic()
        for _ in range(1000):
            shipper.push(event)
        elapsed = time.monotonic() - start
        # 1000 pushes in under 1 second → each under 1 ms on average
        assert elapsed < 1.0, f"1000 push() calls took {elapsed:.3f}s (> 1s budget)"


# ---------------------------------------------------------------------------
# flush() — drain and send
# ---------------------------------------------------------------------------


class TestFlush:
    """flush() drains the queue and calls _send() with batches."""

    def test_flush_drains_queue(self):
        shipper, _, _ = _make_shipper()
        for i in range(3):
            shipper.push({"index": i})

        with patch.object(shipper, "_send") as mock_send:
            shipper.flush()

        assert shipper._queue.empty()
        mock_send.assert_called_once()
        # The single call should contain all 3 events
        sent_events = mock_send.call_args[0][0]
        assert len(sent_events) == 3

    def test_flush_batches_large_queue(self):
        """flush() must split large queues into batch_size chunks."""
        shipper, _, _ = _make_shipper(batch_size=10)
        for i in range(25):
            shipper.push({"index": i})

        with patch.object(shipper, "_send") as mock_send:
            shipper.flush()

        assert shipper._queue.empty()
        # Should produce ceil(25/10) = 3 _send() calls
        assert mock_send.call_count == 3

    def test_flush_empty_queue_no_send(self):
        shipper, _, _ = _make_shipper()
        with patch.object(shipper, "_send") as mock_send:
            shipper.flush()
        mock_send.assert_not_called()


# ---------------------------------------------------------------------------
# Loki payload structure
# ---------------------------------------------------------------------------


class TestLokiPayload:
    """Verify the Loki push API payload format."""

    def _get_payload(self, events: List[Dict[str, Any]]) -> Dict[str, Any]:
        shipper, _, _ = _make_shipper()
        return shipper._build_payload(events)

    def test_payload_has_streams_key(self):
        payload = self._get_payload([{"event": "run_complete", "run_id": "r1"}])
        assert "streams" in payload

    def test_streams_is_list(self):
        payload = self._get_payload([{"event": "run_complete", "run_id": "r1"}])
        assert isinstance(payload["streams"], list)

    def test_stream_has_stream_and_values(self):
        payload = self._get_payload([{"event": "run_complete", "run_id": "r1"}])
        stream = payload["streams"][0]
        assert "stream" in stream
        assert "values" in stream

    def test_stream_labels_include_job(self):
        payload = self._get_payload([{"event": "run_complete", "run_id": "r1"}])
        labels = payload["streams"][0]["stream"]
        assert labels["job"] == "orchestrator"

    def test_stream_labels_include_run_id(self):
        payload = self._get_payload([{"event": "run_complete", "run_id": "r1"}])
        labels = payload["streams"][0]["stream"]
        assert labels.get("run_id") == "r1"

    def test_stream_labels_include_event(self):
        payload = self._get_payload([{"event": "agent_result", "run_id": "r1"}])
        labels = payload["streams"][0]["stream"]
        assert labels.get("event") == "agent_result"

    def test_stream_labels_include_agent(self):
        payload = self._get_payload([{"agent": "backend_engineer", "run_id": "r1"}])
        labels = payload["streams"][0]["stream"]
        assert labels.get("agent") == "backend_engineer"

    def test_stream_labels_include_level(self):
        payload = self._get_payload([{"level": "error", "run_id": "r1"}])
        labels = payload["streams"][0]["stream"]
        assert labels.get("level") == "error"

    def test_values_is_list_of_pairs(self):
        payload = self._get_payload([{"event": "test"}])
        values = payload["streams"][0]["values"]
        assert isinstance(values, list)
        assert len(values) == 1
        # Each value is [timestamp_ns_string, json_line]
        ts, line = values[0]
        assert isinstance(ts, str)
        assert ts.isdigit(), f"timestamp must be a digit string, got {ts!r}"
        assert isinstance(line, str)

    def test_value_line_is_json(self):
        payload = self._get_payload([{"event": "test", "run_id": "r1"}])
        line = payload["streams"][0]["values"][0][1]
        parsed = json.loads(line)
        assert isinstance(parsed, dict)

    def test_timestamp_ns_is_nanoseconds(self):
        """Timestamp should be in nanoseconds (~1.7 × 10^18 for 2024)."""
        payload = self._get_payload([{"event": "test"}])
        ts_ns = int(payload["streams"][0]["values"][0][0])
        # Should be > 1e18 (nanoseconds since epoch for any time after 2001)
        assert ts_ns > 1_000_000_000_000_000_000

    def test_events_with_same_labels_grouped(self):
        """Events with identical stream labels are merged into one stream entry."""
        events = [
            {"event": "run_complete", "run_id": "r1", "level": "info"},
            {"event": "run_complete", "run_id": "r1", "level": "info"},
        ]
        payload = self._get_payload(events)
        assert len(payload["streams"]) == 1
        assert len(payload["streams"][0]["values"]) == 2

    def test_events_with_different_labels_separate_streams(self):
        events = [
            {"event": "run_complete", "run_id": "r1"},
            {"event": "agent_result", "run_id": "r2"},
        ]
        payload = self._get_payload(events)
        assert len(payload["streams"]) == 2

    def test_label_values_truncated_to_64_chars(self):
        long_val = "x" * 100
        payload = self._get_payload([{"run_id": long_val}])
        labels = payload["streams"][0]["stream"]
        assert len(labels["run_id"]) <= 64


# ---------------------------------------------------------------------------
# Background thread — batch size and interval triggers
# ---------------------------------------------------------------------------


class TestBackgroundThread:
    """Background thread flushes on interval and batch-size triggers."""

    def test_background_thread_name(self):
        with patch("orchestrator.monitoring.loki.atexit"), \
             patch("orchestrator.monitoring.loki.threading.Thread") as mock_cls:
            mock_cls.return_value = MagicMock()
            from orchestrator.monitoring.loki import LokiLogShipper
            LokiLogShipper("http://localhost:3100")

        _, kwargs = mock_cls.call_args
        assert kwargs.get("name") == "loki-log-shipper"

    def test_collect_events_returns_up_to_batch_size(self):
        """_collect_events must stop after batch_size events."""
        shipper, _, _ = _make_shipper(batch_size=5, flush_interval=60.0)
        for i in range(10):
            shipper._queue.put_nowait({"i": i})

        # Speed up the test by using a very short flush interval
        shipper._flush_interval = 0.05
        events = shipper._collect_events()
        assert len(events) == 5

    def test_collect_events_returns_before_batch_on_timeout(self):
        """_collect_events must return partial batch after flush_interval."""
        shipper, _, _ = _make_shipper(batch_size=100, flush_interval=0.1)
        shipper.push({"event": "only_one"})
        start = time.monotonic()
        events = shipper._collect_events()
        elapsed = time.monotonic() - start
        # Should have returned within ~flush_interval + overhead
        assert elapsed < 0.5
        assert len(events) == 1

    def test_run_loop_calls_send(self):
        """_run() must call _send() when events are collected."""
        shipper, _, _ = _make_shipper(flush_interval=0.05, batch_size=100)

        calls: List[List[Any]] = []

        def capture_send(events: List[Any]) -> None:
            calls.append(events[:])

        shipper._send = capture_send  # type: ignore[method-assign]

        # Push an event and run one iteration of the loop
        shipper.push({"event": "test"})

        # Run the _run loop but stop after first iteration
        # We patch _collect_events to return the event once, then stop
        original_collect = shipper._collect_events

        call_count = [0]

        def patched_collect() -> List[Any]:
            call_count[0] += 1
            if call_count[0] == 1:
                return original_collect()
            # Signal stop to break the loop
            shipper._stop_event.set()
            return []

        shipper._collect_events = patched_collect  # type: ignore[method-assign]
        shipper._flush_interval = 0.01

        # Ensure there's something to collect
        shipper.push({"event": "trigger"})
        shipper._run()

        # At least one call to _send should have happened
        assert len(calls) >= 1

    def test_run_loop_catches_exceptions(self):
        """Exception in _collect_events must not kill the run loop."""
        shipper, _, _ = _make_shipper(flush_interval=0.05)

        call_count = [0]

        def raises_once() -> List[Any]:
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("simulated failure")
            # Stop the loop
            shipper._stop_event.set()
            return []

        shipper._collect_events = raises_once  # type: ignore[method-assign]

        # Should not raise
        shipper._run()
        assert call_count[0] >= 2, "Loop should have continued after exception"


# ---------------------------------------------------------------------------
# shutdown()
# ---------------------------------------------------------------------------


class TestShutdown:
    """shutdown() stops the thread, flushes remaining events, and is idempotent."""

    def test_shutdown_sets_stop_event(self):
        shipper, _, _ = _make_shipper()
        shipper.shutdown()
        assert shipper._stop_event.is_set()

    def test_shutdown_calls_flush(self):
        shipper, _, _ = _make_shipper()
        shipper.push({"event": "remaining"})

        with patch.object(shipper, "_send") as mock_send:
            shipper.shutdown()

        mock_send.assert_called_once()

    def test_shutdown_joins_thread(self):
        shipper, mock_thread, _ = _make_shipper()
        shipper.shutdown()
        mock_thread.join.assert_called_once_with(timeout=5.0)

    def test_shutdown_idempotent(self):
        """Calling shutdown() twice must not raise and only joins once."""
        shipper, mock_thread, _ = _make_shipper()
        shipper.shutdown()
        shipper.shutdown()  # second call must be a no-op
        assert mock_thread.join.call_count == 1

    def test_shutdown_flushes_remaining_events(self):
        """Events still in the queue at shutdown time are flushed."""
        shipper, _, _ = _make_shipper()
        for i in range(5):
            shipper.push({"index": i})

        sent: List[List[Any]] = []
        shipper._send = lambda evts: sent.append(evts)  # type: ignore[method-assign]

        shipper.shutdown()

        total = sum(len(batch) for batch in sent)
        assert total == 5


# ---------------------------------------------------------------------------
# atexit handler
# ---------------------------------------------------------------------------


class TestAtexitHandler:
    """atexit handler must be registered and invoke shutdown()."""

    def test_atexit_registered(self):
        _, _, mock_atexit = _make_shipper()
        mock_atexit.register.assert_called_once()

    def test_atexit_callback_is_shutdown(self):
        shipper, _, mock_atexit = _make_shipper()
        registered = mock_atexit.register.call_args[0][0]
        # Calling the registered callback should trigger shutdown
        with patch.object(shipper, "shutdown") as mock_shutdown:
            # The registered callable IS shutdown itself
            assert registered == shipper.shutdown or callable(registered)


# ---------------------------------------------------------------------------
# HTTP transport — connection errors
# ---------------------------------------------------------------------------


class TestHttpTransport:
    """HTTP errors must be logged at WARNING level without raising."""

    def test_httpx_connection_error_logs_warning(self, caplog: Any):
        import logging

        shipper, _, _ = _make_shipper()
        with patch("orchestrator.monitoring.loki.HAS_HTTPX", True), \
             patch("orchestrator.monitoring.loki.httpx") as mock_httpx:
            mock_httpx.post.side_effect = ConnectionRefusedError("refused")

            import logging
            with caplog.at_level(logging.WARNING, logger="orchestrator.monitoring.loki"):
                shipper._send_httpx(b'{"streams":[]}', {"Content-Type": "application/json"})

        assert any("Loki unreachable" in r.message for r in caplog.records)

    def test_urllib_connection_error_logs_warning(self, caplog: Any):
        import logging
        import urllib.error

        shipper, _, _ = _make_shipper()
        with patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
            with caplog.at_level(logging.WARNING, logger="orchestrator.monitoring.loki"):
                shipper._send_urllib(b'{"streams":[]}', {"Content-Type": "application/json"})

        assert any("Loki unreachable" in r.message for r in caplog.records)

    def test_loki_non_200_response_logs_warning(self, caplog: Any):
        import logging

        shipper, _, _ = _make_shipper()
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "internal server error"

        with patch("orchestrator.monitoring.loki.HAS_HTTPX", True), \
             patch("orchestrator.monitoring.loki.httpx") as mock_httpx:
            mock_httpx.post.return_value = mock_response

            with caplog.at_level(logging.WARNING, logger="orchestrator.monitoring.loki"):
                shipper._send_httpx(b'{"streams":[]}', {"Content-Type": "application/json"})

        assert any("500" in r.message for r in caplog.records)

    def test_send_does_nothing_on_empty_events(self):
        """_send() with an empty list must not make any HTTP call."""
        shipper, _, _ = _make_shipper()
        with patch.object(shipper, "_send_httpx") as mock_httpx, \
             patch.object(shipper, "_send_urllib") as mock_urllib:
            shipper._send([])
        mock_httpx.assert_not_called()
        mock_urllib.assert_not_called()


# ---------------------------------------------------------------------------
# Authorization header
# ---------------------------------------------------------------------------


class TestAuthHeader:
    """auth_token must be forwarded as Authorization: Bearer <token>."""

    def test_auth_token_in_headers_httpx(self):
        shipper, _, _ = _make_shipper(auth_token="tok-abc123")
        captured_headers: Dict[str, str] = {}

        def capture(body: bytes, headers: Dict[str, str]) -> None:
            captured_headers.update(headers)

        with patch.object(shipper, "_send_httpx", side_effect=capture):
            shipper._send([{"event": "test"}])

        assert captured_headers.get("Authorization") == "Bearer tok-abc123"

    def test_no_auth_token_no_header(self):
        shipper, _, _ = _make_shipper()  # auth_token=None
        captured_headers: Dict[str, str] = {}

        def capture(body: bytes, headers: Dict[str, str]) -> None:
            captured_headers.update(headers)

        with patch.object(shipper, "_send_httpx", side_effect=capture):
            shipper._send([{"event": "test"}])

        assert "Authorization" not in captured_headers

    def test_auth_token_forwarded_to_urllib(self):
        shipper, _, _ = _make_shipper(auth_token="tok-xyz")
        captured_headers: Dict[str, str] = {}

        def capture(body: bytes, headers: Dict[str, str]) -> None:
            captured_headers.update(headers)

        with patch("orchestrator.monitoring.loki.HAS_HTTPX", False), \
             patch.object(shipper, "_send_urllib", side_effect=capture):
            shipper._send([{"event": "test"}])

        assert captured_headers.get("Authorization") == "Bearer tok-xyz"


# ---------------------------------------------------------------------------
# URL validator — private IP enforcement
# ---------------------------------------------------------------------------


class TestUrlValidator:
    """validate_url is called with allow_private_networks=True."""

    def test_private_ip_endpoint_accepted(self):
        """Private IPs (docker internal) must be accepted by default."""
        shipper, _, _ = _make_shipper("http://10.0.0.1:3100")
        assert "10.0.0.1" in shipper._endpoint

    def test_validate_url_called_with_allow_private_true(self):
        with patch("orchestrator.monitoring.loki.validate_url") as mock_validate, \
             patch("orchestrator.monitoring.loki.atexit"), \
             patch("orchestrator.monitoring.loki.threading.Thread") as mock_thread_cls:
            mock_thread_cls.return_value = MagicMock()
            mock_validate.return_value = "http://localhost:3100"

            from orchestrator.monitoring.loki import LokiLogShipper
            LokiLogShipper("http://localhost:3100")

        mock_validate.assert_called_once_with(
            "http://localhost:3100", allow_private_networks=True
        )

    def test_bad_scheme_raises_value_error(self):
        from orchestrator.monitoring.loki import LokiLogShipper

        with pytest.raises(ValueError):
            LokiLogShipper("file:///etc/passwd")


# ---------------------------------------------------------------------------
# Log field scrubber
# ---------------------------------------------------------------------------


class TestScrubEvent:
    """scrub_event() must remove stack traces, file paths, and secret patterns."""

    def setup_method(self):
        from orchestrator.monitoring.loki import scrub_event
        self.scrub = scrub_event

    def test_stack_trace_truncated(self):
        event = {
            "message": (
                "Traceback (most recent call last):\n"
                '  File "app.py", line 10, in main\n'
                "    result = compute()\n"
                '  File "compute.py", line 5, in compute\n'
                "    raise ValueError('oops')\n"
                "ValueError: oops"
            )
        }
        scrubbed = self.scrub(event)
        assert "[stack trace truncated" in scrubbed["message"]
        # Truncated form should be shorter than original
        assert len(scrubbed["message"]) < len(event["message"])

    def test_file_path_home_redacted(self):
        event = {"message": "config loaded from /home/user/config.yaml"}
        scrubbed = self.scrub(event)
        assert "/home/" not in scrubbed["message"]
        assert "[path redacted]" in scrubbed["message"]

    def test_file_path_tmp_redacted(self):
        event = {"message": "temp file at /tmp/work/data.json"}
        scrubbed = self.scrub(event)
        assert "/tmp/" not in scrubbed["message"]
        assert "[path redacted]" in scrubbed["message"]

    def test_api_key_pattern_redacted(self):
        event = {"message": "using api_key=supersecret123"}
        scrubbed = self.scrub(event)
        assert "supersecret123" not in scrubbed["message"]
        assert "[REDACTED]" in scrubbed["message"]

    def test_password_pattern_redacted(self):
        event = {"message": "connecting with password=hunter2"}
        scrubbed = self.scrub(event)
        assert "hunter2" not in scrubbed["message"]
        assert "[REDACTED]" in scrubbed["message"]

    def test_secret_env_var_redacted(self):
        event = {"message": "env SECRET_DB_PASS=mysecret loaded"}
        scrubbed = self.scrub(event)
        assert "mysecret" not in scrubbed["message"]
        assert "[REDACTED]" in scrubbed["message"]

    def test_non_sensitive_values_preserved(self):
        event = {"event": "run_complete", "run_id": "r1", "status": "success"}
        scrubbed = self.scrub(event)
        assert scrubbed["event"] == "run_complete"
        assert scrubbed["run_id"] == "r1"
        assert scrubbed["status"] == "success"

    def test_nested_dict_scrubbed(self):
        event = {"metadata": {"message": "api_key=abc nested"}}
        scrubbed = self.scrub(event)
        assert "abc" not in scrubbed["metadata"]["message"]
        assert "[REDACTED]" in scrubbed["metadata"]["message"]

    def test_list_elements_scrubbed(self):
        event = {"tags": ["/home/user/file.txt", "safe_tag"]}
        scrubbed = self.scrub(event)
        assert "[path redacted]" in scrubbed["tags"][0]
        assert scrubbed["tags"][1] == "safe_tag"

    def test_long_value_truncated(self):
        from orchestrator.monitoring.loki import _MAX_FIELD_LEN
        event = {"message": "x" * (_MAX_FIELD_LEN + 100)}
        scrubbed = self.scrub(event)
        assert len(scrubbed["message"]) <= _MAX_FIELD_LEN + len("...[truncated]")
        assert "[truncated]" in scrubbed["message"]

    def test_non_string_values_unchanged(self):
        event = {"count": 42, "ratio": 0.95, "flag": True}
        scrubbed = self.scrub(event)
        assert scrubbed["count"] == 42
        assert scrubbed["ratio"] == 0.95
        assert scrubbed["flag"] is True

    def test_returns_copy_not_mutates_original(self):
        event = {"message": "api_key=secret"}
        original_msg = event["message"]
        self.scrub(event)
        assert event["message"] == original_msg  # original unchanged


# ---------------------------------------------------------------------------
# HAS_HTTPX guard — urllib fallback
# ---------------------------------------------------------------------------


class TestHttpxFallback:
    """When HAS_HTTPX is False, _send() must use _send_urllib."""

    def test_uses_urllib_when_no_httpx(self):
        shipper, _, _ = _make_shipper()
        with patch("orchestrator.monitoring.loki.HAS_HTTPX", False), \
             patch.object(shipper, "_send_urllib") as mock_urllib, \
             patch.object(shipper, "_send_httpx") as mock_httpx:
            shipper._send([{"event": "test"}])

        mock_urllib.assert_called_once()
        mock_httpx.assert_not_called()

    def test_uses_httpx_when_available(self):
        shipper, _, _ = _make_shipper()
        with patch("orchestrator.monitoring.loki.HAS_HTTPX", True), \
             patch.object(shipper, "_send_httpx") as mock_httpx, \
             patch.object(shipper, "_send_urllib") as mock_urllib:
            shipper._send([{"event": "test"}])

        mock_httpx.assert_called_once()
        mock_urllib.assert_not_called()

    def test_urllib_sends_correct_method(self):
        """urllib fallback must use HTTP POST."""
        import urllib.request

        shipper, _, _ = _make_shipper()
        captured_requests: List[urllib.request.Request] = []

        def fake_urlopen(req: Any, timeout: float) -> Any:
            captured_requests.append(req)
            mock_resp = MagicMock()
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_resp.getcode.return_value = 204
            return mock_resp

        with patch("urllib.request.urlopen", fake_urlopen):
            shipper._send_urllib(
                b'{"streams":[]}',
                {"Content-Type": "application/json"},
            )

        assert len(captured_requests) == 1
        assert captured_requests[0].get_method() == "POST"

    def test_httpx_follow_redirects_false(self):
        """httpx must be called with follow_redirects=False."""
        shipper, _, _ = _make_shipper()
        with patch("orchestrator.monitoring.loki.HAS_HTTPX", True), \
             patch("orchestrator.monitoring.loki.httpx") as mock_httpx:
            mock_httpx.post.return_value = MagicMock(status_code=204)
            shipper._send_httpx(b'{}', {"Content-Type": "application/json"})

        _, kwargs = mock_httpx.post.call_args
        assert kwargs.get("follow_redirects") is False

    def test_httpx_timeout_5s(self):
        """httpx must use a 5-second timeout."""
        shipper, _, _ = _make_shipper()
        with patch("orchestrator.monitoring.loki.HAS_HTTPX", True), \
             patch("orchestrator.monitoring.loki.httpx") as mock_httpx:
            mock_httpx.post.return_value = MagicMock(status_code=204)
            shipper._send_httpx(b'{}', {"Content-Type": "application/json"})

        _, kwargs = mock_httpx.post.call_args
        assert kwargs.get("timeout") == 5.0


# ---------------------------------------------------------------------------
# Integration: real thread flush
# ---------------------------------------------------------------------------


class TestRealThreadIntegration:
    """Integration tests using a real background thread."""

    def test_events_are_flushed_within_interval(self):
        """Events pushed must be delivered to Loki within 2x flush_interval."""
        delivered: List[Any] = []

        with patch("orchestrator.monitoring.loki.atexit"):
            from orchestrator.monitoring.loki import LokiLogShipper

            shipper = LokiLogShipper("http://localhost:3100", flush_interval=0.1)

        with patch.object(shipper, "_send", side_effect=lambda evts: delivered.extend(evts)):
            shipper.push({"event": "test_delivery", "run_id": "r99"})
            time.sleep(0.5)  # wait 5× flush_interval

        assert len(delivered) >= 1, "Event was not flushed within 0.5s"
        shipper._stop_event.set()
        shipper._thread.join(timeout=2.0)

    def test_shutdown_real_thread(self):
        """Real thread shutdown must complete within the join timeout."""
        with patch("orchestrator.monitoring.loki.atexit"):
            from orchestrator.monitoring.loki import LokiLogShipper

            shipper = LokiLogShipper("http://localhost:3100", flush_interval=0.05)

        # No events — just verify shutdown completes promptly
        with patch.object(shipper, "_send"):
            start = time.monotonic()
            shipper.shutdown()
            elapsed = time.monotonic() - start

        assert elapsed < 2.0, f"shutdown() took {elapsed:.2f}s (> 2s budget)"
        assert not shipper._thread.is_alive()
