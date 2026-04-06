"""pytest configuration and shared fixtures for E2E headless browser tests.

Provides:
  - ``dashboard_server``   — session-scoped fixture: starts a real dashboard
                             subprocess once per test session; yields (host, port).
  - ``browser_context``    — function-scoped: Playwright BrowserContext with
                             console-error interception wired up.
  - ``page``               — function-scoped: fresh Playwright Page per test.
  - ``screenshot_on_failure`` — autouse fixture: saves PNG screenshot to
                             tests/e2e/screenshots/{test_name}_{timestamp}.png
                             when a test fails.

CLI flags:
  --headed              run browser in visible (non-headless) mode
                        (also activated by HEADED=1 env var)

Install prerequisites:
  pip install -e ".[e2e,dashboard]"
  playwright install chromium
"""

from __future__ import annotations

import datetime
import os
from pathlib import Path

import pytest

# ConsoleInterceptor is imported lazily inside fixtures so this conftest can be
# collected even when pytest-playwright is not installed (e.g. unit-test runs).


# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).parent.parent.parent
_SCREENSHOTS_DIR = Path(__file__).parent / "screenshots"
_WORKSPACE_DIR = _PROJECT_ROOT / "workspace"


# ---------------------------------------------------------------------------
# CLI flag: --headed
# ---------------------------------------------------------------------------
# Note: pytest-playwright already registers --headed as a CLI option.
# We do NOT add it again here (that would cause "duplicate option" errors).
# Our browser_type_launch_args fixture below extends pytest-playwright's
# --headed handling to also respect the HEADED=1 environment variable.


def _is_headed(config: pytest.Config) -> bool:
    """Return True if the browser should be visible.

    Supports both --headed CLI flag (registered by pytest-playwright) and
    HEADED=1 environment variable for convenience in shell scripts/Makefiles.
    """
    # pytest-playwright registers --headed; use default=False as a safe fallback
    # in case the option is somehow absent.
    return config.getoption("--headed", default=False) or os.environ.get("HEADED", "") == "1"


# ---------------------------------------------------------------------------
# Dashboard server — session-scoped so it starts once for all E2E tests
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def dashboard_server_info():
    """Start the dashboard server subprocess once for the whole session.

    The server is started against the project workspace directory
    (``workspace/``) so that existing artifacts and state are accessible.

    Yields:
        dict with keys ``host``, ``port``, and ``base_url``.
    """
    import asyncio
    from tests.e2e.utils.server import DashboardTestServer

    workspace = _WORKSPACE_DIR
    workspace.mkdir(parents=True, exist_ok=True)

    server = DashboardTestServer(workspace=workspace)

    async def _start():
        return await server.start()

    loop = asyncio.new_event_loop()
    try:
        host, port = loop.run_until_complete(_start())
    finally:
        pass  # keep loop open for teardown

    info = {"host": host, "port": port, "base_url": f"http://{host}:{port}"}
    yield info

    async def _stop():
        await server.stop()

    loop.run_until_complete(_stop())
    loop.close()


# ---------------------------------------------------------------------------
# Playwright browser — reuse across tests in a session for performance
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def browser_type_launch_args(pytestconfig):
    """Override pytest-playwright's launch args to support --headed / HEADED=1."""
    headed = _is_headed(pytestconfig)
    return {
        "headless": not headed,
        "slow_mo": 100 if headed else 0,  # slow-mo in headed mode aids debugging
    }


# pytest-playwright provides the ``browser`` fixture at session scope.
# We derive ``browser_context`` and ``page`` at function scope so each test
# gets a clean context (no shared cookies/localStorage between tests).

@pytest.fixture
def browser_context(browser, dashboard_server_info):
    """Function-scoped BrowserContext.  Each test gets isolated state."""
    ctx = browser.new_context(
        base_url=dashboard_server_info["base_url"],
        # Emulate a desktop viewport
        viewport={"width": 1280, "height": 800},
        ignore_https_errors=True,
    )
    yield ctx
    ctx.close()


@pytest.fixture
def page(browser_context):
    """Function-scoped Page with console-error interception attached.

    A :class:`ConsoleInterceptor` is wired up immediately after page creation
    so that console errors and uncaught JS exceptions are captured from the
    very first navigation.  The interceptor is stored on ``page._interceptor``
    so individual tests can access it when they need finer-grained assertions.
    """
    from tests.e2e.utils.console_interceptor import ConsoleInterceptor

    p = browser_context.new_page()
    p._interceptor = ConsoleInterceptor(p)  # type: ignore[attr-defined]
    yield p
    p.close()


# ---------------------------------------------------------------------------
# Automatic JS-exception guard — applies to every E2E test
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def auto_check_js_exceptions(request, page):
    """After each test, fail immediately if any uncaught JS exception occurred.

    This is a safety net: individual tests may already call
    ``interceptor.assert_no_js_exceptions()`` at strategic points, but this
    fixture ensures that exceptions raised *after* the last in-test assertion
    (e.g. in cleanup callbacks or during page navigation teardown) are never
    silently swallowed.

    Tests that deliberately expect a JS exception should call
    ``page._interceptor.clear()`` before the fixture teardown runs.
    """
    yield  # run the test body

    # Only check if the test did not already fail (avoid noisy double-failures)
    if not getattr(request.node, "_failed", False):
        interceptor = getattr(page, "_interceptor", None)
        if interceptor is not None:
            try:
                interceptor.assert_no_js_exceptions()
            except AssertionError as exc:
                pytest.fail(str(exc), pytrace=False)


# ---------------------------------------------------------------------------
# Screenshot on failure
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def screenshot_on_failure(request, page):
    """Capture a PNG screenshot when a test fails.

    Saved to: tests/e2e/screenshots/{test_name}_{timestamp}.png
    The path is printed to stdout so CI logs show it clearly.
    """
    yield  # run the test

    # Check if the test failed
    rep = getattr(request.node, "_report_sections", None)
    outcome = getattr(request.node, "rep_call", None)
    failed = outcome is not None and outcome.failed

    # Alternative: check via the session's last failed item
    if not failed:
        # pytest sets this attribute via pytest_runtest_makereport
        failed = getattr(request.node, "_failed", False)

    if failed:
        _screenshots_dir = _SCREENSHOTS_DIR
        _screenshots_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        safe_name = request.node.nodeid.replace("/", "_").replace("::", "_").replace(" ", "_")
        filename = f"{safe_name}_{timestamp}.png"
        screenshot_path = _screenshots_dir / filename

        try:
            page.screenshot(path=str(screenshot_path), full_page=True)
            print(f"\nScreenshot saved: {screenshot_path}")
        except Exception as exc:
            print(f"\nFailed to capture screenshot: {exc}")


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Mark test items that failed so the screenshot_on_failure fixture can detect it."""
    outcome = yield
    rep = outcome.get_result()
    if rep.when == "call" and rep.failed:
        item._failed = True
    # Attach the call report so screenshot fixture can read it
    if rep.when == "call":
        item.rep_call = rep
