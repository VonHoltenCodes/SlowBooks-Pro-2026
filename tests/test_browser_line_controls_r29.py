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
template, deposit (its other lines), change order and pay application. A pay
application's lines are its schedule of values: they cannot be inserted,
removed or moved, and two of them can carry the same item number, so they
too are named by their place. The bill form has no Remove line. Every form
lets its last line be removed like any other (the expense form used to keep
one); what a save with no lines does is another module's
(test_browser_zero_total_ask_r29.py).

A nonprofit's Split is a change to the set of lines as well: its chooser is
a row under the line that is not a line (the lines under it keep their
numbers, it moves with its line and goes with it), and Apply puts one line
per share in the line's place, each named by its place, the keyboard on the
first.

Split is the mouse's as well as the keyboard's: it sits in its own cell,
beside the Function picker, where a click on it reaches it. And an estimate
line that keeps its own markup (the commercial bid) names that box by its
line like every other field.

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
# No form keeps its last line anymore: the expense form's lock went with the
# rule that every form asks before saving a document that adds up to $0.00.
KEEPS_ITS_LAST_LINE = set()

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
    // whether the heading over the control's cell marks its column required
    // ("Account *"), colspans counted
    const head = document.querySelector(sel).closest('table').tHead;
    const starred = (el) => {
        const cell = el.closest('td');
        if (!cell || !head || !head.rows.length) return false;
        let col = 0;
        for (const c of cell.parentElement.cells) { if (c === cell) break; col += c.colSpan || 1; }
        let at = 0;
        for (const h of head.rows[head.rows.length - 1].cells) {
            if (col >= at && col < at + (h.colSpan || 1)) return /\\*\\s*$/.test(h.textContent);
            at += h.colSpan || 1;
        }
        return false;
    };
    [...document.querySelector(sel).querySelectorAll(':scope > tr')].forEach((row, i) => {
        row.querySelectorAll('input:not([type=hidden]), select, textarea, button').forEach(el => {
            if (el.closest('[aria-hidden="true"]') || el.offsetParent === null) return;
            el.dataset.r29c = String(out.length);
            out.push({row: i, tag: row.dataset.r29 || '', kind: el.tagName,
                      star: starred(el)});
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


def _names(page, sel, starred=None):
    """Every control of every line, with the name Chromium's accessibility
    tree gives it: [(line index, the line's tag, role, name)]. The names of
    the fields under a heading that marks its column required ("Account *")
    are added to `starred`; each of them must say it is required."""
    controls = page.evaluate(CONTROLS, sel)
    cdp = page.context.new_cdp_session(page)
    out = []
    starred = [] if starred is None else starred
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
            if c["star"] and c["kind"] != "BUTTON":
                # a field under a heading that marks its column required
                # says so itself, the way a screen reader hears it
                said = {
                    p["name"]: p["value"].get("value")
                    for p in node.get("properties", [])
                }
                assert said.get("required") is True, (name, said)
                starred.append(name)
            out.append((c["row"], c["tag"], node["role"]["value"], name))
    finally:
        cdp.detach()
    return out


def _named_by_place(page, sel, form, step, order):
    """The rule's first half, read off the page: the lines are in `order`,
    every field of line k reads "line k", no two fields read the same, and a
    button that says a line's number says the line it is on."""
    _pickers_settled(page)
    starred = []
    got = _names(page, sel, starred)
    where = f"{form}, after {step}"
    # a name is the heading's words and the line: never the heading's
    # required-field mark ("Account *, line 2")
    for _row, _tag, _role, name in got:
        assert "*" not in name, (where, name)
    if form == "expense":
        rows = len({row for row, _t, _r, _n in got})
        assert starred == [f"Account, line {k + 1}" for k in range(rows)], (
            where,
            starred,
        )
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


# ---- the nonprofit Split: a chooser row under a line, then the shares ----

SPLIT_ROWS = """(sel) => [...document.querySelector(sel).querySelectorAll(':scope > tr')]
    .map(r => ({chooser: r.classList.contains('np-split-row'), tag: r.dataset.r29 || ''}))"""

# a type-ahead box a row holds twice, or one inside another
DOUBLED_BOXES = """(sel) => [...document.querySelector(sel).querySelectorAll(':scope > tr')]
    .map((r, i) => {
        const boxes = [...r.querySelectorAll('input[role="combobox"]')];
        const selects = [...r.querySelectorAll('select')].filter(s => s._cbx);
        return boxes.length !== selects.length || r.querySelector('.cbx .cbx') ? i + 1 : 0;
    }).filter(Boolean)"""


def _split_named(page, sel, form, step, order):
    """With a Split chooser about: the lines are in `order` ("A*" is line A
    with its chooser open under it), every field of line k reads "line k",
    the chooser's own picker reads the number of the line it is under, and
    no two fields of the grid read the same."""
    _pickers_settled(page)
    got = _names(page, sel)
    rows = page.evaluate(SPLIT_ROWS, sel)
    where = f"{form}, after {step}"
    seen = "".join("*" if r["chooser"] else (r["tag"] or "+") for r in rows)
    assert seen == order, (where, seen)
    place, n = [], 0
    for r in rows:
        n += 0 if r["chooser"] else 1
        place.append(n)
    fields = [(place[row], name) for row, _t, role, name in got if role != "button"]
    for line, name in fields:
        said = re.findall(r"\bline (\d+)\b", name, flags=re.I)
        assert said == [str(line)], (where, f"line {line}", name, fields)
    names = [name for _line, name in fields]
    assert len(set(names)) == len(names), (where, sorted(names))
    # the chooser took none of the line controls
    for row, _t, role, name in got:
        if rows[row]["chooser"]:
            assert not re.match(r"(Move|Insert|Remove) line", name), (where, name)
    assert page.evaluate(DOUBLED_BOXES, sel) == [], (where, "a doubled picker box")


def _press_words(page, sel, row, text):
    """Focus the row's button of those words (Split, Apply, Cancel) and press Enter."""
    ok = page.evaluate(
        """([sel, row, text]) => {
            const tr = document.querySelector(sel).querySelectorAll(':scope > tr')[row];
            const b = [...tr.querySelectorAll('button')].find(x => x.textContent.trim() === text);
            if (!b) return false;
            b.focus();
            return document.activeElement === b;
        }""",
        [sel, row, text],
    )
    assert ok, f"no '{text}' button to focus in row {row + 1}"
    page.keyboard.press("Enter")


def _on_split_of(page, sel, form, step, row):
    at = _focus(page, sel)
    where = f"{form}, after {step}"
    assert not at["body"], (where, "the keyboard fell to the document body", at)
    assert (at["tagName"], at["text"], at["row"]) == ("BUTTON", "Split", row), (
        where,
        at,
    )


def _sweep_split(browser, client, form):
    name, _route, _opener, sel, orders, removes = form
    page, _handled = _open_form(browser, client, form)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    try:
        while page.evaluate(ROW_COUNT, sel) < 3:
            _add_line(page, sel)
        page.evaluate(MARK, sel)
        # an amount on each line, for the split to share out
        page.evaluate(
            """(sel) => [...document.querySelector(sel).querySelectorAll(':scope > tr')]
                .forEach((r, i) => {
                    const x = r.querySelector('.je-debit, .line-rate');
                    x.value = String((i + 1) * 100);
                    x.dispatchEvent(new Event('input', {bubbles: true}));
                })""",
            sel,
        )
        _split_named(page, sel, name, "opening", "ABC")

        # the chooser under line 1: the lines under it keep their numbers
        _press_words(page, sel, 0, "Split")
        _split_named(page, sel, name, "Split on line 1", "A*BC")
        # Cancel: the keyboard goes back to the line's Split
        _press_words(page, sel, 1, "Cancel")
        _split_named(page, sel, name, "Cancel in the chooser", "ABC")
        _on_split_of(page, sel, name, "Cancel in the chooser", 0)

        _press_words(page, sel, 0, "Split")
        order = "A*BC"
        if orders:
            # the chooser moves with its line, and a new line goes under both
            _press(page, sel, 0, "Move line down")
            order = "BA*C"
            _split_named(page, sel, name, "Move line down, chooser open", order)
            _press(page, sel, 3, "Move line up")
            order = "BCA*"
            _split_named(page, sel, name, "Move line up past a chooser", order)
            _press(page, sel, 2, "Insert line below")
            page.wait_for_function(f"(sel) => ({ROW_COUNT})(sel) === 5", arg=sel)
            order = "BCA*+"
            _split_named(page, sel, name, "Insert line below, chooser open", order)
        if removes:
            # a line above the chooser's line goes: the chooser stays with
            # its line, the keyboard on the line that took the place
            _press_words(page, sel, 2, "Split")
            _press(page, sel, 0, "Remove line")
            _split_named(page, sel, name, "Remove line above a chooser", "B*C")
            _on_remove_of(page, sel, name, "Remove line above a chooser", "B", 0)
            # the chooser's own line goes: the chooser with it, and the
            # keyboard to the line that took its place, not to Add line
            _press(page, sel, 0, "Remove line")
            _split_named(page, sel, name, "Remove line under a chooser", "C")
            _on_remove_of(page, sel, name, "Remove line under a chooser", "C", 0)
            _add_line(page, sel)
            _add_line(page, sel)
            page.evaluate(MARK, sel)
            order = "ABC"
            _press_words(page, sel, 0, "Split")
            order = "A*BC"
            _split_named(page, sel, name, "Split on line 1 again", order)

        # Apply: one line per share in the line's place, each named by its
        # place with one box a picker, the keyboard on the first share
        at = order.index("*")
        _press_words(page, sel, at, "Apply")
        after = order[: at - 1] + order[at - 1] * 3 + order[at + 1 :]
        page.wait_for_function(
            f"([sel, n]) => ({ROW_COUNT})(sel) === n", arg=[sel, len(after)]
        )
        _split_named(page, sel, name, "Apply in the chooser", after)
        _on_split_of(page, sel, name, "Apply in the chooser", at - 1)
        assert errors == [], (name, errors)
    finally:
        page.close()


def test_a_split_names_every_line_by_its_place_and_keeps_the_keyboard(browser, company):
    r = company.put("/api/settings", json={"company_type": "nonprofit"})
    assert r.status_code == 200, r.text
    r = company.post(
        "/api/nonprofit/allocation-rules",
        json={
            "name": "Rent split",
            "basis": "percent",
            "targets": [
                {"function": "program", "weight": 70},
                {"function": "management", "weight": 20},
                {"function": "fundraising", "weight": 10},
            ],
        },
    )
    assert r.status_code == 201, r.text
    for form in FORMS:
        if form[0] in ("bill", "journal entry"):
            _sweep_split(browser, company, form)


# ---- the pay application: its lines are the job's schedule of values ----


def test_pay_application_lines_are_named_by_place_when_item_numbers_repeat(
    browser, company
):
    job_id = _construction_job(company)
    r = company.put(
        f"/api/jobs/{job_id}/contract",
        json={"contract_type": "lump_sum", "contract_amount": 4000},
    )
    assert r.status_code == 200, r.text
    # two lines with the same item number, two with the same description
    r = company.put(
        f"/api/jobs/{job_id}/sov",
        json={
            "lines": [
                {
                    "item_no": "1",
                    "description": "Sitework",
                    "kind": "work",
                    "scheduled_value": 1000,
                },
                {"description": "Concrete", "kind": "work", "scheduled_value": 1000},
                {"description": "Concrete", "kind": "work", "scheduled_value": 1000},
                {
                    "item_no": "1",
                    "description": "Framing",
                    "kind": "work",
                    "scheduled_value": 1000,
                },
            ]
        },
    )
    assert r.status_code == 200, r.text
    page, handled = _open_at(browser, company, f"#/jobs/{job_id}", height=900)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    try:
        page.locator("#job-tab-body").wait_for()
        page.evaluate("async () => { await JobsPage.setTab('payapps'); }")
        settle(page, handled)
        page.evaluate("async () => { await JobsPage.paNew(); }")
        page.locator("#pa-body tr").first.wait_for(state="attached")
        settle(page, handled)
        assert page.evaluate(ROW_COUNT, "#pa-body") == 4
        page.evaluate(MARK, "#pa-body")
        got = _named_by_place(page, "#pa-body", "pay application", "opening", "ABCD")
        assert [n for row, _t, _r, n in got if row == 3] == [
            "This period, line 4",
            "Stored, line 4",
        ]
        assert errors == [], errors
    finally:
        page.close()


# ---- a line's controls are the mouse's too: each sits in its own cell ----

# Every control of every line, brought into view: is it inside the cell it
# was drawn in, and is it what the mouse finds across the width that shows?
COVERED = """(sel) => {
    const out = [];
    [...document.querySelector(sel).querySelectorAll(':scope > tr')].forEach((row, i) => {
        row.querySelectorAll('input:not([type=hidden]), select:not(.cbx-select), textarea, button').forEach(el => {
            if (el.closest('[aria-hidden="true"]') || el.offsetParent === null) return;
            el.scrollIntoView({block: 'center', inline: 'center'});
            const r = el.getBoundingClientRect();
            const td = el.closest('td').getBoundingClientRect();
            const what = (el.getAttribute('aria-label') || el.textContent.trim() || el.className) + `, row ${i + 1}`;
            if (r.left < td.left - 1 || r.right > td.right + 1) {
                out.push(`${what}: spills out of its cell (${Math.round(r.left)}-${Math.round(r.right)} in ${Math.round(td.left)}-${Math.round(td.right)})`);
            }
            // what of it shows: a field wider than the table's scrolling
            // box shows only the part inside the box
            let lo = Math.max(r.left, 0), hi = Math.min(r.right, window.innerWidth);
            for (let box = el.parentElement; box; box = box.parentElement) {
                if (getComputedStyle(box).overflowX === 'visible') continue;
                const b = box.getBoundingClientRect();
                lo = Math.max(lo, b.left);
                hi = Math.min(hi, b.right);
            }
            if (hi - lo < 4) out.push(`${what}: out of sight`);
            for (let x = lo + 1; x < hi; x += 3) {
                const hit = document.elementFromPoint(x, r.top + r.height / 2);
                if (hit !== el && !el.contains(hit)) {
                    out.push(`${what}: under ${hit ? (hit.className || hit.tagName) : 'nothing'} at ${Math.round(x)}`);
                    break;
                }
            }
        });
    });
    return out;
}"""


def test_a_split_button_sits_in_its_own_cell_and_takes_a_click(browser, company):
    r = company.put("/api/settings", json={"company_type": "nonprofit"})
    assert r.status_code == 200, r.text
    r = company.post(
        "/api/nonprofit/allocation-rules",
        json={
            "name": "Rent split",
            "basis": "percent",
            "targets": [
                {"function": "program", "weight": 70},
                {"function": "management", "weight": 30},
            ],
        },
    )
    assert r.status_code == 201, r.text
    for width in (1280, 1500, 1920):
        for form in FORMS:
            name, route, opener, sel = form[:4]
            if name not in ("bill", "journal entry"):
                continue
            where = f"{name} at {width}px"
            page, handled = _open_at(browser, company, route, width=width, height=900)
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            try:
                page.evaluate(f"async () => {{ await {opener}; }}")
                page.wait_for_function(MODAL_SHOWN)
                page.locator(sel).wait_for(state="attached")
                settle(page, handled)
                while page.evaluate(ROW_COUNT, sel) < 2:
                    _add_line(page, sel)
                _pickers_settled(page)
                assert page.locator(f"{sel} .np-split").count() == 2, where
                assert page.evaluate(COVERED, sel) == [], where

                # a real click on the middle of Split opens the chooser
                # under its line and touches nothing beside it
                ticks = "(sel) => [...document.querySelectorAll(sel + ' input[type=checkbox]')].map(b => b.checked)"
                before = page.evaluate(ticks, sel)
                split = page.locator(f"{sel} > tr").nth(0).locator(".np-split")
                split.scroll_into_view_if_needed()
                box = split.bounding_box()
                page.mouse.click(
                    box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
                )
                page.wait_for_function(
                    "(sel) => document.querySelectorAll(sel + ' > tr.np-split-row').length === 1",
                    arg=sel,
                )
                rows = page.evaluate(SPLIT_ROWS, sel)
                assert [r["chooser"] for r in rows] == [False, True, False], where
                assert page.evaluate(ticks, sel) == before, where
                # with the chooser open, still each in its own cell
                assert page.evaluate(COVERED, sel) == [], where
                assert errors == [], (where, errors)
            finally:
                page.close()


# ---- an estimate line that keeps its own markup (the commercial bid) ----


def test_a_kept_markup_is_named_by_its_line_on_a_commercial_bid(browser, company):
    _construction_job(company)
    customer = company.get("/api/customers").json()[0]["id"]
    r = company.post(
        "/api/estimates",
        json={
            "customer_id": customer,
            "date": "2026-09-21",
            "tax_rate": 0,
            "gc_pct": 5,
            "lines": [
                {
                    "description": words,
                    "quantity": 1,
                    "unit_cost": cost,
                    "markup_pct": 10,
                    "rate": cost * 1.1,
                }
                for words, cost in (("Alpha", 100), ("Bravo", 60), ("Charlie", 25))
            ],
        },
    )
    assert r.status_code == 201, r.text
    sel, name = "#est-lines", "estimate (commercial bid, kept markups)"
    page, handled = _open_at(browser, company, "#/estimates", height=900)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    try:
        page.evaluate(
            f"async () => {{ await EstimatesPage.showForm({r.json()['id']}); }}"
        )
        page.wait_for_function(MODAL_SHOWN)
        page.locator(sel).wait_for(state="attached")
        settle(page, handled)
        assert page.evaluate(ROW_COUNT, sel) == 3, name
        assert page.locator(f"{sel} input.line-markup[type=number]").count() == 3
        page.evaluate(MARK, sel)

        def markups(step, order):
            got = _named_by_place(page, sel, name, step, order)
            said = [n for _row, _t, _role, n in got if n.startswith("Markup percent")]
            want = [
                f"Markup percent, line {i + 1}" for i, t in enumerate(order) if t != "+"
            ]
            assert said == want, (name, step, said)

        markups("opening", "ABC")
        _press(page, sel, 0, "Move line down")
        markups("Move line down on line 1", "BAC")
        _press(page, sel, 0, "Insert line below")
        page.wait_for_function(f"(sel) => ({ROW_COUNT})(sel) === 4", arg=sel)
        markups("Insert line below on line 1", "B+AC")
        _press(page, sel, 0, "Remove line")
        markups("Remove line on line 1", "+AC")
        _on_remove_of(page, sel, name, "Remove line on line 1", "", 0)
        assert errors == [], (name, errors)
    finally:
        page.close()
