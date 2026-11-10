"""Edit on an expense (#745, #746).

#745: Edit (void and replace, #689) drew each line's Account picker from the
active Expense/COGS accounts only, so a saved line on any other account (a
mortgage payment's principal on a long-term liability, its escrow on a bank
or asset account, or an account since made inactive) showed "Choose...",
and a memo-only edit could not keep the original postings. Every saved
line's account is kept in the picker now; a new expense's is unchanged.

#746: the Expenses list and the expense's View dialog offer Edit beside View
and Void, and not on a void expense.

Reuses the contrast sweep's books and harness (tests/test_theme_contrast.py).
Skipped, as one module, where playwright or its Chromium is not installed.
"""

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_dialog_contrast import OPEN  # noqa: E402
from tests.test_theme_contrast import (  # noqa: E402,F401  (the fixtures)
    _open,
    _visit,
    browser_fixture,
    company_fixture,
    settle,
)

REPLACING = "() => !!document.querySelector('#expense-form[data-replace-of]')"
PICKERS = """() => [...document.querySelectorAll('#expense-lines tr')].map(r => {
        const sel = r.querySelector('.line-account');
        return {
            account: sel.value,
            shown: sel.options[sel.selectedIndex].textContent,
            memo: r.querySelector('.line-desc').value,
            amount: r.querySelector('.line-amt').value,
        };
    })"""


@pytest.fixture(autouse=True)
def _no_edit_question(company):
    """The "Warn when editing a transaction" and date questions are turned
    off: these are about what Edit shows and saves (as in
    tests/test_bill_edit_browser.py)."""
    company.put(
        "/api/settings",
        json={"warn_edit_transaction": "false", "warn_date_past": "false"},
    )


@pytest.fixture
def mortgage(client, seed_accounts):
    """A paid mortgage payment split over interest (an expense), principal (a
    long-term liability), escrow (a bank account) and a fee on an expense
    account made inactive after it was posted."""
    a = {n: acct.id for n, acct in seed_accounts.items()}
    lines = [
        {"account_id": a["6300"], "amount": "400.00", "description": "Interest"},
        {"account_id": a["2400"], "amount": "900.00", "description": "Principal"},
        {"account_id": a["1010"], "amount": "250.00", "description": "Escrow"},
        {"account_id": a["6950"], "amount": "15.00", "description": "Late fee"},
    ]
    r = client.post(
        "/api/expenses",
        json={
            "date": "2026-09-10",
            "payee": "Harbor Mortgage",
            "paid_from_account_id": a["1000"],
            "amount": "1565.00",
            "reference": "MTG-09",
            "memo": "September payment",
            "lines": lines,
        },
    )
    assert r.status_code in (200, 201), r.text
    r2 = client.put(f"/api/accounts/{a['6950']}", json={"is_active": False})
    assert r2.status_code == 200, r2.text
    assert r2.json()["is_active"] is False
    return {"id": r.json()["id"], "lines": lines, "a": a}


@pytest.fixture
def voided(client, seed_accounts):
    a = {n: acct.id for n, acct in seed_accounts.items()}
    r = client.post(
        "/api/expenses",
        json={
            "date": "2026-09-11",
            "payee": "Twice Entered Co.",
            "expense_account_id": a["6400"],
            "paid_from_account_id": a["1000"],
            "amount": "7.77",
        },
    )
    assert r.status_code in (200, 201), r.text
    x = r.json()["id"]
    assert client.post(f"/api/expenses/{x}/void").status_code == 200
    return x


def _picked(page, selector):
    return page.evaluate(
        """(sel) => [...document.querySelectorAll(sel)].map(s => s.value)""",
        selector,
    )


def test_a_card_charges_edit_keeps_its_account_outside_the_list(
    browser, company, client, seed_accounts
):
    """The same rule on the card charge's Edit: a charge on a liability
    account, and one on an expense account made inactive since."""
    a = {n: acct.id for n, acct in seed_accounts.items()}
    ids = []
    for account in (a["2400"], a["6950"]):
        r = client.post(
            "/api/cc-charges",
            json={
                "date": "2026-09-12",
                "payee": "Harbor Mortgage",
                "account_id": account,
                "card_account_id": a["2100"],
                "amount": "50.00",
                "memo": "card",
            },
        )
        assert r.status_code in (200, 201), r.text
        ids.append(r.json()["transaction_id"])
    assert (
        client.put(f"/api/accounts/{a['6950']}", json={"is_active": False}).status_code
        == 200
    )
    page, handled = _open(browser, company)
    try:
        _visit(page, handled, "#/cc-charges")
        for account, charge in zip((a["2400"], a["6950"]), ids):
            page.evaluate(f"async () => {{ await CCChargesPage.replace({charge}); }}")
            settle(page, handled)
            assert _picked(page, "#modal-body form [name=account_id]") == [str(account)]
            page.evaluate("() => closeModal()")
    finally:
        page.close()


def test_a_vendor_credits_edit_keeps_its_lines_accounts_outside_the_list(
    browser, company, client, seed_accounts, books
):
    """And on the vendor credit's: a line on a bank account and one on an
    expense account made inactive since."""
    a = {n: acct.id for n, acct in seed_accounts.items()}
    lines = [
        {"description": "refund", "quantity": 1, "rate": 20.0, "account_id": a[n]}
        for n in ("1010", "6950", "6400")
    ]
    r = client.post(
        "/api/vendor-credits",
        json={
            "vendor_id": books["vendor"],
            "date": "2026-09-12",
            "notes": "kept",
            "lines": lines,
        },
    )
    assert r.status_code in (200, 201), r.text
    assert (
        client.put(f"/api/accounts/{a['6950']}", json={"is_active": False}).status_code
        == 200
    )
    page, handled = _open(browser, company)
    try:
        _visit(page, handled, "#/vendor-credits")
        page.evaluate(
            f"async () => {{ await VendorCreditsPage.replace({r.json()['id']}); }}"
        )
        settle(page, handled)
        assert _picked(page, "#vc-lines .line-account") == [
            str(l["account_id"]) for l in lines
        ]
        page.evaluate("() => closeModal()")
        # a new credit's picker is still the active expense/COGS list
        page.evaluate("async () => { await VendorCreditsPage.showForm(); }")
        settle(page, handled)
        offered = page.evaluate(
            """() => [...document.querySelector('#vc-lines .line-account').options]
                .map(o => o.value).filter(Boolean)"""
        )
        assert str(a["1010"]) not in offered and str(a["6950"]) not in offered
        assert str(a["6400"]) in offered
    finally:
        page.close()


def _row(page, payee):
    return page.locator(f"#page-content tbody tr:has-text('{payee}')").first


def _buttons(locator):
    return [t.strip() for t in locator.locator("button").all_inner_texts()]


def test_edit_keeps_every_saved_lines_account_in_the_picker(
    browser, company, client, mortgage
):
    page, handled = _open(browser, company)
    page.on("dialog", lambda d: d.accept())
    try:
        _visit(page, handled, "#/expenses")
        # Edit, as the drill-down's Edit (#689) and the row's (#746) call it
        page.evaluate(
            f"async () => {{ await ExpensesPage.replace({mortgage['id']}); }}"
        )
        page.wait_for_function(REPLACING, timeout=5000)
        settle(page, handled)
        shown = page.evaluate(PICKERS)
        assert [s["account"] for s in shown] == [
            str(l["account_id"]) for l in mortgage["lines"]
        ]
        assert all(s["shown"] != "Choose..." for s in shown)
        assert [s["memo"] for s in shown] == [
            l["description"] for l in mortgage["lines"]
        ]
        assert [s["amount"] for s in shown] == [l["amount"] for l in mortgage["lines"]]

        # a memo-only edit: the replacement posts to the same accounts
        page.fill("#expense-form [name=memo]", "September payment (escrow note)")
        page.evaluate("() => document.getElementById('expense-form').requestSubmit()")
        settle(page, handled)
        page.wait_for_function(f"() => !({OPEN})()", timeout=5000)
        settle(page, handled)
        old = client.get(f"/api/expenses/{mortgage['id']}").json()
        assert old["status"] == "void"
        new = [
            x
            for x in client.get("/api/expenses").json()
            if x["payee"] == "Harbor Mortgage" and x["status"] != "void"
        ]
        assert len(new) == 1
        assert new[0]["memo"] == "September payment (escrow note)"
        assert [
            (l["account_id"], float(l["amount"]), l["description"])
            for l in new[0]["lines"]
        ] == [
            (l["account_id"], float(l["amount"]), l["description"])
            for l in mortgage["lines"]
        ]
    finally:
        page.close()


def test_a_new_expenses_picker_lists_only_active_expense_and_cogs_accounts(
    browser, company, client, mortgage
):
    a = mortgage["a"]
    page, handled = _open(browser, company)
    try:
        _visit(page, handled, "#/expenses")
        # Edit first, so a kept account cannot carry over into the new form
        page.evaluate(
            f"async () => {{ await ExpensesPage.replace({mortgage['id']}); }}"
        )
        page.evaluate("() => closeModal()")
        page.evaluate("async () => { await ExpensesPage.showForm(); }")
        page.wait_for_function(OPEN, timeout=5000)
        settle(page, handled)
        offered = set(
            page.evaluate(
                """() => [...document.querySelector('#expense-lines .line-account').options]
                .map(o => o.value).filter(Boolean)"""
            )
        )
        accounts = client.get("/api/accounts").json()
        expected = {
            str(x["id"])
            for x in accounts
            if x["account_type"] in ("expense", "cogs") and x["is_active"]
        }
        assert offered == expected
        for n in ("2400", "1010", "6950"):
            assert str(a[n]) not in offered
        assert str(a["6300"]) in offered
    finally:
        page.close()


def test_the_list_offers_edit_beside_view_and_void_and_not_on_a_void_expense(
    browser, company, mortgage, voided
):
    page, handled = _open(browser, company)
    try:
        _visit(page, handled, "#/expenses")
        assert _buttons(_row(page, "Harbor Mortgage")) == ["View", "Edit", "Void"]
        assert _buttons(_row(page, "Twice Entered Co.")) == ["View"]
        edit = _row(page, "Harbor Mortgage").locator("button:has-text('Edit')")
        assert edit.get_attribute("data-write") is not None
        assert edit.get_attribute("data-destructive") is None

        # Edit opens the replace form for that expense, as the drill-down's does
        edit.dispatch_event("click")
        page.wait_for_function(REPLACING, timeout=5000)
        settle(page, handled)
        form = page.evaluate("""() => {
                const f = document.getElementById('expense-form');
                return { of: f.dataset.replaceOf, kind: f.dataset.replaceKind,
                         submit: f.querySelector('button[type=submit]').textContent };
            }""")
        assert form == {
            "of": str(mortgage["id"]),
            "kind": "expense",
            "submit": "Void and Replace",
        }
    finally:
        page.close()


def test_the_view_dialog_offers_edit_and_not_on_a_void_expense(
    browser, company, mortgage, voided
):
    page, handled = _open(browser, company)
    try:
        _visit(page, handled, "#/expenses")
        page.evaluate(
            f"async () => {{ await ExpensesPage.showDetail({mortgage['id']}); }}"
        )
        page.wait_for_function(OPEN, timeout=5000)
        settle(page, handled)
        footer = page.locator("#modal-body .form-actions")
        assert "Edit" in _buttons(footer) and "Void" in _buttons(footer)
        assert (
            footer.locator("button:has-text('Edit')").get_attribute("data-write")
            is not None
        )
        footer.locator("button:has-text('Edit')").dispatch_event("click")
        page.wait_for_function(REPLACING, timeout=5000)
        assert page.evaluate(
            "() => document.getElementById('expense-form').dataset.replaceOf"
        ) == str(mortgage["id"])
        page.evaluate("() => closeModal()")

        page.evaluate(f"async () => {{ await ExpensesPage.showDetail({voided}); }}")
        page.wait_for_function(OPEN, timeout=5000)
        settle(page, handled)
        footer = page.locator("#modal-body .form-actions")
        assert "Edit" not in _buttons(footer) and "Void" not in _buttons(footer)
    finally:
        page.close()
