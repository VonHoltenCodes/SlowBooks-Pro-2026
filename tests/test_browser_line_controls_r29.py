"""One rule for the line controls of every line form, in a real browser
against the real routes.

After ANY change to the set or the order of a form's lines (Insert line
below, Remove line, Move line up or down, Add line), every line's fields and
buttons are named by their current place: line k's fields read "..., line k"
and no two fields of a grid read the same. After Remove line the keyboard
goes to the same control on the line that took its place, else on the line
before, else to the form's Add line control; never to the document body.

The forms: invoice, estimate (both layouts), sales receipt, credit memo,
purchase order, vendor credit, bill, journal entry, expense, recurring
template, deposit (its other lines) and change order. A pay application's
lines are its schedule of values: they cannot be inserted, removed or moved,
so there is no change for its names to follow. The bill form has no Remove
line; which forms may lose their last line is each form's own rule (the
expense form keeps one), and is not decided here.

Each sweep stays whole inside one test: the forms are walked one after the
other on one company.
"""

import re

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from tests.test_browser_grid import _open_at  # noqa: E402
from tests.test_theme_contrast import (  # noqa: E402,F401  (the fixtures)
    browser_fixture,
    company_fixture,
    settle,
)

MODAL_SHOWN = (
    "() => !document.getElementById('modal-overlay').classList.contains('hidden')"
)

# name, route, how the form opens, its line grid, how a line is appended,
# whether the lines can be moved and inserted, whether one can be removed,
# whether the last line can be removed
FORMS = [
    ("invoice", "#/invoices", "InvoicesPage.showForm()", "#inv-lines", True, True),
    ("estimate", "#/estimates", "EstimatesPage.showForm()", "#est-lines", True, True),
    (
        "sales receipt",
        "#/sales-receipts",
        "SalesReceiptsPage.showForm()",
        "#sr-lines",
        True,
        True,
    ),
    (
        "credit memo",
        "#/credit-memos",
        "CreditMemosPage.showForm()",
        "#cm-lines",
        True,
        True,
    ),
    (
        "purchase order",
        "#/purchase-orders",
        "PurchaseOrdersPage.showForm()",
        "#po-lines",
        True,
        True,
    ),
    (
        "vendor credit",
        "#/vendor-credits",
        "VendorCreditsPage.showForm()",
        "#vc-lines",
        True,
        True,
    ),
    ("bill", "#/bills", "BillsPage.showForm()", "#bill-lines", True, False),
    ("journal entry", "#/journal", "JournalPage.showForm()", "#je-lines", False, True),
    ("expense", "#/expenses", "ExpensesPage.showForm()", "#expense-lines", False, True),
    (
        "recurring template",
        "#/recurring",
        "RecurringPage.showForm()",
        "#rec-lines",
        True,
        True,
    ),
    ("deposit", "#/deposits", None, "#dep-other-rows", False, True),
]
# opened in construction mode, on a job: the change order, and the estimate
# priced by a markup on each line with the sales forms' cost code cells
CHANGE_ORDER = ("change order", None, None, "#co-line-body", False, True)
MARKUP_ESTIMATE = (
    "estimate (markup per line)",
    "#/estimates",
    "EstimatesPage.showForm()",
    "#est-lines",
    True,
    True,
)
INVOICE_WITH_COST_CODES = (
    "invoice (cost code cells)",
    "#/invoices",
    "InvoicesPage.showForm()",
    "#inv-lines",
    True,
    True,
)
KEEPS_ITS_LAST_LINE = {"expense"}

ROW_COUNT = (
    "(sel) => document.querySelector(sel).querySelectorAll(':scope > tr').length"
)

# The form's own Add line control: the button outside the grid's rows that
# adds a line, nearest the grid. Found here by its words, not by the code
# under test.
ADD_BUTTON = """(sel) => {
    const table = document.querySelector(sel).closest('table');
    for (let box = table.parentNode; box && box.querySelectorAll; box = box.parentNode) {
        const found = [...box.querySelectorAll('button')].filter(b =>
            !b.closest('tbody') && b.offsetParent !== null
            && /^\\+?\\s*add line$/i.test(b.textContent.trim()));
        if (found.length) return found[0];
    }
    return null;
}"""

# Each line as the form has it now: its tag (set once, so a line can be
# followed as it moves; a line added later has none) and its controls.
MARK = """(sel) => [...document.querySelector(sel).querySelectorAll(':scope > tr')]
    .forEach((r, i) => { r.dataset.r29 = 'ABCDEFGH'[i]; })"""

CONTROLS = """(sel) => {
    const out = [];
    [...document.querySelector(sel).querySelectorAll(':scope > tr')].forEach((row, i) => {
        row.querySelectorAll('input:not([type=hidden]), select, textarea, button').forEach(el => {
            if (el.closest('[aria-hidden="true"]') || el.offsetParent === null) return;
            el.dataset.r29c = String(out.length);
            out.push({row: i, tag: row.dataset.r29 || '', kind: el.tagName});
        });
    });
    return out;
}"""

ACTIVE = """(sel) => {
    const a = document.activeElement;
    const tbody = document.querySelector(sel);
    const row = a && a.closest ? a.closest('tr') : null;
    const add = (%s)(sel);
    return {
        body: !a || a === document.body || a === document.documentElement,
        tagName: a ? a.tagName : '',
        label: (a && a.getAttribute && a.getAttribute('aria-label')) || '',
        text: a ? (a.textContent || '').trim() : '',
        row: row && row.parentNode === tbody ? [...tbody.querySelectorAll(':scope > tr')].indexOf(row) : -1,
        tag: row && row.parentNode === tbody ? (row.dataset.r29 || '') : '',
        isAdd: !!add && a === add,
    };
}""" % ADD_BUTTON


def _construction_job(client):
    r = client.put("/api/settings", json={"company_type": "construction"})
    assert r.status_code == 200, r.text
    r = client.post("/api/cost-codes", json={"code": "03 30 00", "name": "Concrete"})
    assert r.status_code in (200, 201), r.text
    customer = client.get("/api/customers").json()[0]
    r = client.post(
        "/api/jobs", json={"customer_id": customer["id"], "name": "Harbor Walk"}
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _open_form(browser, client, form, job_id=None):
    name, route, opener, sel = form[:4]
    if name == "change order":
        page, handled = _open_at(browser, client, f"#/jobs/{job_id}")
        page.locator("#job-tab-body").wait_for()
        page.evaluate("async () => { await JobsPage.setTab('changeorders'); }")
        settle(page, handled)
        page.evaluate("() => { JobsPage.coNew(); }")
    else:
        page, handled = _open_at(browser, client, route, height=900)
        if opener:
            page.evaluate(f"async () => {{ await {opener}; }}")
            page.wait_for_function(MODAL_SHOWN)
    page.locator(sel).wait_for(state="attached")
    settle(page, handled)
    return page, handled


def _add_line(page, sel):
    """One more line, with the form's own Add line control."""
    before = page.evaluate(ROW_COUNT, sel)
    page.evaluate(f"(sel) => ({ADD_BUTTON})(sel).click()", sel)
    page.wait_for_function(
        f"([sel, n]) => ({ROW_COUNT})(sel) === n + 1", arg=[sel, before]
    )
    _pickers_settled(page)


def _pickers_settled(page):
    # a picker's <select> becomes a type-ahead box a moment after its row
    # lands; wait for that pass, then for anything queued behind it
    page.wait_for_function(
        "() => new Promise(r => requestAnimationFrame(() => setTimeout(() => r(true), 0)))"
    )
    page.wait_for_function(
        "() => ![...document.querySelectorAll('tbody select')].some(s =>"
        " !s._cbx && typeof Combobox !== 'undefined' && Combobox.wants(s))"
    )


def _press(page, sel, row, label, key="Enter"):
    """Focus the row's button of that name and press it with the keyboard."""
    ok = page.evaluate(
        """([sel, row, label]) => {
            const tr = document.querySelector(sel).querySelectorAll(':scope > tr')[row];
            const b = [...tr.querySelectorAll('button')].find(x =>
                (x.getAttribute('aria-label') || '').replace(/ \\d+$/, '') === label && !x.disabled);
            if (!b) return false;
            b.focus();
            return document.activeElement === b;
        }""",
        [sel, row, label],
    )
    assert ok, f"no '{label}' button to focus on line {row + 1}"
    page.keyboard.press(key)


def _names(page, sel):
    """Every control of every line, with the name Chromium's accessibility
    tree gives it: [(line index, the line's tag, role, name)]."""
    controls = page.evaluate(CONTROLS, sel)
    cdp = page.context.new_cdp_session(page)
    out = []
    try:
        for i, c in enumerate(controls):
            found = cdp.send(
                "Runtime.evaluate",
                {"expression": f"document.querySelector('[data-r29c=\"{i}\"]')"},
            )
            node = cdp.send(
                "Accessibility.getPartialAXTree",
                {"objectId": found["result"]["objectId"], "fetchRelatives": False},
            )["nodes"][0]
            name = " ".join(str((node.get("name") or {}).get("value", "")).split())
            out.append((c["row"], c["tag"], node["role"]["value"], name))
    finally:
        cdp.detach()
    return out


def _named_by_place(page, sel, form, step, order):
    """The rule's first half, read off the page: the lines are in `order`,
    every field of line k reads "line k", no two fields read the same, and a
    button that says a line's number says the line it is on."""
    _pickers_settled(page)
    got = _names(page, sel)
    where = f"{form}, after {step}"
    tags = []
    for row, tag, _role, _name in got:
        if len(tags) == row:
            tags.append(tag)
    assert "".join(t or "+" for t in tags) == order, (where, tags)
    fields = [(row, name) for row, _t, role, name in got if role != "button"]
    assert fields, where
    for row, name in fields:
        said = re.findall(r"\bline (\d+)\b", name, flags=re.I)
        assert said == [str(row + 1)], (where, f"line {row + 1}", name, fields)
    names = [name for _row, name in fields]
    assert len(set(names)) == len(names), (where, sorted(names))
    for row, _tag, role, name in got:
        if role != "button":
            continue
        assert name, (where, "a button with no name", row + 1)
        said = re.findall(r"\bline (\d+)\b", name, flags=re.I)
        assert said in ([], [str(row + 1)]), (where, f"line {row + 1}", name)
    return got


def _sweep_names(browser, client, form, job_id=None):
    name, _route, _opener, sel, orders, removes = form
    page, _handled = _open_form(browser, client, form, job_id)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    try:
        while page.evaluate(ROW_COUNT, sel) < 3:
            _add_line(page, sel)
        assert page.evaluate(ROW_COUNT, sel) == 3, name
        page.evaluate(MARK, sel)
        _named_by_place(page, sel, name, "opening", "ABC")
        order = "ABC"
        if orders:
            _press(page, sel, 0, "Move line down")
            order = "BAC"
            _named_by_place(page, sel, name, "Move line down on line 1", order)
            _press(page, sel, 2, "Move line up", key="Space")
            order = "BCA"
            _named_by_place(page, sel, name, "Move line up on line 3", order)
            _press(page, sel, 0, "Insert line below")
            page.wait_for_function(f"(sel) => ({ROW_COUNT})(sel) === 4", arg=sel)
            order = "B+CA"
            _named_by_place(page, sel, name, "Insert line below on line 1", order)
            # a drop: the handle of the last line onto the top of the first
            page.evaluate(
                """(sel) => {
                    const rows = document.querySelector(sel).querySelectorAll(':scope > tr');
                    const from = rows[rows.length - 1], to = rows[0];
                    const dt = new DataTransfer();
                    const fire = (el, type, y) => el.dispatchEvent(new DragEvent(type, {
                        bubbles: true, cancelable: true, dataTransfer: dt, clientY: y }));
                    fire(from.querySelector('[data-line-order="drag"]'), 'dragstart', 0);
                    const top = to.getBoundingClientRect().top + 1;
                    fire(to, 'dragover', top);
                    fire(to, 'drop', top);
                    fire(from, 'dragend', top);
                }""",
                sel,
            )
            order = "AB+C"
            _named_by_place(page, sel, name, "a line dragged to the top", order)
        if removes:
            _press(page, sel, 0, "Remove line")
            order = order[1:]
            _named_by_place(page, sel, name, "Remove line on line 1", order)
            _press(page, sel, 1, "Remove line")
            order = order[:1] + order[2:]
            _named_by_place(page, sel, name, "Remove line on line 2", order)
        _add_line(page, sel)
        order += "+"
        _named_by_place(page, sel, name, "Add line", order)
        assert errors == [], (name, errors)
    finally:
        page.close()


def test_every_line_is_named_by_its_place_after_any_change_to_the_lines(
    browser, company
):
    for form in FORMS:
        _sweep_names(browser, company, form)
    # construction: the change order, the estimate priced by a markup on
    # each line, and the cost code cells the sales forms draw on a job
    job_id = _construction_job(company)
    _sweep_names(browser, company, CHANGE_ORDER, job_id)
    r = company.put("/api/settings", json={"estimate_style": "line_markup"})
    assert r.status_code == 200, r.text
    _sweep_names(browser, company, MARKUP_ESTIMATE)
    _sweep_names(browser, company, INVOICE_WITH_COST_CODES)


def _focus(page, sel):
    return page.evaluate(ACTIVE, sel)


def _on_remove_of(page, sel, form, step, tag, row):
    """The keyboard is on the Remove line button of the line tagged `tag`,
    which is now line `row + 1`."""
    at = _focus(page, sel)
    where = f"{form}, after {step}"
    assert not at["body"], (where, "the keyboard fell to the document body", at)
    assert at["tagName"] == "BUTTON", (where, at)
    assert re.fullmatch(r"Remove line( \d+)?", at["label"]), (where, at)
    assert (at["tag"], at["row"]) == (tag, row), (where, at)


def _sweep_focus(browser, client, form, job_id=None):
    name, _route, _opener, sel, _orders, removes = form
    if not removes:
        return
    page, _handled = _open_form(browser, client, form, job_id)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    try:
        while page.evaluate(ROW_COUNT, sel) < 4:
            _add_line(page, sel)
        page.evaluate(MARK, sel)
        _pickers_settled(page)

        # a middle line: the line that took its place
        _press(page, sel, 1, "Remove line")
        _on_remove_of(page, sel, name, "Remove line on line 2 of 4", "C", 1)
        assert page.evaluate(ROW_COUNT, sel) == 3, name

        # the last line: the line before
        _press(page, sel, 2, "Remove line", key="Space")
        _on_remove_of(page, sel, name, "Remove line on the last line", "C", 1)

        # the first of two: the line that took its place. The expense form
        # keeps its last line and locks that line's Remove line, so there
        # the keyboard has no such control to go to: the Add line control
        _press(page, sel, 0, "Remove line")
        if name in KEEPS_ITS_LAST_LINE:
            where = f"{name}, after Remove line on line 1 of 2"
            at = _focus(page, sel)
            assert page.evaluate(ROW_COUNT, sel) == 1, where
            assert at["isAdd"], (where, "not on the form's Add line control", at)
        else:
            _on_remove_of(page, sel, name, "Remove line on line 1 of 2", "C", 0)
            # the only line left: the form's Add line control
            _press(page, sel, 0, "Remove line")
            at = _focus(page, sel)
            where = f"{name}, after Remove line on the only line"
            assert page.evaluate(ROW_COUNT, sel) == 0, where
            assert at["isAdd"], (where, "not on the form's Add line control", at)
            # and Add line from there starts the lines again, named line 1
            page.keyboard.press("Enter")
            page.wait_for_function(f"(sel) => ({ROW_COUNT})(sel) === 1", arg=sel)
            _named_by_place(page, sel, name, "Add line on the emptied form", "+")

        # with the mouse: the same place
        while page.evaluate(ROW_COUNT, sel) < 3:
            _add_line(page, sel)
        page.evaluate(MARK, sel)
        page.evaluate(
            """(sel) => {
                const tr = document.querySelector(sel).querySelectorAll(':scope > tr')[0];
                const b = [...tr.querySelectorAll('button')].find(x =>
                    /^Remove line/.test(x.getAttribute('aria-label') || ''));
                b.scrollIntoView({block: 'center'});
                b.dataset.r29press = '1';
            }""",
            sel,
        )
        page.locator("[data-r29press]").click()
        _on_remove_of(page, sel, name, "a click on Remove line on line 1", "B", 0)
        assert errors == [], (name, errors)
    finally:
        page.close()


def test_remove_line_hands_the_keyboard_to_the_next_line_never_the_body(
    browser, company
):
    for form in FORMS:
        _sweep_focus(browser, company, form)
    job_id = _construction_job(company)
    _sweep_focus(browser, company, CHANGE_ORDER, job_id)
