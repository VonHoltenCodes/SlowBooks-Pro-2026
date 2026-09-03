"""Expenses — one-step paid receipts (DR expense, CR bank/credit card)."""

from decimal import Decimal

import pytest

from app.models.transactions import Transaction


@pytest.fixture
def vendor(client):
    r = client.post("/api/vendors", json={"name": "Sweet Forest Cafe"})
    assert r.status_code == 201, r.text
    return r.json()


def _post(client, seed_accounts, **overrides):
    body = {
        "date": "2026-09-02",
        "expense_account_id": seed_accounts["6000"].id,
        "paid_from_account_id": seed_accounts["1000"].id,
        "amount": "30.30",
        "reference": "rcpt 1187",
        "memo": "team lunch",
    }
    body.update(overrides)
    return client.post("/api/expenses", json=body)


def test_create_expense_posts_balanced_entry(client, db_session, seed_accounts, vendor):
    r = _post(client, seed_accounts, vendor_id=vendor["id"])
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["payee"] == "Sweet Forest Cafe"
    assert body["vendor_id"] == vendor["id"]
    assert body["amount"] == 30.30
    assert body["expense_account_name"] == seed_accounts["6000"].name
    assert body["paid_from_account_name"] == seed_accounts["1000"].name
    assert body["memo"] == "team lunch"

    txn = db_session.query(Transaction).filter(Transaction.id == body["id"]).one()
    assert txn.source_type == "expense"
    assert txn.source_id == vendor["id"]
    assert txn.description == "Expense: Sweet Forest Cafe"
    debits = {ln.account_id: ln.debit for ln in txn.lines if ln.debit > 0}
    credits = {ln.account_id: ln.credit for ln in txn.lines if ln.credit > 0}
    assert debits == {seed_accounts["6000"].id: Decimal("30.30")}
    assert credits == {seed_accounts["1000"].id: Decimal("30.30")}


def test_expense_paid_from_credit_card(client, seed_accounts):
    r = _post(client, seed_accounts, paid_from_account_id=seed_accounts["2100"].id)
    assert r.status_code == 201, r.text
    assert r.json()["paid_from_account_name"] == seed_accounts["2100"].name


def test_expense_without_vendor_uses_payee(client, seed_accounts):
    r = _post(client, seed_accounts, payee="Corner Hardware", memo=None)
    assert r.status_code == 201, r.text
    assert r.json()["payee"] == "Corner Hardware"
    assert r.json()["vendor_id"] is None


def test_expense_rejects_bad_paid_from(client, seed_accounts):
    # Paying an expense "from" another expense account is nonsense.
    r = _post(client, seed_accounts, paid_from_account_id=seed_accounts["6100"].id)
    assert r.status_code == 400
    r = _post(client, seed_accounts, paid_from_account_id=999999)
    assert r.status_code == 404
    r = _post(client, seed_accounts, amount="0")
    assert r.status_code == 400
    r = _post(client, seed_accounts, vendor_id=999999)
    assert r.status_code == 404
    r = _post(client, seed_accounts, expense_account_id=999999)
    assert r.status_code == 404


def test_list_and_get_expenses(client, seed_accounts, vendor):
    assert client.get("/api/expenses").json() == []
    a = _post(client, seed_accounts, vendor_id=vendor["id"]).json()
    b = _post(client, seed_accounts, date="2026-09-03", amount="7.42").json()
    listed = client.get("/api/expenses").json()
    assert [x["id"] for x in listed] == [b["id"], a["id"]]
    assert client.get(f"/api/expenses/{a['id']}").json()["payee"] == "Sweet Forest Cafe"
    assert client.get("/api/expenses/999999").status_code == 404


def test_expense_respects_closing_date(client, seed_accounts):
    r = client.put("/api/settings", json={"closing_date": "2026-09-10"})
    assert r.status_code == 200, r.text
    r = _post(client, seed_accounts, date="2026-09-02")
    assert r.status_code == 403, r.text


def test_void_expense_posts_reversal_and_marks_status(
    client, db_session, seed_accounts, vendor
):
    body = _post(client, seed_accounts, vendor_id=vendor["id"]).json()
    assert body["status"] == "recorded"

    r = client.post(f"/api/expenses/{body['id']}/void")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "void"

    reversal = (
        db_session.query(Transaction)
        .filter(
            Transaction.source_type == "expense_void",
            Transaction.source_id == body["id"],
        )
        .one()
    )
    assert reversal.description == "VOID Expense: Sweet Forest Cafe"
    debits = {ln.account_id: ln.debit for ln in reversal.lines if ln.debit > 0}
    credits = {ln.account_id: ln.credit for ln in reversal.lines if ln.credit > 0}
    assert debits == {seed_accounts["1000"].id: Decimal("30.30")}
    assert credits == {seed_accounts["6000"].id: Decimal("30.30")}

    # The list shows it void, the reversal itself is not an expense row,
    # and a second void is refused.
    listed = client.get("/api/expenses").json()
    assert [(x["id"], x["status"]) for x in listed] == [(body["id"], "void")]
    assert client.get(f"/api/expenses/{body['id']}").json()["status"] == "void"
    r = client.post(f"/api/expenses/{body['id']}/void")
    assert r.status_code == 400
    assert "already void" in r.json()["detail"]


def test_void_expense_respects_closing_date(client, seed_accounts):
    body = _post(client, seed_accounts, date="2026-01-15").json()
    r = client.put("/api/settings", json={"closing_date": "2026-06-30"})
    assert r.status_code == 200, r.text
    r = client.post(f"/api/expenses/{body['id']}/void")
    assert r.status_code == 403, r.text
