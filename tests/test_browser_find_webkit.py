"""Find on this screen (tests/test_browser_find.py) again in playwright's
WebKit, the engine of the macOS desktop window. The first version painted
with the CSS Custom Highlight API, which the macOS window's WKWebView
accepts and never paints; the boxes find.js draws now are ordinary
elements, and these are the same checks in WebKit.

Skipped where playwright's WebKit is not installed.
"""

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_browser_find import *  # noqa: E402,F401,F403  (the tests)
from tests.test_browser_find import company_fixture, desk_fixture  # noqa: E402,F401


@pytest.fixture(scope="module", name="browser")
def webkit_browser_fixture():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        try:
            webkit = p.webkit.launch()
        except Exception as exc:  # the browser is not installed here
            pytest.skip(f"playwright's WebKit is not available: {exc}")
        yield webkit
        webkit.close()
