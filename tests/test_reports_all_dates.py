"""Reports' All dates period (#707): the date it starts on, the books'
first transaction, and the reports run from it."""

from datetime import date
from decimal import Decimal

from app.services.accounting import create_journal_entry


def test_no_books_no_first_date(client):
    assert client.get("/api/reports/first-date").json() == {"first_date": None}


def test_the_first_transaction_date_and_the_reports_on_it(
    client, db_session, seed_accounts
):
    cash, income = seed_accounts["1000"], seed_accounts["4000"]
    for day, amount in ((date(2019, 5, 2), "250.00"), (date(2026, 2, 1), "100.00")):
        create_journal_entry(
            db_session,
            day,
            "je",
            [
                {"account_id": cash.id, "debit": Decimal(amount), "credit": 0},
                {"account_id": income.id, "debit": 0, "credit": Decimal(amount)},
            ],
        )
    db_session.commit()
    first = client.get("/api/reports/first-date").json()["first_date"]
    assert first == "2019-05-02"
    qs = f"start_date={first}&end_date=2026-06-30"
    # every year's income on the P&L and the Trial Balance, none of it
    # carried as prior periods' net income
    assert client.get(f"/api/reports/profit-loss?{qs}").json()["net_income"] == 350.0
    tb = client.get(f"/api/reports/trial-balance?{qs}").json()
    assert not [i for i in tb["items"] if i["account_id"] is None]
    assert tb["total_debit"] == tb["total_credit"] == 350.0
    gl = client.get(f"/api/reports/general-ledger?{qs}")
    assert gl.status_code == 200
