"""The bill form's line boxes and its dialog, measured in a browser (2.23.1
gate, NEW-103 / W-5, then NEW-104 and NEW-105). The first fix put width
rules on the selects behind the type-ahead pickers, which the picker hides
at 1px, so nothing on screen changed and a text-only pin passed anyway: the
Account box stayed 55px and "6000 - Advertising & Marketing" read "600…".
The rules sit on the visible picker now (combobox.js mirrors a select's
classes onto it as cbx--<class>) and on the plain Description, Qty and Rate
boxes. With those widths the table outgrew the 700px dialog on every
company, so Rate and Amount sat out of view at rest (NEW-105): the dialog
opens wide now, as the estimate's and the job cost entry's do, and the
table scrolls only where the window is too narrow for it. Where it scrolls,
"+ Add Line" below it clears a classic scroll bar's height even in an
engine that draws the bar below the box rather than inside it (WebKit,
NEW-104). Skipped, as one module, where playwright or its Chromium is not
installed.
"""

import pytest

pytest.importorskip("playwright.sync_api")
from tests.test_browser_classes import classed as _classed  # noqa: E402,F401
from tests.test_dialog_contrast import OPEN  # noqa: E402
from tests.test_theme_contrast import (  # noqa: E402,F401  (the fixtures)
    _open,
    _visit,
    browser_fixture,
    company_fixture,
    settle,
)


@pytest.fixture(name="classed")
def classed_fixture(_classed):  # noqa: F811  (the fixture, by its name)
    return _classed


MEASURE = """() => {
    const row = document.querySelector('#bill-lines tr');
    const w = (sel) => { const e = row.querySelector(sel); return e ? Math.round(e.getBoundingClientRect().width) : null; };
    const c = row.closest('.table-container');
    const cr = c.getBoundingClientRect();
    const modal = document.getElementById('modal');
    const btn = [...document.querySelectorAll('#modal-body button')].find(b => b.textContent.trim() === '+ Add Line');
    return {
        item: w('.cbx--line-item'), account: w('.cbx--line-account'), cost: w('.cbx--line-cost-code'),
        cls: w('.line-function-fund'), desc: w('.line-desc'), qty: w('.line-qty'), rate: w('.line-rate'),
        hasCost: !!row.querySelector('.cbx--line-cost-code'),
        wide: modal.classList.contains('modal--wide'),
        modal: Math.round(modal.getBoundingClientRect().width),
        overflow: getComputedStyle(c).overflowX,
        scrollable: c.scrollWidth > c.clientWidth,
        lastCellRight: Math.round(row.lastElementChild.getBoundingClientRect().right),
        boxRight: Math.round(cr.left + c.clientLeft + c.clientWidth),
        room: btn ? Math.round(btn.getBoundingClientRect().top - cr.bottom) : null,
    };
}"""

# A classic horizontal scroll bar's height on macOS and Windows.
BAR = 17


def _bill_form(browser, company, client, width):
    r = client.post(
        "/api/cost-codes",
        json={"code": "01-100", "name": "Site Prep", "cost_type": "labor"},
    )
    assert r.status_code in (200, 201, 400, 409), r.text  # once per company
    page, handled = _open(browser, company)
    page.set_viewport_size({"width": width, "height": 821})
    _visit(page, handled, "#/bills")
    page.evaluate("async () => { await BillsPage.showForm(); }")
    page.wait_for_function(OPEN, timeout=5000)
    page.wait_for_selector("#bill-lines tr .cbx--line-account")
    settle(page, handled)
    return page


def _boxes_hold(m):
    assert m["hasCost"], m  # the ninth column is there
    assert m["item"] >= 110 and m["account"] >= 200 and m["cost"] >= 110, m
    assert m["cls"] >= 124 and m["desc"] >= 140, m
    assert m["qty"] >= 64 and m["rate"] >= 84, m
    assert m["overflow"] == "auto", m


def test_at_the_agents_window_every_column_shows_at_rest(
    browser, company, books, classed, client
):
    page = _bill_form(browser, company, client, 1264)  # the agents' window
    try:
        m = page.evaluate(MEASURE)
        _boxes_hold(m)
        # the dialog grew to the window, and the table fits it whole
        assert m["wide"] and m["modal"] >= 1000, m
        assert not m["scrollable"], m
        assert m["lastCellRight"] <= m["boxRight"], m
    finally:
        page.close()


def test_in_a_narrow_window_the_table_scrolls_and_add_line_clears_the_bar(
    browser, company, books, classed, client
):
    page = _bill_form(browser, company, client, 900)
    try:
        m = page.evaluate(MEASURE)
        _boxes_hold(m)  # a box is never squeezed to fit; the table scrolls instead
        assert m["wide"] and m["modal"] <= 900, m
        assert m["scrollable"], m
        scrolled = page.evaluate(
            """() => { const c = document.querySelector('#bill-lines tr').closest('.table-container');
                       c.scrollLeft = 9999; return c.scrollLeft > 0; }"""
        )
        assert scrolled, m
        # "+ Add Line" sits below where a classic bar would be drawn, counted
        # in the box's height (Chromium) or not (WebKit, NEW-104)
        assert m["room"] is not None and m["room"] >= BAR, m
    finally:
        page.close()


# The move column's room against what one row of its controls takes.
SLACK = """() => {
    const c = document.querySelector('#bill-lines tr .line-order-controls');
    const td = c.closest('td');
    const cs = getComputedStyle(td);
    const content = td.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
    const kids = [...c.children].map(k => k.getBoundingClientRect().width);
    const gap = parseFloat(getComputedStyle(c).columnGap) || 0;
    const need = kids.reduce((a, b) => a + b, 0) + gap * (kids.length - 1);
    return { content: Math.round(content), need: Math.round(need), kids: kids.map(Math.round) };
}"""


# Each move button's glyph against its button: how far the glyph's centre
# sits from the button's, in px, left negative.
GLYPH_OFFSETS = """() => {
    const c = document.querySelector('#bill-lines tr .line-order-controls');
    return [...c.querySelectorAll('button')].map(b => {
        const r = document.createRange();
        r.selectNodeContents(b);
        const g = r.getBoundingClientRect();
        const bb = b.getBoundingClientRect();
        return Math.round(((g.left + g.right) - (bb.left + bb.right)) / 2 * 10) / 10;
    });
}"""


def test_a_construction_bill_shows_every_column_at_the_agents_window(
    browser, company, books, classed, client
):
    """2.31.1 gate, NEW-187: a construction company's bill adds Cost Type,
    eleven columns with the move controls, and its line table scrolled at
    the 1264px window and at the dialog's cap alike. The wide dialog takes
    the window less a margin, the line cells' sides are 2px, and the move
    controls fold where the table is tight, so every column shows at 1264,
    the move controls with them; with room, the controls keep one row. At
    the cap (1300px, 2.31.11 gate, L-1) the row has slack to spare: the
    controls' widths are set in CSS, not by the font (one font set's wider
    handle folded them at the old 1280px cap), the column has 16px more
    than a row of them needs, and each glyph sits in the middle of its
    button (NEW-214: a flex box with no justify-content left them at the
    left edge)."""
    before = client.get("/api/settings").json().get("company_type") or "business"
    r = client.put("/api/settings", json={"company_type": "construction"})
    assert r.status_code == 200, r.text
    try:
        for width in (1264, 1440):
            page = _bill_form(browser, company, client, width)
            try:
                m = page.evaluate(MEASURE)
                _boxes_hold(m)
                assert page.evaluate(
                    "() => !!document.querySelector('#bill-lines tr .line-cost-type')"
                ), m
                assert not m["scrollable"], (width, m)
                assert m["lastCellRight"] <= m["boxRight"], (width, m)
                rows = page.evaluate(
                    """() => { const c = document.querySelector('#bill-lines tr .line-order-controls');
                               const mid = (e) => { const b = e.getBoundingClientRect(); return Math.round((b.top + b.bottom) / 2); };
                               return new Set([...c.children].map(mid)).size; }"""
                )
                if width == 1440:
                    assert rows == 1, (width, rows)  # room: one row
                    slack = page.evaluate(SLACK)
                    assert slack["need"] <= 84, slack  # set in CSS, not by the font
                    assert slack["content"] - slack["need"] >= 16, slack
                    # the glyphs sit in the middle of their 22px buttons: a
                    # flex box with no justify-content put them at the left
                    # (2.31.11 gate, NEW-214)
                    offsets = page.evaluate(GLYPH_OFFSETS)
                    assert len(offsets) == 3 and all(
                        abs(o) <= 1.5 for o in offsets
                    ), offsets
                else:
                    assert rows in (1, 2), (width, rows)
            finally:
                page.close()
    finally:
        client.put("/api/settings", json={"company_type": before})


USE_TAX = """() => {
    const row = document.querySelector('#bill-lines tr');
    const box = row.querySelector('.line-usetax');
    const head = [...row.closest('table').querySelectorAll('thead th')].find(h => h.textContent.trim() === 'Use tax');
    if (!box || !head) return null;
    const b = box.getBoundingClientRect();
    const c = row.querySelector('.line-order-controls');
    const mid = (e) => { const r = e.getBoundingClientRect(); return Math.round((r.top + r.bottom) / 2); };
    return { head: head.textContent.trim(), boxW: Math.round(b.width), boxH: Math.round(b.height),
             rows: new Set([...c.children].map(mid)).size };
}"""


def test_a_construction_bill_keeps_its_use_tax_column_inside_the_window(
    browser, company, books, classed, client
):
    """The per-line Use tax box (#731) added a twelfth column to a
    construction company's bill, and with its header kept on one line the
    line table ran 11px past the 1264px window, on Enter Bill and on a
    memorized bill alike. The header folds at its space now, "Use" over
    "tax", so the column takes its checkbox's room, not the two words', and
    the table fits again with every column and both words shown; at the
    dialog's cap the move controls still keep one row."""
    before = client.get("/api/settings").json().get("company_type") or "business"
    r = client.put("/api/settings", json={"company_type": "construction"})
    assert r.status_code == 200, r.text
    try:
        for form in ("BillsPage.showForm()", "MemorizedPage.showBillForm()"):
            for width in (1264, 1440):
                r = client.post(
                    "/api/cost-codes",
                    json={"code": "01-100", "name": "Site Prep", "cost_type": "labor"},
                )
                assert r.status_code in (200, 201, 400, 409), r.text
                page, handled = _open(browser, company)
                page.set_viewport_size({"width": width, "height": 821})
                _visit(page, handled, "#/bills")
                page.evaluate(f"async () => {{ await {form}; }}")
                page.wait_for_function(OPEN, timeout=5000)
                page.wait_for_selector("#bill-lines tr .cbx--line-account")
                settle(page, handled)
                try:
                    m = page.evaluate(MEASURE)
                    _boxes_hold(m)
                    u = page.evaluate(USE_TAX)
                    assert u and u["head"] == "Use tax", (form, width, u)
                    assert u["boxW"] >= 13 and u["boxH"] >= 13, (form, width, u)
                    assert not m["scrollable"], (form, width, m)
                    assert m["lastCellRight"] <= m["boxRight"], (form, width, m)
                    if width == 1440:  # at the cap the move controls keep one row
                        assert u["rows"] == 1, (form, width, u)
                finally:
                    page.close()
    finally:
        client.put("/api/settings", json={"company_type": before})
