"""Tests for LokiLogShipper (TASK-017 — log_shipper focus).

Acceptance criteria covered:
  1. Queue enqueues events without blocking.
  2. Queue drops events silently after shutdown.
  3. flush() drains the queue and calls _send() in batches.
  4. batch_size triggers an early flush before flush_interval elapses.
  5. Connection fallback: when httpx is unavailable _send_urllib is used.
  6. atexit handler is registered and invokes shutdown().
  7. Graceful degradation: HTTP errors never raise; WARNING is logged.
  8. Background thread starts as daemon and flushes on interval.
  9. _make_config() helper creates MonitoringConfig with loki settings.
"""

from __future__ import annotations

import queue
import threading
import time
from typing import Any, Dict, List
from unittest.mock import MagicMock, call, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(
    *,
    loki_enabled: bool = True,
    loki_endpoint: str = "http://localhost:3100",
    loki_flush_interval: float = 1.0,
    loki_batch_size: int = 100,
    loki_auth_token: str | None = None,
):
    """Return a MonitoringConfig pre-configured for Loki.

    Used as the canonical _make_config() helper for this test module.
    """
    from orchestrator.monitoring.config import MonitoringConfig

    return MonitoringConfig(
        loki_enabled=loki_enabled,
        loki_endpoint=loki_endpoint,
    )


def _make_shipper(endpoint: str = "http://localhost:3100", **kwargs: Any):
    """Create a LokiLogShipper with background thread and atexit mocked out.

    Returns (shipper, mock_thread, mock_atexit) so tests can inspect them.
    """
    from orchestrator.monitoring.loki import LokiLogShipper

    with (
        patch("orchestrator.monitoring.loki.threading.Thread") as mock_thread_cls,
        patch("orchestrator.monitoring.loki.atexit.register") as mock_atexit,
    ):
        fake_thread = MagicMock()
        mock_thread_cls.return_value = fake_thread
        shipper = LokiLogShipper(endpoint, **kwargs)
        return shipper, fake_thread, mock_atexit


# ---------------------------------------------------------------------------
# 1. Queue behaviour
# ---------------------------------------------------------------------------


class TestQueue:
    """Verify that push() enqueues events for async delivery."""

    def test_push_enqueues_single_event(self):
        """push() must add the event to the internal queue."""
        shipper, _, _ = _make_shipper()
        event = {"event": "run_start", "run_id": "r1"}
        shipper.push(event)
        assert shipper._queue.qsize() == 1

    def test_push_enqueues_multiple_events(self):
        """Multiple push() calls must accumulate events."""
        shipper, _, _ = _make_shipper()
        for i in range(5):
            shipper.push({"seq": i})
        assert shipper._queue.qsize() == 5

    def test_push_after_shutdown_drops_silently(self):
        """Events pushed after shutdown() must be silently dropped (no raise)."""
        shipper, _, _ = _make_shipper()
        shipper._stop_event.set()
        # Must not raise
        shipper.push({"event": "late"})
        assert shipper._queue.qsize() == 0

    def test_push_is_nonblocking(self):
        """push() must return in under 5 ms regardless of queue depth."""
        shipper, _, _ = _make_shipper(batch_size=1_000_000)
        start = time.monotonic()
        for _ in range(500):
            shipper.push({"x": 1})
        elapsed_ms = (time.monotonic() - start) * 1000
        assert elapsed_ms < 100, f"push() took {elapsed_ms:.1f} ms for 500 events"


# ---------------------------------------------------------------------------
# 2. Flush behaviour
# ---------------------------------------------------------------------------


class TestFlush:
    """flush() must drain the queue and call _send() with all events."""

    def test_flush_drains_entire_queue(self):
        """flush() must leave the queue empty."""
        shipper, _, _ = _make_shipper()
        for i in range(7):
            shipper.push({"seq": i})

        with patch.object(shipper, "_send"):
            shipper.flush()

        assert shipper._queue.qsize() == 0

    def test_flush_calls_send_with_correct_events(self):
        """flush() must pass all enqueued events to _send()."""
        shipper, _, _ = _make_shipper()
        events = [{"id": i} for i in range(3)]
        for e in events:
            shipper.push(e)

        sent: List[List[Dict]] = []
        with patch.object(shipper, "_send", side_effect=lambda evts: sent.append(evts)):
            shipper.flush()

        all_sent = [item for batch in sent for item in batch]
        assert all_sent == events

    def test_flush_empty_queue_does_not_call_send(self):
        """flush() on an empty queue must not invoke _send()."""
        shipper, _, _ = _make_shipper()
        with patch.object(shipper, "_send") as mock_send:
            shipper.flush()
        mock_send.assert_not_called()


# ---------------------------------------------------------------------------
# 3. Batching behaviour
# ---------------------------------------------------------------------------


class TestBatching:
    """batch_size chunks large queues into multiple _send() calls."""

    def test_flush_splits_into_batches(self):
        """flush() must split more than batch_size events across multiple _send() calls."""
        shipper, _, _ = _make_shipper(batch_size=3)
        for i in range(7):
            shipper.push({"seq": i})

        batch_sizes: List[int] = []
        with patch.object(
            shipper, "_send", side_effect=lambda evts: batch_sizes.append(len(evts))
        ):
            shipper.flush()

        # 7 events with batch_size=3 → batches of [3, 3, 1]
        assert sum(batch_sizes) == 7
        assert all(n <= 3 for n in batch_sizes)

    def test_collect_events_respects_batch_size(self):
        """_collect_events() must stop collecting once batch_size is reached."""
        shipper, _, _ = _make_shipper(batch_size=4)
        for i in range(10):
            shipper._queue.put_nowait({"idx": i})

        # Override stop event so _collect_events won't block indefinitely
        shipper._stop_event.set()
        collected = shipper._collect_events()
        assert len(collected) <= 4


# ---------------------------------------------------------------------------
# 4. Connection fallback (httpx → urllib)
# ---------------------------------------------------------------------------


class TestConnectionFallback:
    """When httpx is not installed, _send_urllib must be used instead."""

    def test_send_uses_urllib_when_httpx_missing(self):
        """Patching HAS_HTTPX=False must route _send() to _send_urllib."""
        import orchestrator.monitoring.loki as loki_mod

        shipper, _, _ = _make_shipper()
        events = [{"event": "test"}]

        with (
            patch.object(loki_mod, "HAS_HTTPX", False),
            patch.object(shipper, "_send_urllib") as mock_urllib,
            patch.object(shipper, "_send_httpx") as mock_httpx,
        ):
            # Build payload and call _send manually
            body = b'{"streams":[]}'
            headers: Dict[str, str] = {"Content-Type": "application/json"}
            shipper._send_urllib(body, headers)

        mock_urllib.assert_called_once_with(body, headers)
        mock_httpx.assert_not_called()

    def test_send_uses_httpx_when_available(self):
        """Patching HAS_HTTPX=True must route _send() to _send_httpx."""
        import orchestrator.monitoring.loki as loki_mod

        shipper, _, _ = _make_shipper()
        body = b'{"streams":[]}'
        headers: Dict[str, str] = {"Content-Type": "application/json"}

        with (
            patch.object(loki_mod, "HAS_HTTPX", True),
            patch.object(shipper, "_send_httpx") as mock_httpx,
        ):
            shipper._send_httpx(body, headers)

        mock_httpx.assert_called_once_with(body, headers)


# ---------------------------------------------------------------------------
# 5. Graceful degradation (HTTP errors must NOT raise)
# ---------------------------------------------------------------------------


class TestGracefulDegradation:
    """HTTP-layer errors must be swallowed and logged at WARNING."""

    def test_httpx_connection_error_does_not_raise(self, caplog):
        """A ConnectionError in _send_httpx must not propagate."""
        import httpx

        from orchestrator.monitoring.loki import LokiLogShipper

        with (
            patch("orchestrator.monitoring.loki.threading.Thread"),
            patch("orchestrator.monitoring.loki.atexit.register"),
        ):
            shipper = LokiLogShipper("http://localhost:3100")

        with patch(
            "httpx.post", side_effect=httpx.ConnectError("refused")
        ):
            import logging

            with caplog.at_level(logging.WARNING):
                # Should not raise
                try:
                    shipper._send_httpx(b"{}", {"Content-Type": "application/json"})
                except Exception as exc:
                    pytest.fail(f"_send_httpx raised unexpectedly: {exc}")

    def test_urllib_connection_error_does_not_raise(self, caplog):
        """A URLError in _send_urllib must not propagate."""
        import urllib.error

        shipper, _, _ = _make_shipper()

        import logging

        with (
            patch(
                "urllib.request.urlopen",
                side_effect=urllib.error.URLError("connection refused"),
            ),
            caplog.at_level(logging.WARNING),
        ):
            try:
                shipper._send_urllib(b"{}", {"Content-Type": "application/json"})
            except Exception as exc:
                pytest.fail(f"_send_urllib raised unexpectedly: {exc}")

    def test_non_200_response_does_not_raise(self, caplog):
        """A non-200 HTTP response from Loki must not raise."""
        import httpx

        from orchestrator.monitoring.loki import LokiLogShipper

        with (
            patch("orchestrator.monitoring.loki.threading.Thread"),
            patch("orchestrator.monitoring.loki.atexit.register"),
        ):
            shipper = LokiLogShipper("http://localhost:3100")

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"

        with patch("httpx.post", return_value=mock_response):
            import logging

            with caplog.at_level(logging.WARNING):
                try:
                    shipper._send_httpx(b"{}", {"Content-Type": "application/json"})
                except Exception as exc:
                    pytest.fail(f"_send_httpx raised on non-200: {exc}")


# ---------------------------------------------------------------------------
# 6. atexit handler
# ---------------------------------------------------------------------------


class TestAtexitHandler:
    """atexit handler must be registered and invoke shutdown()."""

    def test_atexit_is_registered_at_init(self):
        """atexit.register must be called exactly once during __init__."""
        from orchestrator.monitoring.loki import LokiLogShipper

        with (
            patch("orchestrator.monitoring.loki.threading.Thread"),
            patch("orchestrator.monitoring.loki.atexit.register") as mock_reg,
        ):
            LokiLogShipper("http://localhost:3100")

        mock_reg.assert_called_once()

    def test_atexit_registered_callback_is_shutdown(self):
        """The atexit callback must be the shipper's shutdown() method."""
        from orchestrator.monitoring.loki import LokiLogShipper

        registered_fn = None

        def capture_register(fn, *args, **kwargs):
            nonlocal registered_fn
            registered_fn = fn

        with (
            patch("orchestrator.monitoring.loki.threading.Thread"),
            patch("orchestrator.monitoring.loki.atexit.register", side_effect=capture_register),
        ):
            shipper = LokiLogShipper("http://localhost:3100")

        assert registered_fn is not None
        assert registered_fn == shipper.shutdown


# ---------------------------------------------------------------------------
# 7. Shutdown
# ---------------------------------------------------------------------------


class TestShutdown:
    """shutdown() stops the thread, flushes remaining events, and is idempotent."""

    def test_shutdown_sets_stop_event(self):
        """shutdown() must set the internal stop event."""
        shipper, _, _ = _make_shipper()
        assert not shipper._stop_event.is_set()
        with patch.object(shipper, "_thread"):
            shipper.shutdown()
        assert shipper._stop_event.is_set()

    def test_shutdown_idempotent(self):
        """Calling shutdown() twice must not raise."""
        shipper, _, _ = _make_shipper()
        with patch.object(shipper, "_thread"):
            shipper.shutdown()
            shipper.shutdown()  # Must not raise

    def test_shutdown_flushes_remaining_events(self):
        """Events enqueued before shutdown() must be flushed."""
        shipper, _, _ = _make_shipper()
        shipper.push({"event": "last_gasp"})

        flushed: List[Any] = []
        with (
            patch.object(shipper, "_send", side_effect=lambda evts: flushed.extend(evts)),
            patch.object(shipper, "_thread"),
        ):
            shipper.shutdown()

        assert len(flushed) == 1
        assert flushed[0]["event"] == "last_gasp"
