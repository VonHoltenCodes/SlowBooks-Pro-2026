"""A batch payment pays home-currency invoices only.

A batch payment has no currency or rate of its own, so it is in the home
currency. A EUR invoice in a batch was paid as if its euros were dollars:
EUR 850 recorded as an $850 payment against A/R the invoice had booked at
$935 — no conversion, no currency check, no realized FX (found integrating
the 2.17.3 exploratory fixes). Receive Payment's rule now holds for the
batch too: a payment pays invoices in its own currency, and a
cross-currency allocation is refused with a sentence naming the invoice.
The batch page leaves foreign invoices out, so Select All never picks one.
"""

import json
import shutil
import subprocess
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func

from app.models.contacts import Customer
from app.models.transactions import TransactionLine

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def muller(db_session):
    c = Customer(name="Bäckerei Müller", is_active=True)
    db_session.add(c)
    db_session.commit()
    return c.id


def _invoice(client, cid, amount, **extra):
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": cid,
            "date": "2026-09-01",
            "tax_rate": 0,
            "lines": [{"description": "Schild", "quantity": 1, "rate": amount}],
            **extra,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _batch(client, allocations):
    return client.post(
        "/api/batch-payments",
        json={"date": "2026-09-20", "method": "check", "allocations": allocations},
    )


def _ledger(db_session):
    return db_session.query(
        func.count(TransactionLine.id),
        func.coalesce(func.sum(TransactionLine.debit), 0),
    ).one()


def test_a_eur_invoice_in_a_batch_is_refused_and_nothing_is_written(
    client, db_session, seed_accounts, seed_customer, muller
):
    from app.models.payments import Payment

    usd = _invoice(client, seed_customer.id, 100)
    eur = _invoice(client, muller, 850, currency="EUR", exchange_rate="1.10")
    before = _ledger(db_session)

    r = _batch(
        client,
        [
            {"customer_id": seed_customer.id, "invoice_id": usd["id"], "amount": 100},
            {"customer_id": muller, "invoice_id": eur["id"], "amount": 850},
        ],
    )
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == (
        f"Invoice {eur['invoice_number']} is in EUR, and a batch payment is in "
        "USD, so the batch can't pay it. Take it out of the batch and record its "
        "payment on its own, in EUR."
    )
    db_session.expire_all()
    assert db_session.query(Payment).count() == 0
    assert _ledger(db_session) == before
    for inv in (usd, eur):
        got = client.get(f"/api/invoices/{inv['id']}").json()
        assert Decimal(got["balance_due"]) == Decimal(inv["total"])


def test_home_currency_invoices_still_go_through(
    client, db_session, seed_accounts, seed_customer
):
    from app.models.payments import Payment

    usd = _invoice(client, seed_customer.id, 100)
    r = _batch(
        client,
        [{"customer_id": seed_customer.id, "invoice_id": usd["id"], "amount": 60}],
    )
    assert r.status_code == 200, r.text
    assert r.json()["payments_created"] == 1
    pay = db_session.query(Payment).one()
    assert (pay.currency, pay.exchange_rate) == ("USD", Decimal("1"))
    got = client.get(f"/api/invoices/{usd['id']}").json()
    assert Decimal(got["balance_due"]) == Decimal("40.00")
    assert got["status"] == "partial"


def test_amounts_must_be_positive_as_for_a_payment(
    client, db_session, seed_accounts, seed_customer
):
    usd = _invoice(client, seed_customer.id, 100)
    for amount in (0, -25):
        r = _batch(
            client,
            [
                {
                    "customer_id": seed_customer.id,
                    "invoice_id": usd["id"],
                    "amount": amount,
                }
            ],
        )
        assert r.status_code == 400, r.text
        assert r.json()["detail"] == "Allocation amounts must be positive"
    got = client.get(f"/api/invoices/{usd['id']}").json()
    assert Decimal(got["balance_due"]) == Decimal("100.00")


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_batch_page_leaves_foreign_invoices_out():
    out = subprocess.run(
        ["node", str(ROOT / "tests" / "js" / "batch_payments_probe.js")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert got["listed"] == [1, 3]
    assert got["note"] == (
        "1 open invoice in another currency is not listed here: a batch payment "
        "is in USD, so pay it on its own."
    )
