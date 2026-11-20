"""Gate round 22, the tortoise (#977) in a real browser: playwright's
Chromium on the bakery's books of tests/test_theme_contrast.py, read through
Chromium's own accessibility tree (DevTools ``Accessibility.getPartialAXTree``,
what WebView2 hands Narrator and NVDA).

NEW-263 (VoiceOver read no note): on Bank Rules' New Rule dialog the Vendor
field is named "Vendor" alone (neither the button's "About Vendor" nor an
open note's text joins the name the label gives it), and is described by its
note; the button is "About Vendor", described by the note, collapsed; Enter
opens the note and moves focus to it, a note named by its own text, so a
screen reader reads it as it opens; Escape closes it with focus back on the
button.

W-189: on Memorized Transactions the Auto-enter tortoise is the page's last
control. Tab out of its open note leaves the page with no relatedTarget for
focusout, and the note closes all the same.

Skipped, as one module, where playwright or its Chromium is not installed.
"""

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_browser_grid import _open_at  # noqa: E402
from tests.test_theme_contrast import (  # noqa: E402,F401  (the fixtures)
    browser_fixture,
    company_fixture,
    settle,
)

MODAL_SHOWN = (
    "() => !document.getElementById('modal-overlay').classList.contains('hidden')"
)

VENDOR_NOTE = (
    "Vendor A line this rule matches is added for this vendor. "
    "The vendor's own name matches too."
)


def _ax(page, selector):
    """The accessibility tree's name, description and states of one element."""
    cdp = page.context.new_cdp_session(page)
    try:
        found = cdp.send(
            "Runtime.evaluate",
            {"expression": f"document.querySelector({selector!r})"},
        )
        nodes = cdp.send(
            "Accessibility.getPartialAXTree",
            {"objectId": found["result"]["objectId"], "fetchRelatives": False},
        )["nodes"]
    finally:
        cdp.detach()
    node = nodes[0]
    states = {p["name"]: p["value"].get("value") for p in node.get("properties", [])}
    return {
        "role": node["role"]["value"],
        "name": " ".join(str((node.get("name") or {}).get("value", "")).split()),
        "description": " ".join(
            str((node.get("description") or {}).get("value", "")).split()
        ),
        "expanded": states.get("expanded"),
        "focused": bool(states.get("focused")),
    }


def test_new_263_the_vendor_tortoise_reads_at_the_button_the_field_and_on_opening(
    browser, company
):
    page, handled = _open_at(browser, company, "#/bank-rules")
    try:
        page.evaluate("() => BankRulesPage.showForm()")
        page.wait_for_function(MODAL_SHOWN)
        settle(page, handled)
        page.wait_for_timeout(50)  # the dialog's first focus has gone by
        button = '#modal button[data-hint][aria-label="About Vendor"]'

        closed = _ax(page, button)
        assert closed["role"] == "button" and closed["name"] == "About Vendor"
        assert closed["description"] == VENDOR_NOTE and closed["expanded"] is False
        field = _ax(page, "#bank-rule-vendor")
        # the label's own word (Chromium hands it as drawn: capitals)
        assert field["name"].casefold() == "vendor", field
        assert field["description"] == VENDOR_NOTE, field

        page.focus(button)
        page.keyboard.press("Enter")
        note = _ax(page, "#bank-rule-vendor-hint-note")
        assert note["role"] == "note" and note["focused"], note
        assert note["name"] == VENDOR_NOTE, note
        assert _ax(page, button)["expanded"] is True
        # the open note is not part of the field's name
        assert _ax(page, "#bank-rule-vendor")["name"].casefold() == "vendor"

        page.keyboard.press("Escape")
        assert not page.locator("#bank-rule-vendor-hint-note").is_visible()
        assert page.evaluate(
            "() => document.activeElement.getAttribute('aria-label')"
        ) == ("About Vendor")
        assert page.evaluate(MODAL_SHOWN), "Escape closes the note, not the dialog"

        # Tab from the open note goes on to the field, and closes the note
        page.keyboard.press("Enter")
        page.keyboard.press("Tab")
        page.wait_for_function(
            "() => document.getElementById('bank-rule-vendor-hint-note').hidden"
        )
        assert page.evaluate("() => document.activeElement.id") == "bank-rule-vendor"
    finally:
        page.close()


def test_w189_tab_out_of_the_pages_last_tortoise_closes_its_note(browser, company):
    page, handled = _open_at(browser, company, "#/memorized")
    try:
        button = page.locator('button[data-hint][aria-label="About Auto-enter"]')
        note_id = button.get_attribute("aria-controls")
        # the tortoise is the page's last control: nothing after it takes focus
        last = page.evaluate("""() => {
                const all = [...document.querySelectorAll(
                    'a[href], button, input, select, textarea, [tabindex]')]
                    .filter(e => !e.disabled && e.tabIndex >= 0 && e.getClientRects().length);
                return all[all.length - 1].getAttribute('aria-label');
            }""")
        assert last == "About Auto-enter", last
        button.focus()
        page.keyboard.press("Enter")
        assert page.locator(f"#{note_id}").is_visible()
        page.keyboard.press("Tab")
        page.wait_for_function(f"() => document.getElementById('{note_id}').hidden")
        assert button.get_attribute("aria-expanded") == "false"

        # from the button itself too, as the read-only sweep tabs it
        button.click()
        page.focus('button[data-hint][aria-label="About Auto-enter"]')
        assert page.locator(f"#{note_id}").is_visible()
        page.keyboard.press("Tab")
        page.wait_for_function(f"() => document.getElementById('{note_id}').hidden")
    finally:
        page.close()
