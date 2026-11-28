"""Editing a register entry in the browser (#388 review): the reconciled
note as the form opens, "Change it anyway" saving what the form holds then,
"Keep it as it is" keeping the focus in the dialog, the job and an archived
class surviving an unchanged save, and no Edit for a read-only or an
accountant's sign-in.

Reuses the contrast sweep's books and harness (tests/test_theme_contrast.py).
Skipped, as one module, where playwright or its Chromium is not installed.
"""

from datetime import date
from decimal import Decimal

import pytest

pytest.importorskip("playwright.sync_api")

from app.models.banking import Reconciliation, ReconciliationStatus  # noqa: E402
from app.models.transactions import TransactionLine  # noqa: E402
from tests.test_dialog_contrast import OPEN  # noqa: E402
from tests.test_readonly_browser import _reader, _signed_in, _user  # noqa: E402
from tests.test_theme_contrast import (  # noqa: E402,F401  (the fixtures)
    _open,
    _visit,
    browser_fixture,
    company_fixture,
    settle,
)


@pytest.fixture(autouse=True)
def _no_edit_question(company):
    """These are about what an edit saves: the "Warn when editing a
    transaction" question before it, and the date question the books' fixed
    dates will draw as they age (#481, tests/test_browser_transaction_warnings.py),
    are turned off for them."""
    company.put(
        "/api/settings",
        json={"warn_edit_transaction": "false", "warn_date_past": "false"},
    )


def _entry(client, books, seed_accounts, **over):
    body = {
        "account_id": books["checking"],
        "date": "2026-11-20",
        "amount": "-125.00",
        "category_account_id": seed_accounts["6900"].id,
        "payee": "City Water",
        "description": "November water",
        "check_number": "1042",
    }
    body.update(over)
    r = client.post("/api/banking/transactions", json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _reconcile_line(db, txn_id, account_id):
    """The entry's bank line held by a completed reconciliation (made
    directly: the bakery's register has years of lines to tick)."""
    recon = Reconciliation(
        account_id=account_id,
        statement_date=date(2026, 11, 30),
        statement_balance=Decimal("0"),
        beginning_balance=Decimal("0"),
        status=ReconciliationStatus.COMPLETED,
    )
    db.add(recon)
    db.flush()
    line = (
        db.query(TransactionLine)
        .filter_by(transaction_id=txn_id, account_id=account_id)
        .one()
    )
    line.cleared = True
    line.reconciliation_id = recon.id
    db.commit()
    return recon.id


def _open_edit(browser, company, handled_route, txn_id, account_id):
    page, handled = _open(browser, company)
    _visit(page, handled, handled_route)
    page.evaluate(
        f"async () => {{ await BankingPage.editEntry({txn_id}, {account_id}); }}"
    )
    page.wait_for_function(OPEN, timeout=5000)
    settle(page, handled)
    return page, handled


def test_change_it_anyway_saves_the_form_as_it_is_then(
    browser, company, books, client, db_session, seed_accounts
):
    checking = books["checking"]
    txn = _entry(client, books, seed_accounts)
    recon = _reconcile_line(db_session, txn, checking)
    page, handled = _open_edit(browser, company, f"#/banking/{checking}", txn, checking)
    try:
        opened = page.evaluate("""() => ({
                note: (document.getElementById('entry-reconciled-note') || {}).textContent,
                amountLocked: document.querySelector('#modal-body form [name=amount]').readOnly,
                dateLocked: document.querySelector('#modal-body form [name=date]').readOnly,
            })""")
        assert opened == {
            "note": "This entry was reconciled on the Nov 30, 2026 statement, so its "
            "amount and date stay as they are (a reconciled entry cannot be voided "
            "either). Its payee, memo, category, class and job can still be "
            "corrected.",
            "amountLocked": True,
            "dateLocked": True,
        }
        page.select_option(
            "#modal-body form [name=category_account_id]",
            str(seed_accounts["6400"].id),
        )
        page.evaluate(
            "() => document.querySelector('#modal-body form').requestSubmit()"
        )
        page.wait_for_selector("#entry-reconciled-warning")
        settle(page, handled)
        # a change made after the warning is saved too
        page.fill("#modal-body form [name=description]", "November water, read twice")
        # the start-up splash sits over the page in this harness: the
        # button is clicked as the person would, from the page's side
        page.evaluate(
            """() => [...document.querySelectorAll('#entry-reconciled-warning button')]
                .find(b => b.textContent === 'Change it anyway').click()"""
        )
        page.wait_for_function(f"() => !({OPEN})()", timeout=5000)
        settle(page, handled)
        e = client.get(f"/api/banking/entries/{txn}").json()
        assert e["category_account_id"] == seed_accounts["6400"].id
        assert e["description"] == "November water, read twice"
        assert e["amount"] == "-125.00" and e["reconciled_on"] == "2026-11-30"
        db_session.expire_all()
        line = (
            db_session.query(TransactionLine)
            .filter_by(transaction_id=txn, account_id=checking)
            .one()
        )
        assert line.reconciliation_id == recon
    finally:
        page.close()


def test_keep_it_as_it_is_returns_the_focus_to_the_form(
    browser, company, books, client, db_session, seed_accounts
):
    checking = books["checking"]
    txn = _entry(client, books, seed_accounts)
    _reconcile_line(db_session, txn, checking)
    page, handled = _open_edit(browser, company, f"#/banking/{checking}", txn, checking)
    try:
        page.select_option(
            "#modal-body form [name=category_account_id]",
            str(seed_accounts["6400"].id),
        )
        page.evaluate(
            "() => document.querySelector('#modal-body form').requestSubmit()"
        )
        page.wait_for_selector("#entry-reconciled-warning")
        settle(page, handled)
        # the start-up splash sits over the page in this harness: the
        # button is clicked as the person would, from the page's side
        page.evaluate(
            """() => [...document.querySelectorAll('#entry-reconciled-warning button')]
                .find(b => b.textContent === 'Keep it as it is').click()"""
        )
        after = page.evaluate("""() => ({
                warning: !!document.getElementById('entry-reconciled-warning'),
                inDialog: !!document.activeElement.closest('#modal-body'),
                // the category's picker: the select, or its type-ahead box
                category: (() => {
                    const sel = document.querySelector('#modal-body form [name=category_account_id]');
                    const at = document.activeElement;
                    return at === sel || (!!sel._cbx && at === sel._cbx.box);
                })(),
            })""")
        assert after == {"warning": False, "inDialog": True, "category": True}
        assert (
            client.get(f"/api/banking/entries/{txn}").json()["category_account_id"]
            == seed_accounts["6900"].id
        )
    finally:
        page.close()


def test_an_unchanged_save_keeps_the_job_and_an_archived_class(
    browser, company, books, client, db_session, seed_accounts
):
    checking = books["checking"]
    cls = client.post("/api/classes", json={"name": "Old route"}).json()["id"]
    txn = _entry(client, books, seed_accounts, class_id=cls, job_id=books["job"])
    assert client.put(f"/api/classes/{cls}", json={"is_archived": True}).is_success
    before = client.get(f"/api/banking/entries/{txn}").json()
    page, handled = _open_edit(browser, company, f"#/banking/{checking}", txn, checking)
    try:
        shown = page.evaluate(
            "() => { const s = document.querySelector('#modal-body form [name=class_id]');"
            " return s ? [s.value, s.selectedOptions[0].textContent] : null; }"
        )
        assert shown == [str(cls), "Old route (archived)"]
        page.evaluate(
            "() => document.querySelector('#modal-body form').requestSubmit()"
        )
        page.wait_for_function(f"() => !({OPEN})()", timeout=5000)
        settle(page, handled)
        after = client.get(f"/api/banking/entries/{txn}").json()
        assert after["job_id"] == books["job"] and after["class_id"] == cls
        assert after == before
    finally:
        page.close()


@pytest.mark.parametrize("role", ["readonly", "accountant"])
def test_a_locked_sign_in_sees_no_edit_on_a_register_entry(
    role, browser, company, db_session, books, client, seed_accounts
):
    checking = books["checking"]
    txn = _entry(client, books, seed_accounts)
    signed_in = (
        _reader(company, db_session)
        if role == "readonly"
        else _user(company, db_session, "cpa", "accountant")
    )
    page, handled = _signed_in(browser, signed_in, role)
    try:
        _visit(page, handled, f"#/banking/{checking}")
        row = page.locator(
            f"#page-content tbody tr:has(button[onclick*='editEntry({txn},'])"
        )
        # the register's row for that entry, once drawn
        row.wait_for(state="attached", timeout=5000)
        assert row.count() == 1
        assert not row.locator("button:has-text('Edit')").is_visible()
    finally:
        page.close()
    # the administrator sees it
    page, handled = _open(browser, company)
    try:
        _visit(page, handled, f"#/banking/{checking}")
        edit = page.locator(f"button[onclick*='editEntry({txn},']")
        edit.wait_for(state="attached", timeout=5000)
        assert edit.count() == 1 and edit.is_visible()
    finally:
        page.close()
