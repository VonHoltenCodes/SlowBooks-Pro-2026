"""Adding an item from the line being typed (#445), in playwright's Chromium
on the bakery's books of tests/test_theme_contrast.py.

On a new invoice the second line's item picker takes "+ Add item" with a
name typed that the books don't have: the quick add opens in the line's
cell with that name in it, Save creates the item (its code, its type's
income account), and the line shows it picked with its rate filled, as a
normal pick would. The first line is left as it was. Saving the invoice
stores the new item on its line.

"+ Add item" opens only when chosen: Enter or Tab on a name that matches
nothing opens no form, and Tab goes on to the next cell. Cancel and Save
put the focus back on the line's item picker. On a bill the form asks for
a cost and a cost of goods account (5100 for a material), and the bill
posts the line there. A read-only or accountant sign-in is not offered it.

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
SALT = "Salt & Pine Catering Co."
NAME = "Cedar fence post 4x4"
CODE = "CP-44"
ROW2 = "#inv-lines tr:nth-child(2)"
LINE = """(n) => { const r = document.querySelector(`#inv-lines tr:nth-child(${n})`);
    const s = r.querySelector('select.line-item');
    return { value: s.value, box: r.querySelector('.cbx-input').value,
        desc: r.querySelector('.line-desc').value, rate: r.querySelector('.line-rate').value,
        options: [...s.options].map(o => o.textContent),
        form: !!r.querySelector('.iqa-form') }; }"""


def _ok(resp):
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


def _pick(page, box, typed, name):
    """Type into a type-ahead box and take the top match with Enter."""
    box.click()
    page.keyboard.type(typed)
    page.wait_for_function(
        "(name) => { const l = document.querySelectorAll('#cbx-listbox [role=option]');"
        " return l.length > 0 && l[0].textContent.trim() === name"
        " && l[0].getAttribute('aria-selected') === 'true'; }",
        arg=name,
    )
    page.keyboard.press("Enter")


def _choose_add(page, box, typed):
    """Type a name the books don't have, then choose "+ Add item" on purpose:
    nothing is highlighted for an unmatched name, so ArrowDown goes to it
    and Enter takes it."""
    box.click()
    page.keyboard.type(typed)
    page.wait_for_function(
        "() => { const l = document.querySelectorAll('#cbx-listbox [role=option]');"
        " return l.length === 1 && l[0].textContent.trim() === '+ Add item'"
        " && l[0].getAttribute('aria-selected') === 'false'; }"
    )
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Enter")


FOCUS = """() => { const a = document.activeElement;
    const r = a && a.closest('tr');
    return { cls: a ? a.className : '', row: r ? [...r.parentElement.children].indexOf(r) + 1 : 0,
        value: a ? a.value : '' }; }"""


def _invoice_form(browser, company):
    page, handled = _open_at(browser, company, "#/invoices")
    page.evaluate("async () => { await InvoicesPage.showForm(); }")
    page.wait_for_function(MODAL_SHOWN, timeout=5000)
    settle(page, handled)
    return page, handled


def test_an_item_added_from_an_invoice_line_is_picked_on_that_line(
    browser, company, books
):
    page, handled = _open_at(browser, company, "#/invoices")
    page.evaluate("async () => { await InvoicesPage.showForm(); }")
    page.wait_for_function(MODAL_SHOWN, timeout=5000)
    settle(page, handled)
    try:
        customer = page.get_by_role("combobox", name="Customer", exact=True)
        _pick(page, customer, "salt", SALT)
        page.fill("#inv-lines tr:nth-child(1) .line-desc", "Tent and tables")
        page.fill("#inv-lines tr:nth-child(1) .line-rate", "250")
        page.click("#invoice-form button:has-text('+ Add Line')")
        page.wait_for_selector(f"{ROW2} .cbx-input")
        # a name the books don't have, and "+ Add item" chosen: the quick
        # add opens with the name in it
        _choose_add(page, page.locator(f"{ROW2} .cbx-input"), NAME)
        page.wait_for_selector(f"{ROW2} .iqa-form .iqa-name")
        handed_on = page.input_value(f"{ROW2} .iqa-name")
        page.fill(f"{ROW2} .iqa-code", CODE)
        page.select_option(f"{ROW2} .iqa-type", "material")
        page.fill(f"{ROW2} .iqa-rate", "18.5")
        # the type's income account, once the accounts are in
        page.wait_for_function(
            f"() => document.querySelector('{ROW2} .iqa-income').options.length > 1",
            timeout=5000,
        )
        income = page.evaluate(
            f"""() => {{ const s = document.querySelector('{ROW2} .iqa-income');
                return s.options[s.selectedIndex].textContent; }}"""
        )
        page.click(f"{ROW2} .iqa-save")
        page.wait_for_function(
            f"() => /^\\d+$/.test(document.querySelector('{ROW2} select.line-item').value)",
            timeout=5000,
        )
        settle(page, handled)
        focus_after_save = page.evaluate(FOCUS)
        first = page.evaluate(LINE, 1)
        second = page.evaluate(LINE, 2)
        page.click("#invoice-form button[type=submit]")
        page.wait_for_function(f"!({MODAL_SHOWN})()", timeout=5000)
        settle(page, handled)
    finally:
        page.close()

    assert handed_on == NAME
    assert income == "4200 - Material Income"
    # back on the line's item picker, showing the new item
    assert focus_after_save == {
        "cls": "cbx-input",
        "row": 2,
        "value": f"{CODE} — {NAME}",
    }
    made = [i for i in _ok(company.get("/api/items", params={"q": CODE}))]
    assert len(made) == 1, made
    item = made[0]
    assert (item["name"], item["code"], item["item_type"]) == (NAME, CODE, "material")
    assert float(item["rate"]) == 18.5
    accounts = {a["id"]: a["account_number"] for a in _ok(company.get("/api/accounts"))}
    assert accounts[item["income_account_id"]] == "4200"

    # the line shows the new item, autofilled as a pick would
    assert second["value"] == str(item["id"])
    assert second["box"] == f"{CODE} — {NAME}"
    assert second["desc"] == NAME and float(second["rate"]) == 18.5
    assert second["form"] is False
    # the other line kept what was typed, and can choose the new item too
    assert first["value"] == "" and first["box"] == ""
    assert first["desc"] == "Tent and tables" and float(first["rate"]) == 250
    assert f"{CODE} — {NAME}" in first["options"]

    saved = _ok(company.get(f"/api/invoices?customer_id={books['customer']}"))
    newest = _ok(company.get(f"/api/invoices/{max(i['id'] for i in saved)}"))
    lines = sorted(newest["lines"], key=lambda ln: ln["line_order"])
    assert [ln["item_id"] for ln in lines] == [None, item["id"]]
    assert lines[0]["description"] == "Tent and tables"


def test_enter_and_tab_on_an_unmatched_name_open_no_quick_add(browser, company, books):
    page, handled = _invoice_form(browser, company)
    try:
        box = page.locator("#inv-lines tr:nth-child(1) .cbx-input")
        box.click()
        page.keyboard.type("Nothing the bakery sells")
        page.wait_for_function(
            "() => { const l = document.querySelectorAll('#cbx-listbox [role=option]');"
            " return l.length === 1 && l[0].textContent.trim() === '+ Add item'; }"
        )
        page.keyboard.press("Enter")
        settle(page, handled)
        after_enter = page.evaluate(LINE, 1)
        enter_focus = page.evaluate(FOCUS)
        page.keyboard.press("Tab")
        settle(page, handled)
        after_tab = page.evaluate(LINE, 1)
        tab_focus = page.evaluate(FOCUS)
    finally:
        page.close()

    assert after_enter["form"] is False and after_enter["value"] == ""
    assert enter_focus["cls"] == "cbx-input" and enter_focus["row"] == 1
    # Tab went on to the description, with no item and no form
    assert after_tab["form"] is False and after_tab["value"] == ""
    assert after_tab["box"] == ""
    assert (tab_focus["cls"], tab_focus["row"]) == ("line-desc", 1)


def test_cancel_puts_the_focus_back_on_the_item_picker(browser, company, books):
    page, handled = _invoice_form(browser, company)
    row = "#inv-lines tr:nth-child(1)"
    try:
        _choose_add(page, page.locator(f"{row} .cbx-input"), "Lemon curd tart")
        page.wait_for_selector(f"{row} .iqa-form .iqa-name")
        opened_on = page.evaluate(FOCUS)
        page.click(f"{row} .iqa-cancel")
        settle(page, handled)
        by_click = page.evaluate(FOCUS)
        line_after_click = page.evaluate(LINE, 1)
        # and by Escape, from inside the form; the dialog stays open
        _choose_add(page, page.locator(f"{row} .cbx-input"), "Lemon curd tart")
        page.wait_for_selector(f"{row} .iqa-form .iqa-name")
        page.keyboard.press("Escape")
        settle(page, handled)
        by_escape = page.evaluate(FOCUS)
        still_open = page.evaluate(MODAL_SHOWN)
        line_after_escape = page.evaluate(LINE, 1)
    finally:
        page.close()

    assert opened_on["cls"] == "iqa-name"
    for focus in (by_click, by_escape):
        assert focus == {"cls": "cbx-input", "row": 1, "value": ""}
    for line in (line_after_click, line_after_escape):
        assert line["form"] is False and line["value"] == ""
    assert still_open is True


BILL_ROW = "#bill-lines tr:nth-child(1)"
# The stock label's geometry: the form's box, the label's, the checkbox's,
# and the text's lines (one client rect per line the text takes).
STOCK_LABEL = """() => { const b = document.querySelector('#bill-lines tr:nth-child(1) .iqa-form');
    const l = b.querySelector('.iqa-stock-row');
    const r = document.createRange(); r.selectNodeContents(l.querySelector('span'));
    const box = (e) => { const x = e.getBoundingClientRect(); return [x.left, x.top, x.right, x.bottom]; };
    return { form: box(b), label: box(l), check: box(l.querySelector('input')),
        lines: r.getClientRects().length, text: l.textContent.trim() }; }"""
BILL_LINE = """() => { const r = document.querySelector('#bill-lines tr:nth-child(1)');
    const a = r.querySelector('select.line-account');
    return { item: r.querySelector('select.line-item').value,
        account: a.value, accountText: a.selectedIndex >= 0 ? a.options[a.selectedIndex].textContent : '',
        rate: r.querySelector('.line-rate').value, form: !!r.querySelector('.iqa-form') }; }"""


def test_a_bill_line_asks_for_the_expense_account_and_posts_there(
    browser, company, books
):
    page, handled = _open_at(browser, company, "#/bills")
    page.evaluate("async () => { await BillsPage.showForm(); }")
    page.wait_for_function(MODAL_SHOWN, timeout=5000)
    settle(page, handled)
    try:
        vendor = page.get_by_role("combobox", name="Vendor", exact=True)
        _pick(page, vendor, "cascade", "Cascade Flour Mill")
        settle(page, handled)
        _choose_add(
            page, page.locator(f"{BILL_ROW} .cbx--line-item .cbx-input"), "Rye chaff"
        )
        page.wait_for_selector(f"{BILL_ROW} .iqa-form .iqa-name")
        page.select_option(f"{BILL_ROW} .iqa-type", "material")
        page.wait_for_function(
            f"() => document.querySelector('{BILL_ROW} .iqa-account').options.length > 1",
            timeout=5000,
        )
        form = page.evaluate(
            f"""() => {{ const b = document.querySelector('{BILL_ROW} .iqa-form');
                const s = b.querySelector('.iqa-account');
                return {{ label: s.getAttribute('aria-label'),
                    picked: s.options[s.selectedIndex].textContent,
                    kinds: [...s.options].map(o => o.dataset.number || '').filter(Boolean),
                    income: !!b.querySelector('.iqa-income'), rate: !!b.querySelector('.iqa-rate'),
                    cost: !!b.querySelector('.iqa-cost'),
                    stock: !b.querySelector('.iqa-stock-row').hidden }}; }}"""
        )
        # the stock label at the line's width, then at the panel's 200px minimum
        stock = [page.evaluate(STOCK_LABEL)]
        page.evaluate(
            f"() => {{ document.querySelector('{BILL_ROW} .iqa-form').style.width = '200px'; }}"
        )
        stock.append(page.evaluate(STOCK_LABEL))
        page.evaluate(
            f"() => {{ document.querySelector('{BILL_ROW} .iqa-form').style.width = ''; }}"
        )
        page.fill(f"{BILL_ROW} .iqa-cost", "6.25")
        page.click(f"{BILL_ROW} .iqa-save")
        page.wait_for_function(
            f"() => /^\\d+$/.test(document.querySelector('{BILL_ROW} select.line-item').value)",
            timeout=5000,
        )
        settle(page, handled)
        focus = page.evaluate(FOCUS)
        line = page.evaluate(BILL_LINE)
        page.fill(f"{BILL_ROW} .line-qty", "4")
        page.click("#bill-form button[type=submit]")
        page.wait_for_function(f"!({MODAL_SHOWN})()", timeout=5000)
        settle(page, handled)
    finally:
        page.close()

    assert form["label"] == "Cost of goods account"
    assert form["picked"] == "5100 - Materials Cost"
    # cost of goods only: no income or operating expense account is offered
    assert form["kinds"] and all(n[0] == "5" for n in form["kinds"]), form
    assert "6000" not in form["kinds"], form
    # the stock label sits on one line inside the panel, its box beside the
    # text, at the line's width and at the panel's narrowest (NEW-176: the
    # line table's input{width:100%} stretched the box and the words stacked)
    for seen in stock:
        assert seen["text"] == "Track quantity on hand (stock)"
        assert seen["lines"] == 1, seen
        assert seen["check"][2] - seen["check"][0] < 30, seen
        fl, ft, fr, fb = seen["form"]
        ll, lt, lr, lb = seen["label"]
        assert fl <= ll and lr <= fr and ft <= lt and lb <= fb, seen
    assert stock[1]["form"][2] - stock[1]["form"][0] == 200, stock[1]
    assert (form["income"], form["rate"], form["cost"], form["stock"]) == (
        False,
        False,
        True,
        True,
    )
    made = _ok(company.get("/api/items", params={"q": "Rye chaff"}))
    assert len(made) == 1, made
    item = made[0]
    accounts = {a["id"]: a["account_number"] for a in _ok(company.get("/api/accounts"))}
    assert accounts[item["expense_account_id"]] == "5100"
    assert item["income_account_id"] is None
    assert float(item["cost"]) == 6.25 and item["track_inventory"] is False
    # the line took the item's account and cost, and the focus is back on it
    assert (
        line["item"] == str(item["id"])
        and line["accountText"] == "5100 - Materials Cost"
    )
    assert float(line["rate"]) == 6.25 and line["form"] is False
    assert focus == {"cls": "cbx-input", "row": 1, "value": "Rye chaff"}
    bills = _ok(company.get("/api/bills"))
    newest = _ok(company.get(f"/api/bills/{max(b['id'] for b in bills)}"))
    assert [(ln["item_id"], accounts[ln["account_id"]]) for ln in newest["lines"]] == [
        (item["id"], "5100")
    ]
    assert float(newest["total"]) == 25.0


def test_a_read_only_or_accountant_sign_in_is_not_offered_add_item(
    browser, company, books
):
    page, handled = _open_at(browser, company, "#/invoices")
    got = {}
    try:
        for role in ("readonly", "accountant", "admin"):
            page.evaluate(
                "async (role) => { App.role = role; closeModal();"
                " await InvoicesPage.showForm(); }",
                role,
            )
            page.wait_for_function(MODAL_SHOWN, timeout=5000)
            settle(page, handled)
            got[role] = page.evaluate(LINE, 1)["options"]
        page.evaluate("() => { App.role = 'admin'; }")
    finally:
        page.close()

    assert "+ Add item" not in got["readonly"]
    assert "+ Add item" not in got["accountant"]
    assert "+ Add item" in got["admin"]


PO_ROW = "#po-lines tr:nth-child(1)"


def test_a_purchase_order_line_can_add_a_stock_item_at_its_cost(
    browser, company, books
):
    page, handled = _open_at(browser, company, "#/purchase-orders")
    page.evaluate("async () => { await PurchaseOrdersPage.showForm(); }")
    page.wait_for_function(MODAL_SHOWN, timeout=5000)
    settle(page, handled)
    try:
        _choose_add(
            page,
            page.locator(f"{PO_ROW} .cbx--line-item .cbx-input"),
            "Proofing basket",
        )
        page.wait_for_selector(f"{PO_ROW} .iqa-form .iqa-name")
        page.wait_for_function(
            f"() => document.querySelector('{PO_ROW} .iqa-account').options.length > 1",
            timeout=5000,
        )
        # a service is never stock; a product can be
        page.select_option(f"{PO_ROW} .iqa-type", "service")
        service = page.evaluate(
            f"""() => {{ const b = document.querySelector('{PO_ROW} .iqa-form');
                const s = b.querySelector('.iqa-account');
                return [b.querySelector('.iqa-stock-row').hidden,
                    s.options[s.selectedIndex].textContent]; }}"""
        )
        page.select_option(f"{PO_ROW} .iqa-type", "product")
        page.check(f"{PO_ROW} .iqa-stock")
        product = page.evaluate(
            f"""() => {{ const s = document.querySelector('{PO_ROW} .iqa-account');
                return s.options[s.selectedIndex].textContent; }}"""
        )
        page.fill(f"{PO_ROW} .iqa-cost", "14")
        page.click(f"{PO_ROW} .iqa-save")
        page.wait_for_function(
            f"() => /^\\d+$/.test(document.querySelector('{PO_ROW} select.line-item').value)",
            timeout=5000,
        )
        settle(page, handled)
        rate = page.input_value(f"{PO_ROW} .line-rate")
        focus = page.evaluate(FOCUS)
    finally:
        page.close()

    assert service == [True, "5300 - Subcontractor Costs"]
    assert product == "5000 - Cost of Goods Sold"
    made = _ok(company.get("/api/items", params={"q": "Proofing basket"}))
    assert len(made) == 1, made
    item = made[0]
    accounts = {a["id"]: a["account_number"] for a in _ok(company.get("/api/accounts"))}
    assert (item["item_type"], item["track_inventory"]) == ("product", True)
    assert accounts[item["expense_account_id"]] == "5000"
    assert item["income_account_id"] is None and float(item["cost"]) == 14
    assert float(rate) == 14
    assert focus == {"cls": "cbx-input", "row": 1, "value": "Proofing basket"}
