"""The unapplied part of a payment can be applied to an invoice later.

Explore 2.17.3 (macbase1 F12, skytech W-H9): Receive Payment $200 against
Salt & Pine's only invoice ($512.13) recorded the payment with no
allocation — "the remainder will be tracked as a customer credit" — and
then there was no way to apply it: not from the payment, not from the
invoice, not from a new Receive Payment. The same held for an
overpayment's leftover. POST /api/payments/{id}/apply now moves an
unapplied remainder onto the same customer's open invoices, and
GET /api/customers/{id}/credits lists what a customer has to apply.
"""

from decimal import Decimal

import pytest
from sqlalchemy import func

from app.models.contacts import Customer
from app.models.invoices import Invoice
from app.models.payments import Payment, PaymentAllocation
from app.models.transactions import Transaction, TransactionLine


@pytest.fixture
def salt(db_session):
    c = Customer(name="Salt & Pine Catering Co.", is_active=True)
    db_session.add(c)
    db_session.commit()
    return c.id


def _invoice(client, cid, amount, date="2026-09-01", **extra):
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": cid,
            "date": date,
            "tax_rate": 0,
            "lines": [{"description": "Catering", "quantity": 1, "rate": amount}],
            **extra,
        },
    )
    assert r.status_code == 201, r.text
    inv = r.json()
    assert client.post(f"/api/invoices/{inv['id']}/send").status_code == 200
    return inv


def _payment(client, cid, amount, allocations=(), **extra):
    r = client.post(
        "/api/payments",
        json={
            "customer_id": cid,
            "date": "2026-09-10",
            "amount": amount,
            "method": "Check",
            "check_number": "5521",
            "allocations": [{"invoice_id": i, "amount": a} for i, a in allocations],
            **extra,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _apply(client, payment_id, *allocations):
    return client.post(
        f"/api/payments/{payment_id}/apply",
        json={"allocations": [{"invoice_id": i, "amount": a} for i, a in allocations]},
    )


def _gl(db_session, account):
    dr, cr = (
        db_session.query(
            func.coalesce(func.sum(TransactionLine.debit), 0),
            func.coalesce(func.sum(TransactionLine.credit), 0),
        )
        .filter(TransactionLine.account_id == account.id)
        .one()
    )
    return Decimal(str(dr)) - Decimal(str(cr))


def _snapshot(TestSession):
    with TestSession() as session:
        return {
            model.__tablename__: [
                dict(row)
                for row in session.execute(
                    model.__table__.select().order_by(model.__table__.c.id)
                ).mappings()
            ]
            for model in (Invoice, Payment, PaymentAllocation, Transaction)
        }


def test_an_unapplied_payment_can_be_applied_later(
    client, db_session, seed_accounts, salt
):
    inv = _invoice(client, salt, 512.13)
    pay = _payment(client, salt, 200)
    assert Decimal(pay["unapplied"]) == Decimal("200.00")
    credits = client.get(f"/api/customers/{salt}/credits").json()
    assert credits["total"] == 200.0
    assert [(c["kind"], c["id"], c["available"]) for c in credits["credits"]] == [
        ("payment", pay["id"], 200.0)
    ]
    txns_before = db_session.query(Transaction).count()

    r = _apply(client, pay["id"], (inv["id"], 200))
    assert r.status_code == 200, r.text
    body = r.json()
    assert Decimal(body["unapplied"]) == Decimal("0")
    assert body["allocations"][0]["invoice_number"] == inv["invoice_number"]

    after = client.get(f"/api/invoices/{inv['id']}").json()
    assert Decimal(str(after["balance_due"])) == Decimal("312.13")
    assert after["status"] == "partial"
    assert client.get(f"/api/customers/{salt}/credits").json()["credits"] == []
    # Which invoice the money settles is sub-ledger only: A/R already had it.
    assert db_session.query(Transaction).count() == txns_before
    assert _gl(db_session, seed_accounts["1100"]) == Decimal("312.13")
    assert Decimal(
        str(client.get(f"/api/customers/{salt}").json()["balance"])
    ) == Decimal("312.13")


def test_an_overpayment_remainder_pays_the_next_invoice(client, seed_accounts, salt):
    first = _invoice(client, salt, 100)
    pay = _payment(client, salt, 130, [(first["id"], 100)])
    assert Decimal(pay["unapplied"]) == Decimal("30.00")
    second = _invoice(client, salt, 50, date="2026-09-15")
    r = _apply(client, pay["id"], (second["id"], 30))
    assert r.status_code == 200, r.text
    assert Decimal(
        str(client.get(f"/api/invoices/{second['id']}").json()["balance_due"])
    ) == Decimal("20.00")


def test_it_refuses_more_than_is_unapplied_and_writes_nothing(
    client, TestSession, seed_accounts, salt
):
    a = _invoice(client, salt, 300)
    b = _invoice(client, salt, 300)
    pay = _payment(client, salt, 200)
    before = _snapshot(TestSession)
    r = _apply(client, pay["id"], (a["id"], 150), (b["id"], 100))
    assert r.status_code == 400, r.text
    assert "Only $200.00 of this payment is not applied yet" in r.json()["detail"]
    assert _snapshot(TestSession) == before


def test_it_refuses_more_than_the_invoice_still_owes(
    client, TestSession, seed_accounts, salt
):
    inv = _invoice(client, salt, 100)
    pay = _payment(client, salt, 200)
    before = _snapshot(TestSession)
    r = _apply(client, pay["id"], (inv["id"], 150))
    assert r.status_code == 400, r.text
    assert "more than the $100.00 still due" in r.json()["detail"]
    assert _snapshot(TestSession) == before


def test_it_refuses_another_customers_invoice(
    client, db_session, TestSession, seed_accounts, salt
):
    other = Customer(name="Tidewater Café", is_active=True)
    db_session.add(other)
    db_session.commit()
    theirs = _invoice(client, other.id, 100)
    pay = _payment(client, salt, 200)
    before = _snapshot(TestSession)
    r = _apply(client, pay["id"], (theirs["id"], 50))
    assert r.status_code == 400, r.text
    assert "belongs to a different customer than this payment" in r.json()["detail"]
    assert _snapshot(TestSession) == before


def test_a_void_payment_or_invoice_takes_no_credit(client, seed_accounts, salt):
    inv = _invoice(client, salt, 100)
    pay = _payment(client, salt, 200)
    assert client.post(f"/api/payments/{pay['id']}/void").status_code == 200
    r = _apply(client, pay["id"], (inv["id"], 50))
    assert r.status_code == 400 and "void" in r.json()["detail"]

    pay = _payment(client, salt, 200)
    dead = _invoice(client, salt, 80)
    assert client.post(f"/api/invoices/{dead['id']}/void").status_code == 200
    r = _apply(client, pay["id"], (dead["id"], 50))
    assert r.status_code == 400 and "is void" in r.json()["detail"]


def test_voiding_the_payment_afterwards_unwinds_the_application(
    client, db_session, seed_accounts, salt
):
    inv = _invoice(client, salt, 512.13)
    pay = _payment(client, salt, 200)
    assert _apply(client, pay["id"], (inv["id"], 200)).status_code == 200

    r = client.post(f"/api/payments/{pay['id']}/void")
    assert r.status_code == 200, r.text
    after = client.get(f"/api/invoices/{inv['id']}").json()
    assert Decimal(str(after["balance_due"])) == Decimal("512.13")
    assert after["status"] == "sent"
    assert _gl(db_session, seed_accounts["1100"]) == Decimal("512.13")
    assert _gl(db_session, seed_accounts["1200"]) == Decimal("0")
    assert Decimal(
        str(client.get(f"/api/customers/{salt}").json()["balance"])
    ) == Decimal("512.13")
    assert client.get(f"/api/customers/{salt}/credits").json()["credits"] == []


def test_a_foreign_remainder_settles_at_the_invoice_rate_and_its_void_unwinds(
    client, db_session, seed_accounts, salt
):
    """EUR 850 invoiced at 1.10 (A/R 935.00); EUR 850 received unapplied at
    1.05 relieved A/R 892.50. Applying it later settles the invoice and
    posts the 42.50 difference as a realized FX loss, as applying it on the
    day would have; voiding the payment takes all of it back."""
    inv = _invoice(client, salt, 850, currency="EUR", exchange_rate="1.10")
    pay = _payment(client, salt, 850, currency="EUR", exchange_rate="1.05")
    ar, fx = seed_accounts["1100"], None
    assert _gl(db_session, ar) == Decimal("42.50")

    r = _apply(client, pay["id"], (inv["id"], 850))
    assert r.status_code == 200, r.text
    assert _gl(db_session, ar) == Decimal("0")
    from app.models.accounts import Account

    fx = db_session.query(Account).filter(Account.account_number == "6999").one()
    assert _gl(db_session, fx) == Decimal("42.50")  # a loss (debit)

    assert client.post(f"/api/payments/{pay['id']}/void").status_code == 200
    assert _gl(db_session, ar) == Decimal("935.00")
    assert _gl(db_session, fx) == Decimal("0")
    assert _gl(db_session, seed_accounts["1200"]) == Decimal("0")


def test_credits_list_payments_and_credit_memos(client, seed_accounts, salt):
    pay = _payment(client, salt, 26.39)
    r = client.post(
        "/api/credit-memos",
        json={
            "customer_id": salt,
            "date": "2026-09-12",
            "lines": [{"description": "Return", "quantity": 1, "rate": 18.40}],
        },
    )
    assert r.status_code == 201, r.text
    body = client.get(f"/api/customers/{salt}/credits").json()
    assert body["total"] == pytest.approx(44.79)
    kinds = [(c["kind"], c["available"], c["number"]) for c in body["credits"]]
    assert kinds == [
        ("payment", 26.39, "5521"),
        ("credit_memo", 18.40, r.json()["memo_number"]),
    ]
    assert body["credits"][0]["id"] == pay["id"]


def test_an_amount_that_rounds_to_nothing_is_refused(
    client, TestSession, seed_accounts, salt
):
    inv = _invoice(client, salt, 100)
    pay = _payment(client, salt, 50)
    before = _snapshot(TestSession)
    r = _apply(client, pay["id"], (inv["id"], 0.001))
    assert r.status_code == 400, r.text
    assert _snapshot(TestSession) == before
