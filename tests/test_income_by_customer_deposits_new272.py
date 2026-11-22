"""Round 24, NEW-272: Income by Customer counts Make Deposits' lines on an
income account, so its Sales ties to the P&L's income on both bases.

A deposit's line for money never invoiced (#792) on an income account adds
to the P&L's income, but the report read invoices, credit memos, unapplied
payments and register entries naming a customer (#1065) only: a deposit of
40.00 from a named customer and 25.00 from nobody gave a P&L income of
65.00 and no row at all. Every line recorded straight from a bank or card
account to an income account (a register entry or bank-feed line, a
deposit's line, a card charge, an expense) is now Sales and Paid: under the
customer the line names, else on one "No customer" row."""

import pytest

from app.models.accounts import Account
from app.models.contacts import Customer

DEC = {"start_date": "2026-12-01", "end_date": "2026-12-31"}


def _ids(db_session):
    def num(n):
        return db_session.query(Account).filter(Account.account_number == n).one().id

    return num("1000"), num("4000")


def _customer(db_session, name="Cust One"):
    c = Customer(name=name, is_active=True)
    db_session.add(c)
    db_session.commit()
    return c.id


def _report(client, basis):
    r = client.get("/api/reports/income-by-customer", params={**DEC, "basis": basis})
    assert r.status_code == 200, r.text
    return r.json()


def _pl_income(client, basis):
    r = client.get("/api/reports/profit-loss", params={**DEC, "basis": basis})
    assert r.status_code == 200, r.text
    return r.json()["total_income"]


def _make_deposit(client, bank, lines, when="2026-12-10"):
    r = client.post(
        "/api/deposits",
        json={
            "deposit_to_account_id": bank,
            "date": when,
            "reference": "SLIP-272",
            "line_ids": [],
            "other_lines": lines,
        },
    )
    assert r.status_code in (200, 201), r.text
    return r.json()["transaction_id"]


@pytest.mark.parametrize("basis", ["accrual", "cash"])
def test_deposit_lines_on_income_are_sales_under_the_customer_or_no_customer(
    client, db_session, seed_accounts, basis
):
    bank, income = _ids(db_session)
    cid = _customer(db_session)
    _make_deposit(
        client,
        bank,
        [
            {
                "account_id": income,
                "customer_id": cid,
                "amount": "40.00",
                "memo": "Walk-in repair, not invoiced",
            },
            {"account_id": income, "amount": "25.00", "received_from": "Cash"},
        ],
    )
    body = _report(client, basis)
    rows = {i["customer_name"]: i for i in body["items"]}
    assert rows["Cust One"]["total_sales"] == 40.00
    assert rows["Cust One"]["total_paid"] == 40.00
    assert rows["No customer"]["total_sales"] == 25.00
    assert rows["No customer"]["customer_id"] is None
    assert rows["No customer"]["invoice_count"] == 0
    assert body["total_sales"] == _pl_income(client, basis) == 65.00
    # the row rule still holds on every row
    for i in body["items"]:
        if basis == "cash":
            assert (
                i["total_sales"] + i["total_other"] + i["total_tax"] == i["total_paid"]
            )
        else:
            assert i["total_balance"] == 0


def test_a_voided_deposit_counts_for_nothing(client, db_session, seed_accounts):
    bank, income = _ids(db_session)
    txn = _make_deposit(client, bank, [{"account_id": income, "amount": "25.00"}])
    r = client.post(f"/api/deposits/{txn}/void")
    assert r.status_code == 200, r.text
    for basis in ("accrual", "cash"):
        assert _report(client, basis)["total_sales"] == 0


def test_a_register_entry_with_no_customer_is_on_the_no_customer_row(
    client, db_session, seed_accounts
):
    bank, income = _ids(db_session)
    r = client.post(
        "/api/banking/transactions",
        json={
            "account_id": bank,
            "date": "2026-12-04",
            "amount": "90.00",
            "category_account_id": income,
            "payee": "Walk-in",
        },
    )
    assert r.status_code == 201, r.text
    for basis in ("accrual", "cash"):
        body = _report(client, basis)
        rows = {i["customer_name"]: i for i in body["items"]}
        assert rows["No customer"]["total_sales"] == 90.00
        assert body["total_sales"] == _pl_income(client, basis) == 90.00


def test_a_nonprofit_names_the_row_in_its_own_words(client, db_session, seed_accounts):
    r = client.put("/api/settings", json={"company_type": "nonprofit"})
    assert r.status_code == 200, r.text
    bank, income = _ids(db_session)
    _make_deposit(client, bank, [{"account_id": income, "amount": "10.00"}])
    names = [i["customer_name"] for i in _report(client, "accrual")["items"]]
    assert names == ["No donor"]


@pytest.mark.parametrize("way", ["card charge", "expense"])
def test_money_out_to_an_income_account_lowers_the_no_customer_row(
    client, db_session, seed_accounts, way
):
    """A card charge or an expense booked to an income account (a refund
    given) lowers the P&L's income; it lowers Sales and Paid the same."""
    bank, income = _ids(db_session)
    if way == "card charge":
        r = client.post(
            "/api/cc-charges",
            json={
                "date": "2026-12-05",
                "payee": "Refund",
                "account_id": income,
                "amount": "30",
            },
        )
    else:
        r = client.post(
            "/api/expenses",
            json={
                "date": "2026-12-05",
                "payee": "Refund",
                "expense_account_id": income,
                "paid_from_account_id": bank,
                "amount": "30",
            },
        )
    assert r.status_code in (200, 201), r.text
    for basis in ("accrual", "cash"):
        body = _report(client, basis)
        assert body["total_sales"] == _pl_income(client, basis) == -30.00
        assert body["total_paid"] == -30.00
