"""Ordinary customer-payment allocation ownership (EXP-001)."""

from decimal import Decimal

import pytest

from app.models.accounts import Account
from app.models.contacts import Customer
from app.models.invoices import Invoice
from app.models.payments import Payment, PaymentAllocation
from app.models.transactions import Transaction, TransactionLine


@pytest.mark.parametrize(
    "same_customer", [False, True], ids=["invalid-owner", "valid-owner"]
)
def test_payment_allocation_customer_ownership(
    client, db_session, TestSession, seed_accounts, same_customer
):
    a = Customer(name="Customer A", is_active=True)
    b = Customer(name="Customer B", is_active=True)
    db_session.add_all([a, b])
    db_session.commit()
    a_id, b_id = a.id, b.id
    response = client.post(
        "/api/invoices",
        json={
            "customer_id": b_id,
            "date": "2026-04-01",
            "currency": "USD",
            "exchange_rate": "1",
            "tax_rate": "0",
            "lines": [{"description": "Service", "quantity": "1", "rate": "100"}],
        },
    )
    assert response.status_code == 201, response.text
    invoice_id = response.json()["id"]

    def snapshot():
        # Fresh session: verify persisted rows, not the request's identity map.
        with TestSession() as session:
            return {
                model.__tablename__: [
                    dict(row)
                    for row in session.execute(
                        model.__table__.select().order_by(model.__table__.c.id)
                    ).mappings()
                ]
                for model in (
                    Invoice,
                    Payment,
                    PaymentAllocation,
                    Transaction,
                    TransactionLine,
                    Account,
                )
            }

    before = snapshot()
    original = before[Invoice.__tablename__][0]
    assert (original["total"], original["amount_paid"], original["balance_due"]) == (
        Decimal("100"),
        Decimal("0"),
        Decimal("100"),
    )
    response = client.post(
        "/api/payments",
        json={
            "customer_id": b_id if same_customer else a_id,
            "date": "2026-04-01",
            "currency": "USD",
            "exchange_rate": "1",
            "amount": "60",
            "allocations": [{"invoice_id": invoice_id, "amount": "60"}],
        },
    )
    after = snapshot()
    if not same_customer:
        assert response.status_code == 400, response.text
        assert "does not belong to payment customer" in response.json()["detail"]
        assert after == before
        assert after[Payment.__tablename__] == []
        assert after[PaymentAllocation.__tablename__] == []
        return

    assert response.status_code == 201, response.text
    payment = after[Payment.__tablename__][0]
    allocation = after[PaymentAllocation.__tablename__][0]
    invoice = after[Invoice.__tablename__][0]
    assert payment["customer_id"] == invoice["customer_id"] == b_id
    assert payment["amount"] == allocation["amount"] == Decimal("60")
    assert allocation["payment_id"] == payment["id"]
    assert allocation["invoice_id"] == invoice_id
    assert (invoice["total"], invoice["amount_paid"], invoice["balance_due"]) == (
        Decimal("100"),
        Decimal("60"),
        Decimal("40"),
    )
    assert invoice["status"].value == "partial"
    balances = {
        row["account_number"]: row["balance"] for row in after[Account.__tablename__]
    }
    assert balances["1100"] == Decimal("40")
    assert balances["1200"] == Decimal("60")
    lines = [
        row
        for row in after[TransactionLine.__tablename__]
        if row["transaction_id"] == payment["transaction_id"]
    ]
    assert len(lines) == 2
    assert (
        sum(row["debit"] for row in lines)
        == sum(row["credit"] for row in lines)
        == Decimal("60")
    )
