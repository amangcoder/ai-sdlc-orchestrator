# E2E headless-browser tests using pytest-playwright.
#
# Prerequisites:
#   pip install -e ".[e2e]"
#   playwright install chromium
#
# Run:
#   pytest tests/e2e/               # headless (default)
#   HEADED=1 pytest tests/e2e/      # visible browser
#   pytest tests/e2e/ --headed      # visible browser (pytest-playwright flag)
