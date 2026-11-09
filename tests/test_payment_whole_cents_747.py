"""Payment money is in whole cents (#747).

A one-cent payment applied as two 0.005 allocations passed the "allocations
exceed payment" check, was stored as two 0.01 allocations, and voiding it
left the invoice a cent short. Finer amounts are refused before anything is
written, on every schema a payment's money comes through."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.models.contacts import Customer
from app.models.payments import Payment, PaymentAllocation
from app.routes.batch_payments import BatchAllocation
from app.schemas.bills import BillPaymentAllocationCreate, BillPaymentCreate
from app.schemas.payments import PaymentAllocationCreate, PaymentCreate


def _invoice(client, db_session, total="100"):
    customer = Customer(name="Cents Customer", is_active=True)
    db_session.add(customer)
    db_session.commit()
    response = client.post(
        "/api/invoices",
        json={
            "customer_id": customer.id,
            "date": "2026-04-01",
            "tax_rate": "0",
            "lines": [{"description": "Service", "quantity": "1", "rate": total}],
        },
    )
    assert response.status_code == 201, response.text
    return customer.id, response.json()["id"]


def _counts(TestSession):
    with TestSession() as session:
        return (
            session.query(Payment).count(),
            session.query(PaymentAllocation).count(),
        )


def test_sub_cent_allocations_are_refused_and_nothing_is_written(
    client, db_session, TestSession, seed_accounts
):
    customer_id, invoice_id = _invoice(client, db_session)
    before = _counts(TestSession)
    response = client.post(
        "/api/payments",
        json={
            "customer_id": customer_id,
            "date": "2026-04-01",
            "amount": "0.01",
            "allocations": [
                {"invoice_id": invoice_id, "amount": "0.005"},
                {"invoice_id": invoice_id, "amount": "0.005"},
            ],
        },
    )
    assert response.status_code == 422, response.text
    assert "whole cents" in response.text
    assert "0.005" in response.text
    assert _counts(TestSession) == before
    invoice = client.get(f"/api/invoices/{invoice_id}").json()
    assert Decimal(str(invoice["amount_paid"])) == Decimal("0")


@pytest.mark.parametrize(
    "amount", ["0.004", "1234.50499999999999999999", 1234.505, "10.001"]
)
def test_sub_cent_payment_amount_is_refused(
    client, db_session, TestSession, seed_accounts, amount
):
    customer_id, _ = _invoice(client, db_session)
    before = _counts(TestSession)
    response = client.post(
        "/api/payments",
        json={"customer_id": customer_id, "date": "2026-04-01", "amount": amount},
    )
    assert response.status_code == 422, response.text
    assert "whole cents" in response.text
    assert _counts(TestSession) == before


def test_whole_cent_payment_still_books(client, db_session, seed_accounts):
    customer_id, invoice_id = _invoice(client, db_session)
    response = client.post(
        "/api/payments",
        json={
            "customer_id": customer_id,
            "date": "2026-04-01",
            "amount": 10.5,
            "allocations": [{"invoice_id": invoice_id, "amount": "10.50"}],
        },
    )
    assert response.status_code == 201, response.text
    assert Decimal(str(response.json()["amount"])) == Decimal("10.50")


def test_apply_refuses_sub_cent_allocations(
    client, db_session, TestSession, seed_accounts
):
    customer_id, invoice_id = _invoice(client, db_session)
    paid = client.post(
        "/api/payments",
        json={"customer_id": customer_id, "date": "2026-04-01", "amount": "5.00"},
    )
    assert paid.status_code == 201, paid.text
    before = _counts(TestSession)
    response = client.post(
        f"/api/payments/{paid.json()['id']}/apply",
        json={"allocations": [{"invoice_id": invoice_id, "amount": "0.005"}]},
    )
    assert response.status_code == 422, response.text
    assert "whole cents" in response.text
    assert _counts(TestSession) == before


@pytest.mark.parametrize(
    "build",
    [
        lambda a: PaymentCreate(customer_id=1, date="2026-04-01", amount=a),
        lambda a: PaymentAllocationCreate(invoice_id=1, amount=a),
        lambda a: BillPaymentCreate(vendor_id=1, date="2026-04-01", amount=a),
        lambda a: BillPaymentAllocationCreate(bill_id=1, amount=a),
        lambda a: BatchAllocation(customer_id=1, invoice_id=1, amount=a),
    ],
    ids=["payment", "allocation", "bill-payment", "bill-allocation", "batch"],
)
def test_every_payment_schema_takes_whole_cents_only(build):
    for fine in ("0.005", 0.005, "1.999", "NaN"):
        with pytest.raises(ValidationError):
            build(fine)
    for whole in ("0.01", 0.01, "1234.50", 1234.5, "7"):
        build(whole)
    # A browser's cents / 100 can carry binary noise far below a cent.
    build(0.1 + 0.2)
