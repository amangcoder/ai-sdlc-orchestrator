"""Console error interceptor for Playwright E2E tests.

Attaches a listener to a Playwright Page that accumulates browser console
errors and network failures.  Any uncaught JavaScript exception causes the
accumulated error list to be non-empty, which tests check via
``assert_no_console_errors()``.

Usage:
    interceptor = ConsoleInterceptor(page)
    await page.goto("/runs")
    interceptor.assert_no_console_errors()
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.async_api import ConsoleMessage, Page, Request, Response


@dataclass
class ConsoleInterceptor:
    """Accumulates browser console errors for a Playwright page.

    Attach immediately after the page is created, before any navigation, to
    capture all errors including those raised during page load.

    Attributes:
        page:    The Playwright page to monitor.
        errors:  Accumulated error/warning console messages.
        js_exceptions: Uncaught JavaScript exceptions (pageerror events).
        failed_requests: Network requests that returned 4xx/5xx or failed.
    """

    page: "Page"
    errors: list[str] = field(default_factory=list)
    js_exceptions: list[str] = field(default_factory=list)
    failed_requests: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.page.on("console", self._on_console)
        self.page.on("pageerror", self._on_page_error)
        self.page.on("requestfailed", self._on_request_failed)

    def _on_console(self, msg: "ConsoleMessage") -> None:
        """Called for every console.* call in the browser."""
        if msg.type in ("error", "warning"):
            text = msg.text
            # Filter out known benign browser messages that are not real errors.
            _BENIGN_PREFIXES = (
                # Chrome DevTools noise
                "Failed to load resource: net::ERR_FILE_NOT_FOUND",
                # Favicon 404 is cosmetic
                "favicon.ico",
                # Extension noise in headed mode
                "chrome-extension://",
            )
            if any(text.startswith(prefix) for prefix in _BENIGN_PREFIXES):
                return
            self.errors.append(f"[{msg.type.upper()}] {text}")

    def _on_page_error(self, error: Exception) -> None:
        """Called for uncaught JavaScript exceptions."""
        self.js_exceptions.append(str(error))

    def _on_request_failed(self, request: "Request") -> None:
        """Called when a network request fails completely (not 4xx/5xx)."""
        # Ignore favicon failures — purely cosmetic
        if "favicon" in request.url:
            return
        self.failed_requests.append(
            f"{request.method} {request.url} — {request.failure}"
        )

    def assert_no_console_errors(self, *, include_warnings: bool = False) -> None:
        """Assert that no browser console errors (or JS exceptions) were recorded.

        Args:
            include_warnings: If True, warnings also cause assertion failure.

        Raises:
            AssertionError: Lists all captured errors/exceptions.
        """
        problems: list[str] = list(self.js_exceptions)

        if include_warnings:
            problems.extend(self.errors)
        else:
            problems.extend(e for e in self.errors if e.startswith("[ERROR]"))

        if problems:
            formatted = "\n  ".join(problems)
            raise AssertionError(
                f"Browser reported {len(problems)} console error(s):\n  {formatted}"
            )

    def assert_no_js_exceptions(self) -> None:
        """Assert that no uncaught JavaScript exceptions were thrown.

        Raises:
            AssertionError: Lists all captured JS exceptions.
        """
        if self.js_exceptions:
            formatted = "\n  ".join(self.js_exceptions)
            raise AssertionError(
                f"Uncaught JavaScript exception(s):\n  {formatted}"
            )

    def get_errors(self) -> list[str]:
        """Return all captured console errors and uncaught JS exceptions.

        Combines ``errors`` (console.error / console.warn) with
        ``js_exceptions`` (uncaught JS exceptions) into a single flat list.
        This is the primary method for querying captured problems.

        Returns:
            List of error message strings (may be empty).
        """
        return list(self.errors) + list(self.js_exceptions)

    def assert_no_errors(self) -> None:
        """Assert that no console errors or uncaught JS exceptions were captured.

        Equivalent to calling both :meth:`assert_no_console_errors` and
        :meth:`assert_no_js_exceptions` together; raises :class:`AssertionError`
        with a combined list if any problems exist.

        Raises:
            AssertionError: Lists all captured console errors and JS exceptions.
        """
        all_errors = self.get_errors()
        if all_errors:
            formatted = "\n  ".join(all_errors)
            raise AssertionError(
                f"Browser reported {len(all_errors)} error(s):\n  {formatted}"
            )

    def clear(self) -> None:
        """Reset all accumulated errors (useful between navigations)."""
        self.errors.clear()
        self.js_exceptions.clear()
        self.failed_requests.clear()
