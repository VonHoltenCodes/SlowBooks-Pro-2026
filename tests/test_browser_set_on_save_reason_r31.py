"""Why a field ticket's amount is "Set on save" is read in the open (gate
round 30, W-245), in a real browser against the real routes.

A blank rate the server fills when the ticket is saved shows "Set on save"
in the line's Cost and Bill and in the footer, and the reason was only a
tooltip on those cells, which take no focus: a keyboard or touch user
without a screen reader could not read why. The reason now stands under
the lines, in the flow, while any amount is set on save, and each blank
rate it is about is described by it, so it is read on the field a person
tabs to."""

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_browser_grid import _open_at  # noqa: E402
from tests.test_browser_zero_total_ask_r29 import (  # noqa: E402
    FILL_TICKET_LINE,
    NEW_TM_TICKET,
    TICKET_FIGURES,
    _first,
    _made,
)
from tests.test_theme_contrast import (  # noqa: E402, F401
    browser_fixture,
    company_fixture,
    settle,
)

REASON = (
    "Set on save: the rate is blank, so it is filled in when the ticket is "
    "saved: the employee's, equipment's or item's cost rate, and the bill rate "
    "from the cost rate with the job's markup."
)
# The note under the lines: its text, whether it is drawn, and whether it
# comes after the lines table in the document (in the flow, not a tooltip).
NOTE = """() => {
    const n = document.getElementById('ft-on-save-why');
    if (!n) return null;
    const r = n.getBoundingClientRect();
    const table = document.getElementById('ft-lines');
    return {
        text: n.textContent.trim(),
        shown: r.width > 0 && r.height > 0 && getComputedStyle(n).visibility !== 'hidden',
        after: !!(table.compareDocumentPosition(n) & Node.DOCUMENT_POSITION_FOLLOWING),
        live: n.getAttribute('role'),
    };
}"""
# What each rate box of the first line is described by, read as a screen
# reader reads it: the text of the elements aria-describedby names.
DESCRIBED = """() => {
    const row = document.querySelector('#ft-line-body tr.ft-line');
    const of = el => (el.getAttribute('aria-describedby') || '').split(/\\s+/)
        .filter(Boolean).map(id => document.getElementById(id)?.textContent.trim() || '')
        .join(' ');
    return { cost: of(row.querySelector('.ft-cost')), bill: of(row.querySelector('.ft-bill')) };
}"""

# One keystroke in the quantity, counting what changes in the note and in
# the footer (the footer is redrawn by every recalc, so a change there shows
# the keystroke was handled).
KEYSTROKE_CHANGES = """(qty) => {
    const note = document.getElementById('ft-on-save-why');
    const foot = document.getElementById('ft-line-foot');
    const seen = { note: 0, foot: 0 };
    const watch = (el, key) => {
        const o = new MutationObserver(r => { seen[key] += r.length; });
        o.observe(el, { childList: true, characterData: true, subtree: true });
        return o;
    };
    const watchers = { note: watch(note, 'note'), foot: watch(foot, 'foot') };
    const box = document.querySelector('#ft-line-body tr.ft-line .ft-qty');
    box.value = qty;
    box.dispatchEvent(new Event('input', { bubbles: true }));
    for (const [key, o] of Object.entries(watchers)) {
        seen[key] += o.takeRecords().length;
        o.disconnect();
    }
    return seen;
}"""


def test_the_reason_for_set_on_save_is_read_in_the_flow(browser, company):
    r = company.put("/api/settings", json={"company_type": "construction"})
    assert r.status_code == 200, r.text
    cust = _first(company, "/api/customers")
    job = _made(company, "/api/jobs", {"customer_id": cust, "name": "Elm Court"})
    crew = _made(
        company,
        "/api/employees",
        {"first_name": "Ada", "last_name": "Mason", "pay_rate": 32},
    )
    page, handled = _open_at(browser, company, f"#/jobs/{job['id']}")
    try:
        page.locator("#job-tab-body").wait_for()
        page.evaluate(NEW_TM_TICKET)
        page.wait_for_function("() => !!document.querySelector('#ft-line-body tr')")
        settle(page, handled)

        # a priced line: nothing is set on save, so the note says nothing
        page.evaluate(FILL_TICKET_LINE, ["8", "40", "65", ""])
        assert page.evaluate(TICKET_FIGURES) == ["$320.00", "$520.00"] * 2
        note = page.evaluate(NOTE)
        assert note is not None and note["text"] == ""
        assert page.evaluate(DESCRIBED) == {"cost": "", "bill": ""}

        # both rates blank for an employee: every amount is set on save, and
        # the reason stands under the lines, drawn, for anyone to read
        page.evaluate(FILL_TICKET_LINE, ["8", "", "", f"emp:{crew['id']}"])
        assert page.evaluate(TICKET_FIGURES) == ["Set on save"] * 4
        note = page.evaluate(NOTE)
        assert note == {"text": REASON, "shown": True, "after": True, "live": "status"}
        # and a screen reader hears it on each blank rate a person tabs to
        assert page.evaluate(DESCRIBED) == {"cost": REASON, "bill": REASON}

        # a keystroke that leaves the reason as it was does not touch the
        # note, so a screen reader does not hear it again while one types
        changes = page.evaluate(KEYSTROKE_CHANGES, "9")
        assert changes["foot"] > 0, changes  # the keystroke was handled
        assert changes["note"] == 0, changes
        assert page.evaluate(NOTE)["text"] == REASON

        # the cost rate typed, the bill rate left for the save: only the
        # bill rate is described by the reason
        page.evaluate(FILL_TICKET_LINE, ["8", "40", "", f"emp:{crew['id']}"])
        assert page.evaluate(TICKET_FIGURES) == [
            "$320.00",
            "Set on save",
            "$320.00",
            "Set on save",
        ]
        assert page.evaluate(NOTE)["text"] == REASON
        assert page.evaluate(DESCRIBED) == {"cost": "", "bill": REASON}

        # priced again: the note empties and no box points at it
        page.evaluate(FILL_TICKET_LINE, ["8", "40", "65", f"emp:{crew['id']}"])
        assert page.evaluate(NOTE)["text"] == ""
        assert page.evaluate(DESCRIBED) == {"cost": "", "bill": ""}
    finally:
        page.close()
