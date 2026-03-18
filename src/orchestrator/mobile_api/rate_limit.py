"""In-memory per-IP rate limiter for POST /api/v1/runs.

Enforces a minimum 5-second gap between requests from the same source IP.
Does NOT trust X-Forwarded-For (no trusted proxy configuration).
"""

from __future__ import annotations

import time


class RateLimiter:
    """Sliding-window per-IP rate limiter.

    Allows at most one request per `window_seconds` per source IP.
    Thread-safe for single-worker asyncio usage (no locks needed).
    """

    def __init__(self, window_seconds: float = 5.0) -> None:
        self._window = window_seconds
        # Maps IP address -> timestamp of most recent allowed request
        self._last_request: dict[str, float] = {}

    def check_and_record(self, ip: str) -> bool:
        """Check whether the IP is allowed to make a request now.

        If allowed, records the current timestamp for future checks.

        Args:
            ip: The source IP address of the request.

        Returns:
            True if the request is allowed, False if rate-limited.
        """
        now = time.monotonic()
        last = self._last_request.get(ip)

        if last is not None and (now - last) < self._window:
            # Too soon since last request — rate limited
            return False

        # Allow and record timestamp
        self._last_request[ip] = now
        return True
