"""Adding an item from the line being typed (#445), in playwright's Chromium
on the bakery's books of tests/test_theme_contrast.py.

On a new invoice the second line's item picker takes "+ Add item" with a
name typed that the books don't have: the quick add opens in the line's
cell with that name in it, Save creates the item (its code, its type's
income account), and the line shows it picked with its rate filled, as a
normal pick would. The first line is left as it was. Saving the invoice
stores the new item on its line.

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
        # a name the books don't have: "+ Add item" is what Enter takes,
        # and the quick add opens with the name in it
        _pick(page, page.locator(f"{ROW2} .cbx-input"), NAME, "+ Add item")
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
        first = page.evaluate(LINE, 1)
        second = page.evaluate(LINE, 2)
        page.click("#invoice-form button[type=submit]")
        page.wait_for_function(f"!({MODAL_SHOWN})()", timeout=5000)
        settle(page, handled)
    finally:
        page.close()

    assert handed_on == NAME
    assert income == "4200 - Material Income"
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
