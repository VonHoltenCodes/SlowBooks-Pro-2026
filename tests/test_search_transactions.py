"""Search finds transactions by memo, number and amount (#695), as
QuickBooks Desktop's Find does: a check by its number, a journal entry by
its memo, an expense by its reference, a bill payment by its check number,
any of them by its total to the cent. Each hit carries the address it opens
(a bank entry, its register's account); a voided one is found and says so.
An accountant's and a read-only sign-in get the same results."""

from pathlib import Path

import pytest

from app.models.users import ROLE_ACCOUNTANT, ROLE_READONLY, User
from app.services import auth as auth_service

ROOT = Path(__file__).resolve().parents[1]


def _post(client, path, body):
    r = client.post(path, json=body)
    assert r.status_code in (200, 201), r.text
    return r.json()


@pytest.fixture
def books(client, db_session, seed_accounts, seed_customer):
    from app.models.contacts import Vendor

    acct = seed_accounts
    vendor = Vendor(name="Lumber Yard", is_active=True)
    db_session.add(vendor)
    db_session.commit()
    check = _post(
        client,
        "/api/banking/transactions",
        {
            "account_id": acct["1000"].id,
            "date": "2026-09-02",
            "amount": "-125.00",
            "category_account_id": acct["6000"].id,
            "payee": "City Water",
            "description": "August water",
            "check_number": "4471",
        },
    )
    journal = _post(
        client,
        "/api/journal",
        {
            "date": "2026-09-03",
            "description": "Accrue quarterly insurance",
            "lines": [
                {"account_id": acct["6300"].id, "debit": "333.33", "credit": "0"},
                {"account_id": acct["2000"].id, "debit": "0", "credit": "333.33"},
            ],
        },
    )
    expense = _post(
        client,
        "/api/expenses",
        {
            "date": "2026-09-04",
            "vendor_id": vendor.id,
            "expense_account_id": acct["6000"].id,
            "paid_from_account_id": acct["1000"].id,
            "amount": "48.10",
            "reference": "RCPT-8812",
            "memo": "Two-by-fours",
        },
    )
    bill_payment = _post(
        client,
        "/api/bill-payments",
        {
            "vendor_id": vendor.id,
            "date": "2026-09-05",
            "amount": 90.0,
            "method": "check",
            "check_number": "7305",
            "pay_from_account_id": acct["1000"].id,
        },
    )
    payment = _post(
        client,
        "/api/payments",
        {
            "customer_id": seed_customer.id,
            "date": "2026-09-06",
            "amount": "75.00",
            "check_number": "55021",
        },
    )
    voided = _post(
        client,
        "/api/journal",
        {
            "date": "2026-09-07",
            "description": "Wrong insurance accrual",
            "lines": [
                {"account_id": acct["6300"].id, "debit": "12.00", "credit": "0"},
                {"account_id": acct["2000"].id, "debit": "0", "credit": "12.00"},
            ],
        },
    )
    _post(client, f"/api/journal/{voided['id']}/void", {})
    from app.models.bills import BillPayment

    bp_txn = db_session.get(BillPayment, bill_payment["id"]).transaction_id
    return {
        "check": check["id"],
        "journal": journal["id"],
        "expense": expense["id"],
        "bill_payment": bp_txn,
        "bill_payment_doc": bill_payment["id"],
        "payment": payment["id"],
        "voided": voided["id"],
        "bank": acct["1000"].id,
    }


def _find(client, q):
    return client.get("/api/search", params={"q": q}).json()


def _txns(client, q):
    return {t["id"]: t for t in _find(client, q).get("transactions", [])}


def test_a_check_by_its_number_opens_on_its_register(client, books):
    hits = _txns(client, "4471")
    hit = hits[books["check"]]
    assert hit["source_type"] == "bank_entry" and hit["reference"] == "4471"
    assert hit["register_account_id"] == books["bank"]
    assert hit["total"] == 125.0 and hit["voided"] is False
    assert hit["payee"] == "City Water" and hit["date"] == "2026-09-02"


def test_a_journal_entry_by_its_memo(client, books):
    hits = _txns(client, "quarterly insur")
    assert set(hits) == {books["journal"]}
    hit = hits[books["journal"]]
    assert hit["link"] == f"/#/journal/{books['journal']}"
    assert hit["register_account_id"] is None


def test_an_expense_by_its_reference_and_its_memo(client, books):
    for q in ("RCPT-8812", "two-by"):
        hit = _txns(client, q)[books["expense"]]
        assert hit["source_type"] == "expense"
        assert hit["link"] == f"/#/expenses/{books['expense']}"
        assert hit["reference"] == "RCPT-8812" and hit["memo"] == "Two-by-fours"


def test_a_payment_by_its_check_number(client, books):
    hit = _txns(client, "7305")[books["bill_payment"]]
    assert hit["source_type"] == "bill_payment" and hit["reference"] == "7305"
    assert hit["link"] == f"/#/bill-payments/{books['bill_payment_doc']}"
    # a customer's payment is listed once, under Payments, not again here
    found = _find(client, "55021")
    assert {p["id"] for p in found["payments"]} == {books["payment"]}
    assert "transactions" not in found


def test_an_amount_to_the_cent(client, books):
    assert set(_txns(client, "333.33")) == {books["journal"]}
    assert set(_txns(client, "$48.10")) == {books["expense"]}
    assert not set(_txns(client, "333.34"))
    assert not set(_txns(client, "333"))


def test_a_voided_one_is_found_and_says_so_its_reversal_is_not(client, books):
    hits = _txns(client, "insurance accrual")
    assert set(hits) == {books["voided"]}
    assert hits[books["voided"]]["voided"] is True


def test_newest_first_five_at_most(client, books, seed_accounts):
    for day in range(10, 17):
        _post(
            client,
            "/api/journal",
            {
                "date": f"2026-09-{day}",
                "description": f"Batch accrual {day}",
                "lines": [
                    {
                        "account_id": seed_accounts["6300"].id,
                        "debit": "1.00",
                        "credit": "0",
                    },
                    {
                        "account_id": seed_accounts["2000"].id,
                        "debit": "0",
                        "credit": "1.00",
                    },
                ],
            },
        )
    dates = [t["date"] for t in _find(client, "Batch accrual")["transactions"]]
    assert dates == [f"2026-09-{d}" for d in (16, 15, 14, 13, 12)]


def _sign_in_as(client, db_session, username, role):
    password = "a-test-password-123"
    db_session.add(
        User(
            username=username,
            display_name=username.title(),
            password_hash=auth_service.hash_password(password),
            role=role,
            is_active=True,
        )
    )
    db_session.commit()
    client.post("/api/auth/logout")
    r = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("role", [ROLE_ACCOUNTANT, ROLE_READONLY])
def test_the_accountant_and_read_only_sign_ins_find_the_same(
    client, db_session, books, role
):
    queries = ("4471", "quarterly", "RCPT-8812", "7305", "333.33", "accrual")
    as_admin = {q: _find(client, q) for q in queries}
    _sign_in_as(client, db_session, f"user-{role}", role)
    for q in queries:
        r = client.get("/api/search", params={"q": q})
        assert r.status_code == 200, r.text
        assert r.json() == as_admin[q], q


def test_the_dropdown_lists_and_opens_them():
    js = (ROOT / "app/static/js/app.js").read_text(encoding="utf-8")
    assert "key: 'transactions', label: 'Transactions'" in js
    assert "App.openSearchTransaction(${Number(item.id)})" in js
    assert "BankingPage.hrefShowing(t.register_account_id, t.date)" in js
    assert "return t.voided ? `${said} (void)` : said;" in js
