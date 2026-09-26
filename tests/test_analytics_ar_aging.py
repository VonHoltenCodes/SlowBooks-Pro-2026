"""The analytics A/R aging chart shows what A/R Aging shows.

A/R Aging was fixed to count a foreign-currency invoice at the rate it was
booked at and to net the credits a customer holds (explore 2.17.3, W-H9 /
F17), so its total is account 1100. The analytics page's aging chart kept
summing raw invoice balances: EUR 850 as 850, and no unapplied payments or
credit memos. It now reads the report's own figures.
"""

from decimal import Decimal

import pytest
from sqlalchemy import func

from app.models.contacts import Customer
from app.models.transactions import TransactionLine


@pytest.fixture
def customers(db_session):
    acme = Customer(name="Acme Diner", is_active=True)
    muller = Customer(name="Bäckerei Müller", is_active=True)
    only_credit = Customer(name="Paid Ahead Co", is_active=True)
    db_session.add_all([acme, muller, only_credit])
    db_session.commit()
    return acme.id, muller.id, only_credit.id


def _invoice(client, cid, amount, **extra):
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": cid,
            "date": "2026-09-01",
            "due_date": "2026-12-01",
            "tax_rate": 0,
            "lines": [{"description": "Sign", "quantity": 1, "rate": amount}],
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


def test_the_chart_adds_up_to_account_1100(
    client, db_session, seed_accounts, customers
):
    from app.services.analytics import AnalyticsEngine

    acme, muller, only_credit = customers
    inv = _invoice(client, acme, 312.30)
    _pay(client, acme, 332.30, [(inv["id"], 312.30)])  # 20 over
    _pay(client, acme, 50)  # nothing applied
    _invoice(client, acme, 400)
    _invoice(client, muller, 850, currency="EUR", exchange_rate="1.10")
    _pay(client, only_credit, 75)

    aging = AnalyticsEngine(db_session).ar_aging()
    by_customer = {}
    for bucket in ("current", "30", "60", "90"):
        for name, amount in aging[bucket].items():
            by_customer[name] = by_customer.get(name, 0.0) + amount
    assert by_customer == {
        "Acme Diner": 330.0,  # 400 owed, 70 held
        "Bäckerei Müller": 935.0,  # EUR 850 at 1.10
        "Paid Ahead Co": -75.0,
    }

    dr, cr = (
        db_session.query(
            func.coalesce(func.sum(TransactionLine.debit), 0),
            func.coalesce(func.sum(TransactionLine.credit), 0),
        )
        .filter(TransactionLine.account_id == seed_accounts["1100"].id)
        .one()
    )
    assert Decimal(str(sum(by_customer.values()))) == Decimal(str(dr)) - Decimal(
        str(cr)
    )

    # the same figures the report gives, bucket for bucket, on the API too
    report = client.get("/api/reports/ar-aging").json()
    rows = {i["customer_name"]: i for i in report["items"]}
    served = client.get("/api/analytics/cash-flow").json()["ar_aging"]
    for name, row in rows.items():
        for bucket, column in (
            ("current", "current"),
            ("30", "over_30"),
            ("60", "over_60"),
            ("90", "over_90"),
        ):
            assert served[bucket].get(name, 0.0) == pytest.approx(row[column])
