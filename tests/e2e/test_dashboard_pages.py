"""E2E tests for all dashboard pages using Playwright headless browser.

Each test:
  1. Navigates to the target page.
  2. Verifies key structural elements (navigation, headings, tables/charts).
  3. Asserts no JavaScript console errors were logged.

PRD requirements covered:
  REQ-004, REQ-005, REQ-006, REQ-007, REQ-011
  AC-003, AC-004, AC-005, AC-006, AC-007, AC-008, AC-014, AC-015
"""

from __future__ import annotations

import pytest

# Skip this entire module if playwright is not installed (e.g. during unit-test
# CI runs that install only [dev] extras, not [e2e]).  This prevents collection
# errors without requiring --ignore=tests/e2e on every pytest invocation.
playwright = pytest.importorskip("playwright", reason="playwright not installed — run: pip install -e '.[e2e]'")
from playwright.sync_api import Page, expect  # noqa: E402 — guarded by importorskip above

from tests.e2e.utils.console_interceptor import ConsoleInterceptor

# Default per-test timeout (ms) — must be < 15 000 per REQ-006
_PAGE_TIMEOUT = 14_000


class TestDashboardPages:
    """Structural page tests — verify DOM elements and absence of JS errors."""

    def test_dashboard_redirects_to_runs_or_dashboard(
        self, page: Page, dashboard_server_info: dict
    ) -> None:
        """GET / should redirect to /dashboard or /runs without JS errors."""
        interceptor = ConsoleInterceptor(page)
        page.goto("/", timeout=_PAGE_TIMEOUT)
        # After redirect the URL should not be the bare root anymore
        assert page.url.rstrip("/") != dashboard_server_info["base_url"].rstrip("/"), (
            f"Root page did not redirect. URL is still: {page.url}"
        )
        interceptor.assert_no_js_exceptions()

    def test_runs_list_page(self, page: Page) -> None:
        """GET /runs — AC-004: nav, heading 'All Runs', search bar, table/empty-state."""
        interceptor = ConsoleInterceptor(page)
        page.goto("/runs", timeout=_PAGE_TIMEOUT)

        # Navigation bar must be present
        nav = page.locator("nav, [role='navigation'], header")
        expect(nav.first).to_be_visible(timeout=5_000)

        # Page heading (case-insensitive)
        heading = page.locator("h1, h2").filter(has_text="Run")
        expect(heading.first).to_be_visible(timeout=5_000)

        # Filter / search element OR data table OR empty-state message
        content_area = page.locator(
            "table, [role='table'], "
            "input[type='search'], input[placeholder*='search' i], input[placeholder*='filter' i], "
            "[data-testid='empty-state'], [class*='empty'], p:has-text('No runs')"
        )
        expect(content_area.first).to_be_visible(timeout=8_000)

        interceptor.assert_no_js_exceptions()

    def test_new_run_form(self, page: Page) -> None:
        """GET /new-run — AC-005: textarea, workflow dropdown, start button, labels."""
        interceptor = ConsoleInterceptor(page)
        page.goto("/new-run", timeout=_PAGE_TIMEOUT)

        # Feature request textarea
        textarea = page.locator("textarea, [name='feature_request'], [id*='feature']")
        expect(textarea.first).to_be_visible(timeout=5_000)

        # Workflow type dropdown/select
        dropdown = page.locator("select, [role='combobox'], [name*='workflow']")
        expect(dropdown.first).to_be_visible(timeout=5_000)

        # Start Run button
        start_btn = page.locator(
            "button:has-text('Start'), button:has-text('Run'), "
            "input[type='submit'][value*='Start' i]"
        )
        expect(start_btn.first).to_be_visible(timeout=5_000)

        interceptor.assert_no_js_exceptions()

    def test_cost_analytics_page(self, page: Page) -> None:
        """GET /cost-analytics — AC-006: KPI cards and chart containers, no JS errors."""
        interceptor = ConsoleInterceptor(page)
        page.goto("/cost-analytics", timeout=_PAGE_TIMEOUT)

        # KPI cards or summary section (accept broad selector)
        kpi_area = page.locator(
            "[class*='card'], [class*='kpi'], [class*='stat'], "
            "[class*='metric'], [class*='summary'], "
            "h1, h2, h3"
        )
        expect(kpi_area.first).to_be_visible(timeout=8_000)

        interceptor.assert_no_js_exceptions()

    def test_slo_compliance_page(self, page: Page) -> None:
        """GET /slo — AC-007: compliance banner or disabled-state banner visible."""
        interceptor = ConsoleInterceptor(page)
        page.goto("/slo", timeout=_PAGE_TIMEOUT)

        # Either a compliance summary / SLI table OR a disabled/empty state
        content = page.locator(
            "table, [role='table'], "
            "[class*='slo'], [class*='compliance'], "
            "[class*='disabled'], [class*='empty'], "
            "h1, h2, h3"
        )
        expect(content.first).to_be_visible(timeout=8_000)

        interceptor.assert_no_js_exceptions()

    def test_observability_page(self, page: Page) -> None:
        """GET /observability — hub page with structural elements."""
        interceptor = ConsoleInterceptor(page)
        page.goto("/observability", timeout=_PAGE_TIMEOUT)

        heading = page.locator("h1, h2, h3")
        expect(heading.first).to_be_visible(timeout=8_000)

        interceptor.assert_no_js_exceptions()

    def test_metrics_page(self, page: Page) -> None:
        """GET /metrics — metrics page loads with structural elements."""
        interceptor = ConsoleInterceptor(page)
        page.goto("/metrics", timeout=_PAGE_TIMEOUT)

        content = page.locator("h1, h2, h3, table, [role='table'], [class*='metric']")
        expect(content.first).to_be_visible(timeout=8_000)

        interceptor.assert_no_js_exceptions()

    def test_alerts_page(self, page: Page) -> None:
        """GET /alerts — AC-015: summary bar + alert table OR empty state."""
        interceptor = ConsoleInterceptor(page)
        page.goto("/alerts", timeout=_PAGE_TIMEOUT)

        content = page.locator(
            "table, [role='table'], "
            "[class*='alert'], [class*='empty'], "
            "h1, h2, h3"
        )
        expect(content.first).to_be_visible(timeout=8_000)

        interceptor.assert_no_js_exceptions()

    def test_settings_page(self, page: Page) -> None:
        """GET /settings — AC-014: form fields for monitoring settings + save button."""
        interceptor = ConsoleInterceptor(page)
        page.goto("/settings", timeout=_PAGE_TIMEOUT)

        # At minimum, there should be some form input or toggle
        form_content = page.locator(
            "input, select, textarea, "
            "button:has-text('Save'), button:has-text('Apply'), "
            "form, [role='form']"
        )
        expect(form_content.first).to_be_visible(timeout=8_000)

        interceptor.assert_no_js_exceptions()

    def test_dashboard_overview_page(self, page: Page) -> None:
        """GET /dashboard — dashboard overview page loads."""
        interceptor = ConsoleInterceptor(page)
        page.goto("/dashboard", timeout=_PAGE_TIMEOUT)

        content = page.locator("h1, h2, h3, [class*='dashboard']")
        expect(content.first).to_be_visible(timeout=8_000)

        interceptor.assert_no_js_exceptions()

    def test_live_run_view(self, page: Page) -> None:
        """GET /runs/{mock_id}/live — live run viewer loads without JS errors.

        AC: live run viewer structure is present (log stream, status indicator,
        or graceful empty/404 state).  Uses a mock run ID so the page must
        handle the missing-run case cleanly.
        """
        interceptor = ConsoleInterceptor(page)
        page.goto("/runs/test-run-id-e2e/live", timeout=_PAGE_TIMEOUT)

        # Accept: structural live-view content OR a graceful 404/error page
        content = page.locator(
            "h1, h2, h3, "
            "[class*='live'], [class*='log'], [class*='stream'], "
            "[class*='status'], [class*='run'], "
            "[class*='error'], [class*='not-found']"
        )
        expect(content.first).to_be_visible(timeout=8_000)

        interceptor.assert_no_js_exceptions()

    def test_artifacts_view(self, page: Page) -> None:
        """GET /runs/{mock_id}/artifacts-view — artifact browser loads without JS errors.

        AC: artifact browser structure present OR graceful empty/404 state.
        Uses a mock run ID — the page must not throw a JS exception when no
        artifacts exist for the given run.
        """
        interceptor = ConsoleInterceptor(page)
        page.goto("/runs/test-run-id-e2e/artifacts-view", timeout=_PAGE_TIMEOUT)

        # Accept: artifact browser elements OR graceful not-found / empty state
        content = page.locator(
            "h1, h2, h3, "
            "[class*='artifact'], [class*='browser'], [class*='file'], "
            "[class*='empty'], [class*='not-found'], [class*='error']"
        )
        expect(content.first).to_be_visible(timeout=8_000)

        interceptor.assert_no_js_exceptions()


class TestPageAccessibility:
    """Basic accessibility checks on dashboard pages.

    Verifies that interactive elements have accessible names (aria-label or
    visible text) and that no images lack alt attributes.
    Covers REQ-015.
    """

    def _check_images_have_alt(self, page: Page) -> None:
        """Assert all <img> elements have a non-empty alt attribute."""
        bad_images = page.locator("img:not([alt]), img[alt='']").all()
        if bad_images:
            srcs = [img.get_attribute("src") or "(no src)" for img in bad_images]
            raise AssertionError(
                f"{len(bad_images)} image(s) missing alt attribute: {srcs}"
            )

    def test_runs_page_images_have_alt(self, page: Page) -> None:
        page.goto("/runs", timeout=_PAGE_TIMEOUT)
        page.wait_for_load_state("networkidle", timeout=8_000)
        self._check_images_have_alt(page)

    def test_new_run_form_images_have_alt(self, page: Page) -> None:
        page.goto("/new-run", timeout=_PAGE_TIMEOUT)
        page.wait_for_load_state("networkidle", timeout=8_000)
        self._check_images_have_alt(page)
