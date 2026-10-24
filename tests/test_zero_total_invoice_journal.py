"""A $0.00 invoice leaves no empty journal entry behind (#499).

A no-charge invoice saved with ``allow_zero_total`` used to post a journal
whose A/R debit and every line were $0.00: create_journal_entry wrote the
header and skipped each zero line, so the invoice pointed at a transaction
with no splits — an empty entry that balanced only because it held nothing.
A $0.00 invoice now posts no journal at all and keeps ``transaction_id``
NULL; its void and an edit that gives it a real total both still work.
"""

from decimal import Decimal

from sqlalchemy import func

from app.models.invoices import Invoice
from app.models.transactions import Transaction, TransactionLine

FREE = {"description": "Warranty repair", "quantity": 1, "rate": 0}
PRICED = {"description": "Repair", "quantity": 1, "rate": 100}


def _invoice(client, customer_id, line, **extra):
    return client.post(
        "/api/invoices",
        json={
            "customer_id": customer_id,
            "date": "2026-01-05",
            "tax_rate": 0,
            "lines": [line],
            **extra,
        },
    )


def _ar(db_session, seed_accounts):
    dr, cr = (
        db_session.query(
            func.coalesce(func.sum(TransactionLine.debit), 0),
            func.coalesce(func.sum(TransactionLine.credit), 0),
        )
        .filter(TransactionLine.account_id == seed_accounts["1100"].id)
        .one()
    )
    return Decimal(str(dr)) - Decimal(str(cr))


def test_zero_total_invoice_posts_no_journal(
    client, db_session, seed_accounts, seed_customer
):
    r = _invoice(client, seed_customer.id, FREE, allow_zero_total=True)
    assert r.status_code == 201, r.text
    inv = r.json()
    assert Decimal(inv["total"]) == Decimal("0")

    invoice = db_session.query(Invoice).filter_by(id=inv["id"]).first()
    assert invoice.transaction_id is None

    # No header was written for it, so no stranded empty journal entry.
    assert (
        db_session.query(Transaction)
        .filter(
            Transaction.source_type == "invoice",
            Transaction.source_id == inv["id"],
        )
        .count()
        == 0
    )
    assert _ar(db_session, seed_accounts) == Decimal("0")


def test_void_of_a_zero_total_invoice_works(
    client, db_session, seed_accounts, seed_customer
):
    inv = _invoice(client, seed_customer.id, FREE, allow_zero_total=True).json()
    r = client.post(f"/api/invoices/{inv['id']}/void")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "void"


def test_editing_a_zero_invoice_up_to_a_total_posts_a_journal(
    client, db_session, seed_accounts, seed_customer
):
    inv = _invoice(client, seed_customer.id, FREE, allow_zero_total=True).json()
    assert (
        db_session.query(Invoice).filter_by(id=inv["id"]).first().transaction_id
        is None
    )

    r = client.put(f"/api/invoices/{inv['id']}", json={"lines": [PRICED]})
    assert r.status_code == 200, r.text
    assert Decimal(r.json()["total"]) == Decimal("100.00")

    db_session.expire_all()
    invoice = db_session.query(Invoice).filter_by(id=inv["id"]).first()
    assert invoice.transaction_id is not None
    lines = (
        db_session.query(TransactionLine)
        .filter(TransactionLine.transaction_id == invoice.transaction_id)
        .all()
    )
    assert len(lines) > 0
    dr = sum((Decimal(str(line.debit)) for line in lines), Decimal("0"))
    cr = sum((Decimal(str(line.credit)) for line in lines), Decimal("0"))
    assert dr == cr == Decimal("100.00")
    assert _ar(db_session, seed_accounts) == Decimal("100.00")
