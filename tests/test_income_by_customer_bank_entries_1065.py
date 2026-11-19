"""#1065: Income by Customer counts money a customer paid that was recorded
straight from the bank.

The report read invoices alone, so a register deposit (or a bank-feed line
added) with a customer named on its income line showed under nobody: a
bookkeeper who recorded sales from the feed saw no revenue by customer. Each
such entry is that customer's Sales and Paid, on the accrual and the cash
basis; a voided one counts for nothing, and invoice figures are unchanged."""

from app.models.accounts import Account
from app.models.contacts import Customer

OCT = {"start_date": "2026-10-01", "end_date": "2026-10-31"}


def _ids(db_session):
    bank = db_session.query(Account).filter(Account.account_number == "1000").one()
    income = db_session.query(Account).filter(Account.account_number == "4000").one()
    return bank.id, income.id


def _customer(db_session, name):
    c = Customer(name=name, is_active=True)
    db_session.add(c)
    db_session.commit()
    return c.id


def _deposit(client, bank_id, income_id, cid, amount, when="2026-10-14"):
    r = client.post(
        "/api/banking/transactions",
        json={
            "account_id": bank_id,
            "date": when,
            "amount": amount,
            "category_account_id": income_id,
            "payee": "Counter sale",
            "customer_id": cid,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _rows(client, basis):
    r = client.get("/api/reports/income-by-customer", params={**OCT, "basis": basis})
    assert r.status_code == 200, r.text
    body = r.json()
    return {i["customer_id"]: i for i in body["items"]}, body


def test_a_deposit_named_to_a_customer_is_that_customers_sales(
    client, db_session, seed_accounts
):
    bank_id, income_id = _ids(db_session)
    cid = _customer(db_session, "Harbor Cafe")
    _deposit(client, bank_id, income_id, cid, "300.00")
    _deposit(client, bank_id, income_id, cid, "50.00", when="2026-10-20")
    # outside the period: not this month's
    _deposit(client, bank_id, income_id, cid, "75.00", when="2026-11-02")
    for basis in ("accrual", "cash"):
        rows, body = _rows(client, basis)
        assert rows[cid]["total_sales"] == 350.00, (basis, rows)
        assert rows[cid]["total_paid"] == 350.00
        assert rows[cid]["total_balance"] == 0
        assert rows[cid]["invoice_count"] == 0
        assert body["total_sales"] == 350.00


def test_a_voided_deposit_counts_for_nothing(client, db_session, seed_accounts):
    bank_id, income_id = _ids(db_session)
    cid = _customer(db_session, "Harbor Cafe")
    keep = _deposit(client, bank_id, income_id, cid, "300.00")
    gone = _deposit(client, bank_id, income_id, cid, "120.00")
    assert keep != gone
    r = client.post(f"/api/banking/entries/{gone}/void")
    assert r.status_code == 200, r.text
    for basis in ("accrual", "cash"):
        rows, _ = _rows(client, basis)
        assert rows[cid]["total_sales"] == 300.00, (basis, rows)


def test_a_deposit_with_no_customer_and_one_to_a_balance_sheet_account_stay_out(
    client, db_session, seed_accounts
):
    bank_id, income_id = _ids(db_session)
    cid = _customer(db_session, "Harbor Cafe")
    r = client.post(
        "/api/banking/transactions",
        json={
            "account_id": bank_id,
            "date": "2026-10-14",
            "amount": "90.00",
            "category_account_id": income_id,
            "payee": "Walk-in",
        },
    )
    assert r.status_code == 201, r.text
    rows, body = _rows(client, "accrual")
    assert cid not in rows and body["total_sales"] == 0


def test_invoice_figures_are_unchanged_beside_a_deposit(
    client, db_session, seed_accounts, seed_customer
):
    bank_id, income_id = _ids(db_session)
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-10-05",
            "tax_rate": 0,
            "lines": [{"description": "Service", "quantity": 1, "rate": 200}],
        },
    )
    assert r.status_code == 201, r.text
    before, _ = _rows(client, "accrual")
    _deposit(client, bank_id, income_id, seed_customer.id, "45.00")
    after, _ = _rows(client, "accrual")
    row = after[seed_customer.id]
    assert row["invoice_count"] == 1
    assert row["total_sales"] == before[seed_customer.id]["total_sales"] + 45.00
    assert row["total_balance"] == before[seed_customer.id]["total_balance"] == 200.00
