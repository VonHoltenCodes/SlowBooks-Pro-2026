"""A/R Aging and Income by Customer agree with the ledger.

Explore 2.17.3: A/R Aging totalled $10,826,709.24 against GL 1100
$10,826,724.24 (skytech W-H9) and $782.13 against a balance sheet of
$555.74 (macbase1 F17), because

- money a customer paid that was not applied to an invoice (an
  overpayment's leftover, a payment recorded without choosing invoices)
  was left out — ``unapplied_credits`` read 0 for everyone — and
- a foreign-currency invoice counted at its document amount (EUR 850)
  instead of its booked home amount (USD 935) (W-M2).

Income by Customer counted "Paid" without those payments and put sales tax
into "Sales" (W-L11).
"""

from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func

from app.models.contacts import Customer
from app.models.transactions import TransactionLine

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def acme(db_session):
    c = Customer(name="Acme Diner", is_active=True)
    db_session.add(c)
    db_session.commit()
    return c.id


@pytest.fixture
def muller(db_session):
    c = Customer(name="Bäckerei Müller", is_active=True)
    db_session.add(c)
    db_session.commit()
    return c.id


def _invoice(client, cid, lines, tax_rate=0, **extra):
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": cid,
            "date": "2026-09-01",
            "due_date": "2026-10-01",
            "tax_rate": tax_rate,
            "lines": [
                {"description": d, "quantity": 1, "rate": rate} for d, rate in lines
            ],
            **extra,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _pay(client, cid, amount, allocations=()):
    r = client.post(
        "/api/payments",
        json={
            "customer_id": cid,
            "date": "2026-09-10",
            "amount": amount,
            "allocations": [{"invoice_id": i, "amount": a} for i, a in allocations],
        },
    )
    assert r.status_code == 201, r.text


def _gl_ar(db_session, seed_accounts):
    dr, cr = (
        db_session.query(
            func.coalesce(func.sum(TransactionLine.debit), 0),
            func.coalesce(func.sum(TransactionLine.credit), 0),
        )
        .filter(TransactionLine.account_id == seed_accounts["1100"].id)
        .one()
    )
    return Decimal(str(dr)) - Decimal(str(cr))


def _scenario(client, acme, muller):
    # Acme: 332.30 paid against a 312.30 balance (20 over), plus 50 paid
    # with no invoice chosen. Müller: EUR 850 booked at 1.10.
    inv = _invoice(client, acme, [("Sign", 312.30)])
    _pay(client, acme, 332.30, [(inv["id"], 312.30)])
    _pay(client, acme, 50)
    _invoice(client, acme, [("Banner", 400)])
    _invoice(client, muller, [("Schild", 850)], currency="EUR", exchange_rate="1.10")


def test_aging_total_is_what_account_1100_carries(
    client, db_session, seed_accounts, acme, muller
):
    _scenario(client, acme, muller)
    body = client.get("/api/reports/ar-aging?as_of_date=2026-09-30").json()
    assert Decimal(str(body["totals"]["total"])) == _gl_ar(db_session, seed_accounts)
    assert _gl_ar(db_session, seed_accounts) == Decimal("1265.00")  # 400 − 70 + 935

    rows = {i["customer_id"]: i for i in body["items"]}
    assert rows[acme]["unapplied_credits"] == 70.0
    assert rows[acme]["total"] == 330.0
    assert rows[muller]["total"] == 935.0


def test_a_customer_with_only_a_credit_appears_in_the_aging(
    client, db_session, seed_accounts, acme
):
    _pay(client, acme, 75)
    body = client.get("/api/reports/ar-aging?as_of_date=2026-09-30").json()
    rows = {i["customer_id"]: i for i in body["items"]}
    assert rows[acme]["unapplied_credits"] == 75.0
    assert rows[acme]["total"] == -75.0
    assert Decimal(str(body["totals"]["total"])) == _gl_ar(db_session, seed_accounts)


def test_income_by_customer_sales_are_pre_tax_and_paid_counts_every_payment(
    client, seed_accounts, acme, muller
):
    inv = _invoice(client, acme, [("Sign", 100)], tax_rate=0.0825)  # 100 + 8.25
    _pay(client, acme, 150, [(inv["id"], 108.25)])  # 41.75 not applied
    _invoice(client, muller, [("Schild", 850)], currency="EUR", exchange_rate="1.10")

    body = client.get(
        "/api/reports/income-by-customer?start_date=2026-01-01&end_date=2026-12-31"
    ).json()
    rows = {i["customer_id"]: i for i in body["items"]}
    assert rows[acme]["total_sales"] == 100.0
    assert rows[acme]["total_tax"] == 8.25
    assert rows[acme]["total_paid"] == 150.0
    assert rows[acme]["total_balance"] == -41.75
    assert rows[muller]["total_sales"] == 935.0
    assert body["total_sales"] == 1035.0 and body["total_tax"] == 8.25
    # the columns add up
    for r in rows.values():
        assert round(r["total_sales"] + r["total_tax"] - r["total_paid"], 2) == round(
            r["total_balance"], 2
        )


def test_the_report_pages_show_the_credits_and_the_tax():
    js = (ROOT / "app/static/js/reports.js").read_text(encoding="utf-8")
    aging = js[js.index("async arAging(") : js.index("async apAging(")]
    assert "unapplied_credits" in aging and ">Credits<" in aging
    income = js[
        js.index("async incomeByCustomer(") : js.index("async customerStatementPicker(")
    ]
    assert "total_tax" in income and ">Sales Tax<" in income
