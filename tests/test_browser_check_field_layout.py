"""Settings -> Payroll and checks -> Check Printing -> Field layout (#749), in
playwright's Chromium on the bakery's books of tests/test_theme_contrast.py:
the table fits the panel at the default 1264 px window, a change marks the
group as changed and Save Settings stores it, the test print sends it unsaved,
Find a setting finds it by a field's words, and Reset puts every field back.

Skipped, as one module, where playwright or its Chromium is not installed.
"""

import json

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_browser_grid import _open_at  # noqa: E402
from tests.test_theme_contrast import (  # noqa: E402,F401  (the fixtures)
    browser_fixture,
    company_fixture,
    settle,
)


def _hidden(page):
    return page.input_value("#settings-check-field-layout")


def test_the_field_layout_is_set_saved_test_printed_and_reset(browser, company, books):
    page, handled = _open_at(browser, company, "#/settings", width=1264, height=800)
    try:
        page.click("#settings-tab-payroll")
        fits = page.evaluate("""() => {
            const table = document.querySelector('#settings-check-fields table').getBoundingClientRect();
            const section = document.getElementById('settings-check-fields')
                .closest('.settings-section').getBoundingClientRect();
            return { shown: table.width > 0, inside: table.right <= section.right + 0.5,
                     scroll: document.documentElement.scrollWidth <= innerWidth };
        }""")
        assert fits == {"shown": True, "inside": True, "scroll": True}, fits
        assert _hidden(page) == ""
        page.fill("#check-field-payee-x", "0.25")
        page.fill("#check-field-date-size", "12")
        page.uncheck("#check-field-amount-bold")
        assert json.loads(_hidden(page)) == {
            "amount": {"bold": False},
            "date": {"size": 12},
            "payee": {"x": 0.25},
        }
        assert page.text_content("#settings-dirty-note") == "Unsaved changes"
        # the test print sends it unsaved
        page.evaluate(
            "() => { window._opened = []; window.open = (u) => { window._opened.push(u); return null; }; }"
        )
        page.click("text=Print a test check")
        (url,) = page.evaluate("() => window._opened")
        assert "check_field_layout=" in url
        from urllib.parse import parse_qs, urlparse

        sent = parse_qs(urlparse(url).query)["check_field_layout"][0]
        assert json.loads(sent) == json.loads(_hidden(page))
        assert company.get("/api/settings").json()["check_field_layout"] == ""
        page.click("#settings-save-btn")
        page.wait_for_function(
            "() => !document.getElementById('settings-dirty-note').textContent"
        )
        assert json.loads(
            company.get("/api/settings").json()["check_field_layout"]
        ) == {
            "amount": {"bold": False},
            "date": {"size": 12},
            "payee": {"x": 0.25},
        }
        # Reset: every box back, the hidden field empty, the page changed
        page.click("text=Reset field layout to defaults")
        assert _hidden(page) == ""
        assert page.input_value("#check-field-payee-x") == ""
        assert page.is_checked("#check-field-amount-bold")
        assert page.text_content("#settings-dirty-note") == "Unsaved changes"
        page.click("#settings-save-btn")
        page.wait_for_function(
            "() => !document.getElementById('settings-dirty-note').textContent"
        )
        assert company.get("/api/settings").json()["check_field_layout"] == ""
    finally:
        page.close()


def test_the_amount_size_hint_follows_check_position(browser, company, books):
    # W-139: the figures print at 14 with the check at the bottom, 12 on top
    size = "#check-field-amount-size"
    page, handled = _open_at(browser, company, "#/settings", width=1264, height=800)
    try:
        page.click("#settings-tab-payroll")
        hint = lambda: (  # noqa: E731
            page.get_attribute(size, "placeholder"),
            page.get_attribute(size, "aria-label"),
        )
        assert hint() == ("14", "Amount in figures text size (pt), today 14")
        page.select_option("#settings-check-position", "top")
        assert hint() == ("12", "Amount in figures text size (pt), today 12")
        assert _hidden(page) == ""
        assert page.text_content("#settings-dirty-note") == "Unsaved changes"
        page.click("#settings-save-btn")
        page.wait_for_function(
            "() => !document.getElementById('settings-dirty-note').textContent"
        )
        assert company.get("/api/settings").json()["check_field_layout"] == ""
    finally:
        page.close()
    # drawn again from the saved position
    page, handled = _open_at(browser, company, "#/settings", width=1264, height=800)
    try:
        page.click("#settings-tab-payroll")
        assert page.get_attribute(size, "placeholder") == "12"
        page.select_option("#settings-check-position", "bottom")
        assert page.get_attribute(size, "placeholder") == "14"
    finally:
        page.close()


@pytest.mark.parametrize(
    "words", ["memo bold", "payee address size", "amount in figures", "field layout"]
)
def test_find_a_setting_finds_the_field_layout(browser, company, books, words):
    page, handled = _open_at(browser, company, "#/settings", width=1264, height=800)
    try:
        page.fill("#settings-find", words)
        shown = page.evaluate(
            """() => [...document.querySelectorAll('.settings-section')]
            .filter(s => s.getClientRects().length).map(s => s.querySelector('h3').textContent)"""
        )
        assert "Check Printing" in shown, shown
    finally:
        page.close()
