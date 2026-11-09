"""A bank account deactivated in the chart takes no new deposit (#741).

Make Deposits and a payment's or a sales receipt's Deposit To still offered
a bank account made inactive in the chart, and the API took a deposit into
one. A new deposit, payment, batch payment or sales receipt into one is now
refused with a 422 naming the account, and nothing is written. A record
already on an account deactivated since keeps it: a deposit still reads,
prints and edits, and a payment's edit may keep its Deposit To. Undeposited
Funds (a blank Deposit To) is unaffected.
"""

from decimal import Decimal

import pytest
from sqlalchemy import func

from app.models.contacts import Customer
from app.models.payments import Payment
from app.models.transactions import Transaction, TransactionLine
from app.services import pdf_service

REFUSAL = (
    "Deposit To: Savings is inactive in the chart of accounts. Choose another "
    "account, or make it active again. Nothing was saved."
)


@pytest.fixture
def acme(db_session):
    c = Customer(name="Acme Diner", is_active=True)
    db_session.add(c)
    db_session.commit()
    return c.id


def _deactivate(db_session, acct):
    acct.is_active = False
    db_session.commit()


def _payment(acme, amount="125.00", deposit_to=None, allocations=()):
    return {
        "customer_id": acme,
        "date": "2026-11-02",
        "amount": amount,
        "method": "Check",
        "deposit_to_account_id": deposit_to,
        "allocations": list(allocations),
    }


def _receive(client, acme, **kw):
    r = client.post("/api/payments", json=_payment(acme, **kw))
    assert r.status_code == 201, r.text
    return r.json()


def _deposit(client, account_id, date="2026-11-03"):
    pending = client.get("/api/deposits/pending").json()
    return client.post(
        "/api/deposits",
        json={
            "deposit_to_account_id": account_id,
            "date": date,
            "reference": "SLIP-1",
            "line_ids": [p["transaction_line_id"] for p in pending],
        },
    )


def _count(db_session, model):
    return db_session.query(func.count(model.id)).scalar()


def test_a_new_deposit_into_an_inactive_bank_account_is_refused_naming_it(
    client, db_session, seed_accounts, acme
):
    _receive(client, acme)
    _deactivate(db_session, seed_accounts["1010"])
    before = _count(db_session, Transaction)
    r = _deposit(client, seed_accounts["1010"].id)
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == REFUSAL
    assert _count(db_session, Transaction) == before
    # the payment still waits, and an active account takes it
    assert len(client.get("/api/deposits/pending").json()) == 1
    assert _deposit(client, seed_accounts["1000"].id).status_code == 200


def test_a_new_payment_into_an_inactive_bank_account_is_refused_naming_it(
    client, db_session, seed_accounts, acme
):
    _deactivate(db_session, seed_accounts["1010"])
    before = _count(db_session, Payment)
    r = client.post(
        "/api/payments", json=_payment(acme, deposit_to=seed_accounts["1010"].id)
    )
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == REFUSAL
    assert _count(db_session, Payment) == before


def test_a_payment_with_a_blank_deposit_to_still_goes_to_undeposited_funds(
    client, db_session, seed_accounts, acme
):
    _deactivate(db_session, seed_accounts["1010"])
    pay = _receive(client, acme)
    assert pay["deposit_to_account_id"] is None
    assert len(client.get("/api/deposits/pending").json()) == 1


def test_a_new_sales_receipt_into_an_inactive_bank_account_is_refused_naming_it(
    client, db_session, seed_accounts, acme
):
    from app.models.invoices import Invoice

    _deactivate(db_session, seed_accounts["1010"])
    before = (_count(db_session, Invoice), _count(db_session, Payment))
    r = client.post(
        "/api/sales-receipts",
        json={
            "customer_id": acme,
            "date": "2026-11-02",
            "tax_rate": 0,
            "method": "Cash",
            "deposit_to_account_id": seed_accounts["1010"].id,
            "lines": [{"description": "Sign", "quantity": 1, "rate": 40}],
        },
    )
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == REFUSAL
    # refused before the receipt's invoice was written
    assert (_count(db_session, Invoice), _count(db_session, Payment)) == before


def test_a_new_batch_payment_into_an_inactive_bank_account_is_refused_naming_it(
    client, db_session, seed_accounts, acme
):
    inv = client.post(
        "/api/invoices",
        json={
            "customer_id": acme,
            "date": "2026-11-01",
            "tax_rate": 0,
            "lines": [{"description": "Sign", "quantity": 1, "rate": 250}],
        },
    ).json()
    _deactivate(db_session, seed_accounts["1010"])
    before = _count(db_session, Payment)
    r = client.post(
        "/api/batch-payments",
        json={
            "date": "2026-11-04",
            "deposit_to_account_id": seed_accounts["1010"].id,
            "method": "check",
            "allocations": [
                {"customer_id": acme, "invoice_id": inv["id"], "amount": "250.00"}
            ],
        },
    )
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == REFUSAL
    assert _count(db_session, Payment) == before


def test_editing_a_payment_on_an_account_deactivated_since_keeps_its_account(
    client, db_session, seed_accounts, acme
):
    savings = seed_accounts["1010"]
    pay = _receive(client, acme, deposit_to=savings.id)
    _deactivate(db_session, savings)
    r = client.post(
        f"/api/payments/{pay['id']}/replace",
        json=_payment(acme, amount="150.00", deposit_to=savings.id),
    )
    assert r.status_code == 200, r.text
    new = r.json()
    assert new["deposit_to_account_id"] == savings.id
    assert Decimal(str(new["amount"])) == Decimal("150.00")
    # the edit keeps its own account only: moving it to another inactive
    # one is refused
    checking = seed_accounts["1000"]
    _deactivate(db_session, checking)
    r = client.post(
        f"/api/payments/{new['id']}/replace",
        json=_payment(acme, amount="150.00", deposit_to=checking.id),
    )
    assert r.status_code == 422, r.text
    assert "Checking is inactive" in r.json()["detail"]


def test_a_deposit_on_an_account_deactivated_since_still_reads_prints_and_edits(
    client, db_session, seed_accounts, acme, monkeypatch
):
    savings = seed_accounts["1010"]
    _receive(client, acme)
    r = _deposit(client, savings.id)
    assert r.status_code == 200, r.text
    dep = r.json()["transaction_id"]
    _deactivate(db_session, savings)

    shown = client.get(f"/api/deposits/{dep}")
    assert shown.status_code == 200, shown.text
    assert shown.json()["account_name"] == "Savings"
    listed = client.get("/api/deposits").json()
    assert [d["account_name"] for d in listed if d["id"] == dep] == ["Savings"]

    monkeypatch.setattr(pdf_service, "render_pdf", lambda html, **kw: html.encode())
    slip = client.get(f"/api/deposits/{dep}/pdf")
    assert slip.status_code == 200, slip.text
    assert "Savings" in slip.text
    preview = client.get(f"/api/deposits/{dep}/print-preview")
    assert preview.status_code == 200, preview.text
    assert "Savings" in preview.text

    # its edit keeps the account
    r = client.put(f"/api/deposits/{dep}", json={"reference": "SLIP-2"})
    assert r.status_code == 200, r.text
    assert r.json()["account_name"] == "Savings"
    bank_lines = (
        db_session.query(TransactionLine)
        .filter(
            TransactionLine.transaction_id == dep,
            TransactionLine.account_id == savings.id,
        )
        .count()
    )
    assert bank_lines == 1
