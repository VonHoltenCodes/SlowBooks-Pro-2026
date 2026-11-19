"""#977 review: a tortoise's note opens whole, in playwright's Chromium on the
bakery's books of tests/test_theme_contrast.py. A note sat inside its
button's box, so the invite dialog's right edge and a form's lines table
(its own scroll box) cut it off; it is now placed against the window. Here
a note opened near a dialog's right edge and one opened from a column head
inside a scrolling table are each wholly inside the window and drawn whole:
the page at each corner of the note is the note. The column head's note
does not take the head's mono capitals. And a dialog opens on its first
field, never on a tortoise.

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

# The open note's box against the window, the boxes around it that clip
# (overflow other than visible) it would cross, and whether the point just
# inside each corner of it is the note itself.
WHOLE = """(id) => {
    const n = document.getElementById(id);
    const r = n.getBoundingClientRect();
    const inside = r.left >= 0 && r.top >= 0 && r.right <= innerWidth && r.bottom <= innerHeight;
    const crossed = [];
    for (let p = n.parentElement; p && p !== document.body; p = p.parentElement) {
        const s = getComputedStyle(p);
        if (!/(auto|scroll|hidden|clip)/.test(s.overflowX + s.overflowY)) continue;
        const b = p.getBoundingClientRect();
        if (r.left < b.left || r.right > b.right || r.top < b.top || r.bottom > b.bottom)
            crossed.push(p.className || p.id || p.tagName);
    }
    const corners = [[r.left + 3, r.top + 3], [r.right - 3, r.top + 3],
                     [r.left + 3, r.bottom - 3], [r.right - 3, r.bottom - 3]];
    const drawn = corners.map(([x, y]) => n.contains(document.elementFromPoint(x, y)));
    const cs = getComputedStyle(n);
    return { inside, crossed, drawn, width: r.width, height: r.height,
             font: cs.fontFamily, transform: cs.textTransform, spacing: cs.letterSpacing };
}"""


def _open_note(page, label):
    button = page.locator(f'#modal button[data-hint][aria-label="{label}"]')
    button.click()
    note_id = button.get_attribute("aria-controls")
    assert button.get_attribute("aria-expanded") == "true"
    return note_id


def _dialog(page, handled, opener):
    page.evaluate(f"async () => {{ await ({opener}); }}")
    page.wait_for_function(MODAL_SHOWN)
    settle(page, handled)
    # the dialog's first focus has gone by (openModal's setTimeout)
    page.wait_for_timeout(50)


def test_a_note_at_a_dialogs_right_edge_opens_whole(browser, company):
    page, handled = _open_at(browser, company, "#/settings", width=1264, height=800)
    try:
        _dialog(page, handled, "SettingsPage.showInvite()")
        # the dialog opens on its first field, not on the tortoise in its label
        first = page.evaluate(
            """() => { const a = document.activeElement;
                return { hint: a.matches('[data-hint]'), name: a.name || '' }; }"""
        )
        assert first == {"hint": False, "name": "username"}, first
        note_id = _open_note(page, "About Sign-in ends on")
        whole = page.evaluate(WHOLE, note_id)
        assert whole["inside"], whole
        assert all(whole["drawn"]), whole
        # it reaches past the dialog's edge, and is not cut off there
        dialog_right = page.evaluate(
            "() => document.getElementById('modal').getBoundingClientRect().right"
        )
        note_right = page.evaluate(
            f"() => document.getElementById('{note_id}').getBoundingClientRect().right"
        )
        assert note_right > dialog_right, (note_right, dialog_right)
        assert "modal" in " ".join(whole["crossed"]), whole
        # Escape shuts the note and leaves the dialog open
        page.keyboard.press("Escape")
        assert not page.locator(f"#{note_id}").is_visible()
        assert page.evaluate(MODAL_SHOWN)
    finally:
        page.close()


def test_a_note_from_a_column_head_in_a_scrolling_table_opens_whole(
    browser, company
):
    # an order with tax: its "Turn into a bill" dialog has a tortoise on the
    # Account column head, inside the lines table's own scroll box
    vendor = company.get("/api/vendors").json()[0]["id"]
    made = company.post(
        "/api/purchase-orders",
        json={
            "vendor_id": vendor,
            "date": "2026-11-02",
            "tax_rate": 0.08,
            "lines": [{"description": "Flour, 50 lb", "quantity": 4, "rate": 30}],
        },
    )
    assert made.status_code == 201, made.text
    po = made.json()["id"]
    page, handled = _open_at(
        browser, company, "#/purchase-orders", width=1264, height=800
    )
    try:
        _dialog(page, handled, f"PurchaseOrdersPage.convertToBill({po})")
        assert not page.evaluate(
            "() => document.activeElement.matches('[data-hint]')"
        )
        label = "About Tax on this order"
        scroller = page.evaluate(
            f"""() => {{ const b = document.querySelector('#modal th button[data-hint][aria-label="{label}"]');
                const c = b && b.closest('.table-container');
                return c ? getComputedStyle(c).overflowX : null; }}"""
        )
        assert scroller in ("auto", "scroll", "hidden"), scroller
        note_id = _open_note(page, label)
        whole = page.evaluate(WHOLE, note_id)
        assert whole["inside"], whole
        assert all(whole["drawn"]), whole
        assert whole["height"] > 40, "the whole note, not one clipped line"
        assert any("table-container" in c for c in whole["crossed"]), whole
        # the note's own text, not the column head's mono capitals
        assert whole["transform"] == "none", whole
        assert whole["spacing"] in ("normal", "0px"), whole
        body_font = page.evaluate("() => getComputedStyle(document.body).fontFamily")
        assert whole["font"] == body_font, (whole["font"], body_font)
        head_font = page.evaluate(
            "() => getComputedStyle(document.querySelector('#modal th')).fontFamily"
        )
        assert whole["font"] != head_font or "mono" not in head_font.lower()
        # a click elsewhere shuts it
        page.mouse.click(5, 795)
        assert not page.locator(f"#{note_id}").is_visible()
    finally:
        page.close()
