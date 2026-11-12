"""The Contact support form in a real browser (the mailto builder is also
unit-tested in tests/js/support_mailto.test.cjs). Opened from the Help entry
point in the top bar, with the diagnostics endpoint stubbed, the Send button
is a mailto: to support carrying an [SBP] subject — the app opens the user's
own mail client and sends nothing itself."""

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_browser_ui import _open  # noqa: E402
from tests.test_theme_contrast import browser_fixture  # noqa: E402,F401  (the fixture)

DIAG = {
    "version": "2.29.1",
    "commit": "abc1234def56",
    "platform": "macOS",
    "deployment": "native",
    "is_native": True,
    "reply_to": "books@example.com",
    "environment": "Version: v2.29.1+abc1234def56\nPlatform: macOS\nPython: 3.13.0\nDeployment: native",
    "log_tail": "2026-10-25 09:15:01 INFO ready",
    "log_available": True,
    "log_note": "",
}


def test_help_entry_opens_the_form_and_send_is_a_mailto_to_support(browser):
    page = _open(browser, 1280, 900, "#/")
    page.route("**/api/support/diagnostics", lambda r: r.fulfill(json=DIAG))

    page.click("#support-btn")
    page.wait_for_selector("#support-send")

    # The reply-to is prefilled from the company file's email.
    assert page.input_value("#support-reply-to") == "books@example.com"

    page.fill("#support-what-happened", "The invoice PDF is blank")
    # oninput rebuilds the Send href; let the handler run.
    page.evaluate("() => new Promise((done) => setTimeout(done, 0))")

    href = page.get_attribute("#support-send", "href")
    page.close()

    assert href.startswith("mailto:support@slowbookspro.com")
    subject = href.split("subject=", 1)[1].split("&", 1)[0]
    from urllib.parse import unquote

    assert "[SBP]" in unquote(subject)
    assert "The invoice PDF is blank" in unquote(subject)


def test_a_read_only_sign_in_can_still_use_the_form(browser):
    page = _open(browser, 1280, 900, "#/")
    page.route("**/api/support/diagnostics", lambda r: r.fulfill(json=DIAG))
    page.evaluate("() => App.setRole('readonly')")

    page.click("#support-btn")
    page.wait_for_selector("#support-send")

    # data-readonly-ok: the textareas stay enabled and no lock banner appears —
    # a read-only reviewer is exactly who may need to report a problem.
    assert page.is_enabled("#support-what-happened")
    assert page.is_enabled("#support-reply-to")
    banner = page.evaluate(
        "() => !!document.querySelector('#modal-body .readonly-note')"
    )
    page.close()
    assert banner is False
