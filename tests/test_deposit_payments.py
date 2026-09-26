"""A deposit remembers the payments it took, and a deposited payment can't
be voided out from under it.

Explore 2.17.3 (skytech W-H4): Receive Payment (check 4420, $332.30) → Make
Deposits into Checking → Reconcile → Payments → Void. "Payment voided",
Undeposited Funds −$332.30, the reconciled $812.20 deposit unchanged. Make
Deposits posted one lump entry and dropped the list of payments ticked, so
nothing linked a payment to its deposit and the void route checked only
"already voided". The same lump netting also showed the wrong payments as
waiting once a deposit or a void happened out of date order.

macbase1 S-e: a sales receipt waiting to be deposited read "Payment from …"
with no receipt number, so two receipts from one customer looked the same.
"""

from decimal import Decimal

import pytest
from sqlalchemy import func

from app.models.contacts import Customer
from app.models.payments import Payment
from app.models.transactions import TransactionLine


@pytest.fixture
def acme(db_session):
    c = Customer(name="Acme Diner", is_active=True)
    db_session.add(c)
    db_session.commit()
    return c.id


def _invoice(client, cid, amount, date="2026-09-01"):
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": cid,
            "date": date,
            "tax_rate": 0,
            "lines": [{"description": "Sign", "quantity": 1, "rate": amount}],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _receive(client, cid, amount, date="2026-09-10", check=None, apply_to=None):
    body = {
        "customer_id": cid,
        "date": date,
        "amount": amount,
        "method": "Check",
        "check_number": check,
        "allocations": [],
    }
    if apply_to:
        body["allocations"] = [{"invoice_id": apply_to, "amount": amount}]
    r = client.post("/api/payments", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _pending(client):
    r = client.get("/api/deposits/pending")
    assert r.status_code == 200, r.text
    return r.json()


def _line_of(client, payment_id):
    """The Make Deposits row for a payment, found by its journal entry."""
    from app.database import SessionLocal  # the test's transaction

    with SessionLocal() as s:
        txn_id = s.get(Payment, payment_id).transaction_id
    rows = [p for p in _pending(client) if p["transaction_id"] == txn_id]
    assert rows, f"payment {payment_id} is not waiting to be deposited"
    return rows[0]


def _waiting_payments(client):
    from app.database import SessionLocal

    by_txn = {p["transaction_id"] for p in _pending(client)}
    with SessionLocal() as s:
        return {
            p.id for p in s.query(Payment).filter(Payment.transaction_id.in_(by_txn))
        }


def _deposit(client, seed_accounts, lines, date="2026-09-12", ref="DEP-1"):
    r = client.post(
        "/api/deposits",
        json={
            "deposit_to_account_id": seed_accounts["1000"].id,
            "date": date,
            "total": float(sum(Decimal(str(p["amount"])) for p in lines)),
            "reference": ref,
            "line_ids": [p["transaction_line_id"] for p in lines],
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["transaction_id"]


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


def _reconcile(client, db_session, seed_accounts, deposit_txn_id, balance):
    checking = seed_accounts["1000"].id
    r = client.post(
        "/api/banking/reconciliations",
        json={
            "account_id": checking,
            "statement_date": "2026-09-30",
            "statement_balance": balance,
        },
    )
    assert r.status_code == 201, r.text
    recon = r.json()["id"]
    line = (
        db_session.query(TransactionLine)
        .filter(
            TransactionLine.transaction_id == deposit_txn_id,
            TransactionLine.account_id == checking,
        )
        .one()
    )
    r = client.post(f"/api/banking/reconciliations/{recon}/toggle/{line.id}")
    assert r.status_code == 200, r.text
    r = client.post(f"/api/banking/reconciliations/{recon}/complete")
    assert r.status_code == 200, r.text


def test_a_deposited_payment_cannot_be_voided_until_its_deposit_is(
    client, db_session, seed_accounts, acme
):
    inv = _invoice(client, acme, 332.30)
    first = _receive(client, acme, 332.30, check="4420", apply_to=inv["id"])
    second = _receive(client, acme, 479.90, check="4421")
    dep = _deposit(
        client,
        seed_accounts,
        [_line_of(client, first["id"]), _line_of(client, second["id"])],
    )
    assert _pending(client) == []

    r = client.post(f"/api/payments/{first['id']}/void")
    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert "deposit of 2026-09-12 to Checking" in detail
    assert "Void that deposit on the Make Deposits page first" in detail
    # nothing moved
    assert _gl(db_session, seed_accounts["1200"]) == Decimal("0")
    assert db_session.get(Payment, first["id"]).is_voided is False
    assert client.get(f"/api/invoices/{inv['id']}").json()["status"] == "paid"

    # Voiding the deposit puts both payments back on the list...
    r = client.post(f"/api/deposits/{dep}/void")
    assert r.status_code == 200, r.text
    assert r.json()["voided"] is True
    assert _waiting_payments(client) == {first["id"], second["id"]}
    assert _gl(db_session, seed_accounts["1200"]) == Decimal("812.20")
    assert _gl(db_session, seed_accounts["1000"]) == Decimal("0")

    # ...and then the payment voids cleanly, leaving the other one waiting.
    r = client.post(f"/api/payments/{first['id']}/void")
    assert r.status_code == 200, r.text
    assert _waiting_payments(client) == {second["id"]}
    assert _gl(db_session, seed_accounts["1200"]) == Decimal("479.90")


def test_a_payment_in_a_reconciled_deposit_can_never_be_voided(
    client, db_session, seed_accounts, acme
):
    inv = _invoice(client, acme, 332.30)
    pay = _receive(client, acme, 332.30, check="4420", apply_to=inv["id"])
    dep = _deposit(client, seed_accounts, [_line_of(client, pay["id"])])
    _reconcile(client, db_session, seed_accounts, dep, 332.30)

    r = client.post(f"/api/payments/{pay['id']}/void")
    assert r.status_code == 400, r.text
    assert "reconciled with a bank statement" in r.json()["detail"]
    r = client.post(f"/api/deposits/{dep}/void")
    assert r.status_code == 400, r.text
    assert "reconciled" in r.json()["detail"]
    assert _gl(db_session, seed_accounts["1200"]) == Decimal("0")
    assert _gl(db_session, seed_accounts["1000"]) == Decimal("332.30")
    deposits = client.get("/api/deposits").json()
    assert deposits[0]["reconciled"] is True and deposits[0]["items"] == 1


def test_a_payment_received_straight_into_a_reconciled_account_cant_be_voided(
    client, db_session, seed_accounts, acme
):
    r = client.post(
        "/api/payments",
        json={
            "customer_id": acme,
            "date": "2026-09-10",
            "amount": 150,
            "deposit_to_account_id": seed_accounts["1000"].id,
        },
    )
    assert r.status_code == 201, r.text
    pay = r.json()
    txn_id = db_session.get(Payment, pay["id"]).transaction_id
    _reconcile(client, db_session, seed_accounts, txn_id, 150)
    r = client.post(f"/api/payments/{pay['id']}/void")
    assert r.status_code == 400, r.text
    assert "reconciled" in r.json()["detail"]


def test_a_sales_receipt_in_a_deposit_is_guarded_the_same_way(
    client, db_session, seed_accounts, acme
):
    r = client.post(
        "/api/sales-receipts",
        json={
            "customer_id": acme,
            "date": "2026-09-10",
            "method": "Check",
            "check_number": "5521",
            "lines": [{"description": "Counter sale", "quantity": 1, "rate": 45}],
        },
    )
    assert r.status_code == 201, r.text
    receipt = r.json()
    pay_id = receipt["payment"]["id"]
    _deposit(client, seed_accounts, [_line_of(client, pay_id)])
    # Voiding a receipt voids its payment first — refused while deposited.
    r = client.post(f"/api/payments/{pay_id}/void")
    assert r.status_code == 400, r.text
    assert "deposit of 2026-09-12" in r.json()["detail"]


def test_a_payment_in_a_deposit_that_named_no_payments_is_still_guarded(
    client, db_session, seed_accounts, acme
):
    """Deposits made before 2.17.4 (and ones imported from QuickBooks) are
    one amount with no list; the payment they used up is still deposited."""
    pay = _receive(client, acme, 200, check="77")
    r = client.post(
        "/api/deposits",
        json={
            "deposit_to_account_id": seed_accounts["1000"].id,
            "date": "2026-09-12",
            "total": 200,
        },
    )
    assert r.status_code == 200, r.text
    assert _pending(client) == []
    r = client.post(f"/api/payments/{pay['id']}/void")
    assert r.status_code == 400, r.text
    assert "already been deposited" in r.json()["detail"]
    assert _gl(db_session, seed_accounts["1200"]) == Decimal("0")

    # Voiding that deposit releases it, as for a listed one.
    dep = client.get("/api/deposits").json()[0]
    assert dep["items"] is None
    assert client.post(f"/api/deposits/{dep['id']}/void").status_code == 200
    assert client.post(f"/api/payments/{pay['id']}/void").status_code == 200
    assert _gl(db_session, seed_accounts["1200"]) == Decimal("0")


def test_the_list_drops_a_voided_payment_and_keeps_the_live_one(
    client, seed_accounts, acme
):
    older = _receive(client, acme, 100, date="2026-09-01", check="1")
    newer = _receive(client, acme, 50, date="2026-09-05", check="2")
    assert client.post(f"/api/payments/{older['id']}/void").status_code == 200
    assert _waiting_payments(client) == {newer["id"]}


def test_a_deposit_takes_exactly_the_payments_ticked(
    client, db_session, seed_accounts, acme
):
    older = _receive(client, acme, 100, date="2026-09-01", check="1")
    newer = _receive(client, acme, 50, date="2026-09-05", check="2")
    _deposit(client, seed_accounts, [_line_of(client, older["id"])])
    assert _waiting_payments(client) == {newer["id"]}
    assert _gl(db_session, seed_accounts["1200"]) == Decimal("50.00")


def test_a_payment_can_only_be_deposited_once(client, seed_accounts, acme):
    pay = _receive(client, acme, 100, check="1")
    line = _line_of(client, pay["id"])
    _deposit(client, seed_accounts, [line])
    r = client.post(
        "/api/deposits",
        json={
            "deposit_to_account_id": seed_accounts["1000"].id,
            "date": "2026-09-13",
            "total": 100,
            "line_ids": [line["transaction_line_id"]],
        },
    )
    assert r.status_code == 400, r.text
    assert "already been deposited or voided" in r.json()["detail"]


def test_the_deposit_is_the_payments_ticked_not_the_total_sent(
    client, seed_accounts, acme
):
    pay = _receive(client, acme, 100, check="1")
    r = client.post(
        "/api/deposits",
        json={
            "deposit_to_account_id": seed_accounts["1000"].id,
            "date": "2026-09-13",
            "total": 1000,
            "line_ids": [_line_of(client, pay["id"])["transaction_line_id"]],
        },
    )
    assert r.status_code == 400, r.text
    assert "add up to $100.00" in r.json()["detail"]


def test_the_list_names_the_sales_receipt_and_the_check(client, seed_accounts, acme):
    r = client.post(
        "/api/sales-receipts",
        json={
            "customer_id": acme,
            "date": "2026-09-10",
            "method": "Check",
            "check_number": "5521",
            "lines": [{"description": "Counter sale", "quantity": 1, "rate": 45}],
        },
    )
    assert r.status_code == 201, r.text
    number = r.json()["invoice"]["invoice_number"]
    inv = _invoice(client, acme, 80)
    pay = _receive(client, acme, 80, check="4420", apply_to=inv["id"])

    rows = {p["payment_id"]: p for p in _pending(client)}
    receipt = rows[r.json()["payment"]["id"]]
    assert receipt["description"] == f"Sales Receipt #{number} - Acme Diner"
    assert receipt["check_number"] == "5521"
    assert receipt["received_from"] == "Acme Diner"
    plain = rows[pay["id"]]
    assert plain["check_number"] == "4420"
    assert plain["document"] == f"Invoice #{inv['invoice_number']}"


def test_make_deposits_page_shows_the_numbers_and_can_void_a_deposit():
    from pathlib import Path

    js = (
        Path(__file__).resolve().parents[1] / "app" / "static" / "js" / "deposits.js"
    ).read_text(encoding="utf-8")
    assert "p.check_number" in js and "p.received_from" in js
    assert "/deposits/${id}/void" in js
    assert "Recent deposits" in js
