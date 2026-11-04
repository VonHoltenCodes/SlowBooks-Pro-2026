"""Arithmetic in an amount box (#696, QuickBooks Desktop's QuickMath), in
playwright's Chromium: + - * or / typed in an amount box that holds a
figure opens a calculator tape under it; = or Enter puts the worked-out
sum in the box and the form's arithmetic follows; Tab does the same and
moves on; Escape leaves the box as it was; a sum that cannot be worked out
says so and the tape stays. On an invoice line's rate and Quick Entry's
(the bakery's books of tests/test_theme_contrast.py) and the register's
quick row (stubbed, tests/test_browser_register_date.py). The tape's input
is named for its box and its total is a polite status; it meets AA in both
themes and stays inside a 320 px window.

Skipped, as one module, where playwright or its Chromium is not installed.
"""

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_browser_grid import _open_at  # noqa: E402
from tests.test_browser_keyboard import (  # noqa: E402
    GOLD,
    RING,
    SETTLED,
    _ax,
    _open_dialog,
)
from tests.test_browser_register_date import _register_page  # noqa: E402
from tests.test_theme_contrast import (  # noqa: E402,F401  (the fixtures)
    _theme,
    below_threshold,
    browser_fixture,
    company_fixture,
    settle,
    sweep_of,
)

RATE = "#inv-lines tr:first-child .line-rate"
TAPE_OPEN = "() => !!document.getElementById('qmath-input')"
TAPE_GONE = "() => !document.getElementById('qmath')"
ACTIVE = "() => document.activeElement && (document.activeElement.id || document.activeElement.className)"


def _invoice(browser, company):
    page, handled = _open_at(browser, company, "#/invoices")
    _open_dialog(page, handled, "InvoicesPage.showForm()", "#inv-lines tr")
    return page, handled


def _start(page, box, figure, typed):
    """The figure in the box, then the sum typed from the keyboard: the
    first operator opens the tape, the rest goes to it."""
    page.fill(box, figure)
    page.focus(box)
    page.keyboard.type(typed[0])
    page.wait_for_function(TAPE_OPEN)
    page.keyboard.type(typed[1:])


def test_an_invoice_lines_rate_works_out_the_sum_and_the_line_follows(
    browser, company, books
):
    page, handled = _invoice(browser, company)
    try:
        _start(page, RATE, "100", "+25*2")
        tape = page.evaluate(
            """() => ({ text: document.getElementById('qmath-input').value,
                status: document.getElementById('qmath-status').textContent })"""
        )
        assert tape == {"text": "100 + 25*2", "status": "= 150.00"}
        # the box itself is untouched until = or Enter
        assert page.input_value(RATE) == "100"
        page.keyboard.press("Enter")
        page.wait_for_function(TAPE_GONE)
        assert page.input_value(RATE) == "150.00"
        amount = page.inner_text("#inv-lines tr:first-child .line-amount")
        assert amount.endswith("150.00"), amount
        # the caret is back in the box, and the dialog is still open
        assert page.evaluate(
            "() => document.activeElement.classList.contains('line-rate')"
        )
        assert page.is_visible("#invoice-form")

        # = works the same as Enter
        _start(page, RATE, "10", "*3")
        page.keyboard.press("=")
        page.wait_for_function(TAPE_GONE)
        assert page.input_value(RATE) == "30.00"
    finally:
        page.close()


def test_escape_leaves_the_box_as_it_was_and_the_dialog_open(browser, company, books):
    page, handled = _invoice(browser, company)
    try:
        _start(page, RATE, "100", "+5")
        page.keyboard.press("Escape")
        page.wait_for_function(TAPE_GONE)
        assert page.input_value(RATE) == "100"
        assert page.evaluate(
            "() => document.activeElement.classList.contains('line-rate')"
        )
        assert page.is_visible(
            "#invoice-form"
        )  # Escape was the tape's, not the dialog's
    finally:
        page.close()


def test_tab_works_the_sum_out_and_moves_on_shift_tab_back(browser, company, books):
    page, handled = _invoice(browser, company)
    try:
        page.fill(RATE, "1")
        page.focus(RATE)
        after, before = page.evaluate(
            """(sel) => { const box = document.querySelector(sel);
                const stops = _tabStops(document.getElementById('modal'), box);
                const i = stops.indexOf(box);
                stops[i + 1].dataset.qmNext = '1'; stops[i - 1].dataset.qmPrev = '1';
                return [stops[i + 1].className, stops[i - 1].className]; }""",
            RATE,
        )
        _start(page, RATE, "100", "*3")
        page.keyboard.press("Tab")
        page.wait_for_function(TAPE_GONE)
        assert page.input_value(RATE) == "300.00"
        assert page.evaluate(
            "() => document.activeElement.dataset.qmNext === '1'"
        ), after

        _start(page, RATE, "300", "/4")
        page.keyboard.press("Shift+Tab")
        page.wait_for_function(TAPE_GONE)
        assert page.input_value(RATE) == "75.00"
        assert page.evaluate(
            "() => document.activeElement.dataset.qmPrev === '1'"
        ), before
    finally:
        page.close()


def test_a_sum_that_cannot_be_worked_out_says_so_and_stays(browser, company, books):
    page, handled = _invoice(browser, company)
    try:
        _start(page, RATE, "100", "/0")
        page.keyboard.press("Enter")
        said = page.inner_text("#qmath-status")
        assert "divides by zero" in said
        assert page.get_attribute("#qmath-input", "aria-invalid") == "true"
        assert page.input_value(RATE) == "100"
        # an unfinished one too
        page.fill("#qmath-input", "100 +")
        page.keyboard.press("Enter")
        assert "not finished" in page.inner_text("#qmath-status")
        assert page.evaluate("() => document.activeElement.id") == "qmath-input"
        # letters never reach the tape
        page.keyboard.type("abc")
        assert page.input_value("#qmath-input") == "100 +"
        page.keyboard.type(" 1")
        page.keyboard.press("Enter")
        page.wait_for_function(TAPE_GONE)
        assert page.input_value(RATE) == "101.00"
    finally:
        page.close()


def test_a_leading_minus_is_a_sign_and_a_read_only_box_never_opens_it(
    browser, company, books
):
    page, handled = _invoice(browser, company)
    try:
        page.fill(RATE, "")
        page.focus(RATE)
        page.keyboard.type("-5")
        assert page.evaluate(TAPE_GONE)
        assert page.input_value(RATE) == "-5"
        page.evaluate(f"() => document.querySelector('{RATE}').readOnly = true")
        page.focus(RATE)
        page.keyboard.type("+")
        assert page.evaluate(TAPE_GONE)
    finally:
        page.close()


def test_the_tape_is_named_for_its_box_and_its_total_is_a_polite_status(
    browser, company, books
):
    page, handled = _invoice(browser, company)
    try:
        _start(page, RATE, "12.50", "+7.25")
        assert page.input_value("#qmath-input") == "12.50 + 7.25"
        node = _ax(page, "#qmath-input")
        assert node["role"] == "textbox"
        assert node["name"] == "Calculator for Rate, line 1"
        status = _ax(page, "#qmath-status")
        assert status["role"] == "status"
        assert page.get_attribute("#qmath-status", "aria-live") == "polite"
        assert page.inner_text("#qmath-status") == "= 19.75"
        assert page.evaluate("() => document.activeElement.id") == "qmath-input"
        # the focused tape draws the keyboard's ring in both themes (W-111):
        # its own look had beaten the field's focus rule, which took the
        # outline away and left nothing to show where focus was
        for theme in ("light", "dark"):
            _theme(page, theme)
            page.evaluate(SETTLED)
            ring = page.evaluate(RING)
            assert ring["at"] == "qmath-input" and ring["keyboard"], ring
            assert ring["outline"] == f"2px solid {GOLD[theme]}", (theme, ring)
            assert ring["offset"] == "2px", (theme, ring)
        _theme(page, "light")
        # inside the dialog (its modal reading takes it in), not a dialog itself
        assert page.evaluate(
            "() => document.getElementById('modal').contains(document.getElementById('qmath'))"
            " && !document.querySelector('#qmath [role=dialog], #qmath[role=dialog]')"
        )
        # AA in both themes, the error sentence too
        sweeps = {}
        for theme in ("light", "dark"):
            _theme(page, theme)
            sweeps[(theme, "tape")] = page.evaluate(
                sweep_of("document.getElementById('qmath')")
            )
        page.fill("#qmath-input", "1/0")
        page.keyboard.press("Enter")
        for theme in ("light", "dark"):
            _theme(page, theme)
            sweeps[(theme, "tape error")] = page.evaluate(
                sweep_of("document.getElementById('qmath')")
            )
        _theme(page, "light")
        texts = {it["text"] for items in sweeps.values() for it in items}
        assert any("divides by zero" in t for t in texts), texts
        assert below_threshold(sweeps) == []
    finally:
        page.close()


def test_the_registers_quick_row_works_it_out_and_the_next_enter_posts(browser):
    posted = []
    page = _register_page(browser, posted)
    try:
        page.select_option("#qe-category", "2")
        _start(page, "#qe-out", "100", "+25*2")
        page.keyboard.press("Enter")
        page.wait_for_function(TAPE_GONE)
        assert page.input_value("#qe-out") == "150.00"
        assert posted == []  # the tape's Enter posted nothing
        assert page.evaluate("() => document.activeElement.id") == "qe-out"
        # Escape there too
        page.focus("#qe-out")
        page.keyboard.type("-50")
        page.wait_for_function(TAPE_OPEN)
        page.keyboard.press("Escape")
        page.wait_for_function(TAPE_GONE)
        assert page.input_value("#qe-out") == "150.00"
        # Tab moves on from the row's Payment box to its Deposit box
        page.focus("#qe-out")
        page.keyboard.type("*2")
        page.keyboard.press("Tab")
        page.wait_for_function(TAPE_GONE)
        assert page.input_value("#qe-out") == "300.00"
        assert page.evaluate("() => document.activeElement.id") == "qe-in"
        page.focus("#qe-out")
        page.keyboard.press("Enter")
        page.wait_for_selector("#toast-container .toast:has-text('Entry posted')")
        assert posted[-1]["amount"] == "-300.00"
    finally:
        page.close()


def test_quick_entry_works_it_out_and_the_total_follows(browser, company, books):
    page, handled = _open_at(browser, company, "#/quick-entry")
    try:
        page.wait_for_selector("#qe-lines tr .line-rate")
        rate = "#qe-lines tr:first-child .line-rate"
        _start(page, rate, "100", "+25*2")
        page.keyboard.press("Enter")
        page.wait_for_function(TAPE_GONE)
        assert page.input_value(rate) == "150.00"
        assert page.inner_text("#qe-total").endswith("150.00")
        assert page.inner_text("#qe-lines tr:first-child .line-amount").endswith(
            "150.00"
        )
    finally:
        page.close()


def test_the_tape_stays_inside_a_320_px_window(browser, company, books):
    page, handled = _open_at(browser, company, "#/quick-entry", width=320, height=640)
    try:
        page.wait_for_selector("#qe-lines tr .line-rate")
        rate = "#qe-lines tr:first-child .line-rate"
        page.locator(rate).scroll_into_view_if_needed()
        _start(page, rate, "100", "+1")
        box = page.evaluate(
            """() => { const r = document.getElementById('qmath').getBoundingClientRect();
                return { left: r.left, right: r.right, top: r.top, bottom: r.bottom,
                         w: window.innerWidth, h: window.innerHeight }; }"""
        )
        assert box["left"] >= 0 and box["right"] <= box["w"], box
        assert box["top"] >= 0 and box["bottom"] <= box["h"], box
        page.keyboard.press("Escape")
    finally:
        page.close()
