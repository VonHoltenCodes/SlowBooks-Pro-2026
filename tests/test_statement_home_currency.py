"""A customer statement adds up in home currency, like A/R Aging.

The statement summed a EUR 850.00 invoice as 850 dollars beside the dollar
invoices, and a EUR payment at its face amount, so its Balance Due was
neither what the customer owes in dollars nor in euros (explore 2.17.3,
A11: A/R Aging was fixed to home currency, the statement was not). Each
line is now in home currency — an invoice at the rate it was booked at, a
payment at what it took off A/R — and a foreign line names its own amount.
"""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func

from app.models.contacts import Customer
from app.models.transactions import TransactionLine


@pytest.fixture
def muller(db_session):
    c = Customer(name="Bäckerei Müller", is_active=True)
    db_session.add(c)
    db_session.commit()
    return c


def _invoice(client, cid, amount, day, **extra):
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": cid,
            "date": f"2026-09-{day:02d}",
            "due_date": "2026-10-15",
            "tax_rate": 0,
            "lines": [{"description": "Schild", "quantity": 1, "rate": amount}],
            **extra,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _pay(client, cid, amount, day, allocations=(), **extra):
    r = client.post(
        "/api/payments",
        json={
            "customer_id": cid,
            "date": f"2026-09-{day:02d}",
            "amount": amount,
            "method": "Wire",
            "allocations": [{"invoice_id": i, "amount": a} for i, a in allocations],
            **extra,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _scenario(client, muller):
    usd = _invoice(client, muller.id, 100, 1)
    eur = _invoice(client, muller.id, 850, 2, currency="EUR", exchange_rate="1.10")
    # paid in full in euros when the euro stood at 1.20: $1,020 in the bank,
    # $935 off A/R, $85 realized gain
    _pay(
        client,
        muller.id,
        850,
        10,
        [(eur["id"], 850)],
        currency="EUR",
        exchange_rate="1.20",
    )
    # EUR 50 paid ahead, nothing applied: $60 off A/R at the payment's rate
    _pay(client, muller.id, 50, 11, currency="EUR", exchange_rate="1.20")
    return usd, eur


def test_the_statement_is_in_home_currency_and_ties_to_the_balance(
    client, db_session, seed_accounts, muller
):
    from app.routes.reports.receivables import statement_activity

    usd, eur = _scenario(client, muller)
    act = statement_activity(db_session, muller, date(2026, 9, 30))
    got = [
        (ln["type"], ln["currency"], ln["amount"], ln["balance"]) for ln in act["lines"]
    ]
    assert got == [
        ("Invoice", None, Decimal("100.00"), Decimal("100.00")),
        ("Invoice", "EUR", Decimal("935.00"), Decimal("1035.00")),
        ("Payment", "EUR", Decimal("-935.00"), Decimal("100.00")),
        ("Payment", "EUR", Decimal("-60.00"), Decimal("40.00")),
    ]
    assert act["total_invoiced"] == Decimal("1035.00")
    assert act["total_payments"] == Decimal("995.00")
    assert act["balance_due"] == Decimal("40.00")
    assert act["has_foreign"] is True and act["home_currency"] == "USD"

    # what the customer owes, and what account 1100 carries
    owed = Decimal(str(client.get(f"/api/customers/{muller.id}").json()["balance"]))
    assert owed == act["balance_due"]
    dr, cr = (
        db_session.query(
            func.coalesce(func.sum(TransactionLine.debit), 0),
            func.coalesce(func.sum(TransactionLine.credit), 0),
        )
        .filter(TransactionLine.account_id == seed_accounts["1100"].id)
        .one()
    )
    assert Decimal(str(dr)) - Decimal(str(cr)) == act["balance_due"]

    # a foreign line says what it was in its own currency
    desc = [ln["description"] for ln in act["lines"]]
    assert desc[0] == "Due Oct 15, 2026"
    assert desc[1] == "EUR 850.00 · Due Oct 15, 2026"
    assert desc[2] == f"EUR 850.00 · Wire · applied to #{eur['invoice_number']}"
    assert desc[3] == "EUR 50.00 · Wire · not applied to an invoice yet"


def test_the_pdf_names_the_currency_of_its_amounts(
    client, db_session, seed_accounts, muller
):
    from app.routes.reports.receivables import statement_activity
    from app.services.pdf_service import _render
    from app.services.settings_service import get_all_settings

    _scenario(client, muller)
    html = _render(
        "statement_pdf.html",
        get_all_settings(db_session),
        customer=muller,
        activity=statement_activity(db_session, muller, date(2026, 9, 30)),
        as_of_date=date(2026, 9, 30),
    )
    assert "Amount (USD)" in html and "Balance (USD)" in html
    body = html[html.index("<tbody>") :]
    assert "EUR 850.00" in body and "$935.00" in body and "-$935.00" in body
    assert "$40.00" in html


def test_a_home_currency_statement_reads_as_before(
    client, db_session, seed_accounts, muller
):
    from app.routes.reports.receivables import statement_activity

    inv = _invoice(client, muller.id, 100, 1)
    _pay(client, muller.id, 30, 2, [(inv["id"], 30)])
    act = statement_activity(db_session, muller, date(2026, 9, 30))
    assert [(ln["currency"], ln["amount"]) for ln in act["lines"]] == [
        (None, Decimal("100.00")),
        (None, Decimal("-30.00")),
    ]
    assert act["has_foreign"] is False
    assert (
        act["lines"][1]["description"] == f"Wire · applied to #{inv['invoice_number']}"
    )
