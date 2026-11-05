"""The invitation in a real browser (2.24.0, NEW-167): the address, the
username and the one-time password each have a Copy button of their own,
and the box under them selects like any text."""

import re

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_browser_report_views import _open_at  # noqa: E402
from tests.test_theme_contrast import (  # noqa: E402,F401  (the fixtures)
    ORIGIN,
    browser_fixture,
    company_fixture,
    settle,
)

TITLE = "() => document.getElementById('modal-title').textContent"
READABLE = re.compile(r"^[A-HJ-NP-Za-km-z2-9]{16}$")
SELECTED = "() => String(window.getSelection())"
BOX = """() => { const t = document.getElementById('invite-note');
    return { start: t.selectionStart, end: t.selectionEnd, length: t.value.length,
             active: document.activeElement === t }; }"""


def _clipboard(page):
    page.wait_for_timeout(100)
    return page.evaluate(
        "async () => { try { return await navigator.clipboard.readText(); }"
        " catch (e) { return ''; } }"
    )


def test_each_line_of_the_invitation_copies_on_its_own(browser, company, books):
    page, handled = _open_at(browser, company, "#/settings")
    try:
        page.context.grant_permissions(
            ["clipboard-read", "clipboard-write"], origin=ORIGIN
        )
        page.click("#settings-tab-security")  # its group (#720)
        page.click("button:has-text('Invite your accountant…')")
        page.fill("#modal-body input[name=username]", "cpa-browser")
        page.click("#modal-body button[type=submit]")
        page.wait_for_selector("#invite-password")
        settle(page, handled)
        assert page.evaluate(TITLE) == "Send this to your accountant"
        password = page.locator("#invite-password").inner_text()
        assert READABLE.match(password), password
        address = page.locator("#invite-address").inner_text()
        assert address.startswith("http")
        assert page.locator("#invite-username").inner_text() == "cpa-browser"
        note = page.locator("#invite-note").input_value()
        assert f"One-time password: {password}" in note
        assert f"Address: {address}" in note and "Username: cpa-browser" in note
        assert "Sign in once with this password" in note

        # Copy by the password: the password alone. It is selected for the
        # copy shortcut in any case, and on the clipboard where the page may
        # write it (this origin is not a secure context, so the page says so).
        page.click("button[aria-label='Copy the one-time password']")
        assert page.evaluate(SELECTED) == password
        assert _clipboard(page) in ("", password)
        page.click("button[aria-label='Copy the address']")
        assert page.evaluate(SELECTED) == address

        # the box: a click puts a caret, not all of its text
        page.click("#invite-note", position={"x": 40, "y": 14})
        box = page.evaluate(BOX)
        assert box["active"] and box["end"] - box["start"] < box["length"], box
        # a double-click takes a word
        page.dblclick("#invite-note", position={"x": 40, "y": 14})
        box = page.evaluate(BOX)
        assert 0 < box["end"] - box["start"] < box["length"], box

        # the whole invitation
        page.click("button:has-text('Copy the whole invitation')")
        box = page.evaluate(BOX)
        assert box["active"] and box["start"] == 0 and box["end"] == box["length"], box
        assert _clipboard(page) in ("", note)
    finally:
        page.close()
