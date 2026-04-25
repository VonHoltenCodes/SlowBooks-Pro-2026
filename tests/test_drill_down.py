"""Phase 11: drill-down account transactions.

Verifies /api/reports/account-transactions returns every journal line hitting
a given account in the date range, with source-doc linkage so the SPA can
jump from a P&L row to the invoice behind it.
"""
from decimal import Decimal

from app.models.accounts import Account
from app.models.invoices import Invoice


def _mk_invoice(client, customer_id, *, amount, date_="2026-04-01", tax_rate="0"):
    r = client.post("/api/invoices", json={
        "customer_id": customer_id,
        "date": date_,
        "terms": "Net 30",
        "tax_rate": tax_rate,
        "lines": [{
            "description": "Test line",
            "quantity": "1",
            "rate": amount,
        }],
    })
    assert r.status_code == 201, r.text
    return r.json()


def test_drill_down_returns_entries_for_account(
    client, db_session, seed_accounts, seed_customer
):
    """After creating an invoice, drilling into AR should show the invoice line."""
    _mk_invoice(client, seed_customer.id, amount="250.00")

    ar_acct = db_session.query(Account).filter_by(account_number="1100").first()

    r = client.get(
        f"/api/reports/account-transactions"
        f"?account_id={ar_acct.id}&start_date=2026-01-01&end_date=2026-12-31"
    )
    assert r.status_code == 200
    body = r.json()
    assert body["account"]["number"] == "1100"
    assert body["account"]["natural_balance"] == "debit"
    assert len(body["entries"]) >= 1

    entry = body["entries"][0]
    assert entry["debit"] == 250.0
    assert entry["credit"] == 0.0
    assert entry["source_type"] == "invoice"
    assert entry["source_link"] is not None
    assert entry["source_link"].startswith("/#/invoices/")


def test_drill_down_running_balance_and_totals(
    client, db_session, seed_accounts, seed_customer
):
    """Multiple invoices accumulate the running balance and period totals."""
    _mk_invoice(client, seed_customer.id, amount="100.00", date_="2026-04-01")
    _mk_invoice(client, seed_customer.id, amount="200.00", date_="2026-04-02")

    ar_acct = db_session.query(Account).filter_by(account_number="1100").first()
    r = client.get(
        f"/api/reports/account-transactions"
        f"?account_id={ar_acct.id}&start_date=2026-04-01&end_date=2026-04-30"
    )
    body = r.json()
    assert body["period_debit"] == 300.0
    assert body["period_credit"] == 0.0
    assert body["period_net"] == 300.0
    # Running balance on the last entry equals the period net
    assert body["entries"][-1]["running_balance"] == 300.0


def test_drill_down_404_for_unknown_account(client, seed_accounts):
    r = client.get("/api/reports/account-transactions?account_id=999999")
    assert r.status_code == 404


def test_drill_down_empty_for_account_with_no_activity(
    client, db_session, seed_accounts
):
    """An account with no activity in the range returns an empty entries list (not 404)."""
    acct = db_session.query(Account).filter_by(account_number="3000").first()  # Owner's Equity
    r = client.get(
        f"/api/reports/account-transactions"
        f"?account_id={acct.id}&start_date=2020-01-01&end_date=2020-12-31"
    )
    assert r.status_code == 200
    assert r.json()["entries"] == []
    assert r.json()["period_debit"] == 0.0
    assert r.json()["period_credit"] == 0.0
