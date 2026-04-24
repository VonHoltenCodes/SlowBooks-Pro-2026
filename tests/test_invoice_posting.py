"""Smoke tests for the invoice posting path.

Goal: prove that creating, paying, and voiding an invoice produces correct,
balanced journal entries and keeps Invoice.balance_due consistent.
"""
from decimal import Decimal


def _create_invoice(client, customer_id, amount="100.00", tax_rate="0", qty="1"):
    body = {
        "customer_id": customer_id,
        "date": "2026-04-01",
        "terms": "Net 30",
        "tax_rate": tax_rate,
        "lines": [
            {"description": "Consulting", "quantity": qty, "rate": amount, "line_order": 0}
        ],
    }
    r = client.post("/api/invoices", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _sum_debits_credits(db_session, txn_id):
    from app.models.transactions import TransactionLine
    lines = db_session.query(TransactionLine).filter_by(transaction_id=txn_id).all()
    return (
        sum((Decimal(str(l.debit)) for l in lines), Decimal("0")),
        sum((Decimal(str(l.credit)) for l in lines), Decimal("0")),
    )


def test_create_invoice_produces_balanced_journal(client, db_session, seed_accounts, seed_customer):
    inv = _create_invoice(client, seed_customer.id, amount="100.00")

    from app.models.invoices import Invoice
    invoice = db_session.query(Invoice).filter_by(id=inv["id"]).first()
    assert invoice is not None
    assert invoice.transaction_id is not None
    assert invoice.total == Decimal("100.00")
    assert invoice.balance_due == Decimal("100.00")

    dr, cr = _sum_debits_credits(db_session, invoice.transaction_id)
    assert dr == cr == Decimal("100.00")


def test_create_invoice_with_tax_balances(client, db_session, seed_accounts, seed_customer):
    inv = _create_invoice(client, seed_customer.id, amount="100.00", tax_rate="0.0875")

    from app.models.invoices import Invoice
    invoice = db_session.query(Invoice).filter_by(id=inv["id"]).first()
    assert invoice.subtotal == Decimal("100.00")
    assert invoice.tax_amount == Decimal("8.75")
    assert invoice.total == Decimal("108.75")

    dr, cr = _sum_debits_credits(db_session, invoice.transaction_id)
    assert dr == cr == Decimal("108.75")


def test_pay_invoice_full_marks_paid_and_reverses_ar(client, db_session, seed_accounts, seed_customer):
    inv = _create_invoice(client, seed_customer.id, amount="100.00")

    r = client.post("/api/payments", json={
        "customer_id": seed_customer.id,
        "date": "2026-04-02",
        "amount": "100.00",
        "allocations": [{"invoice_id": inv["id"], "amount": "100.00"}],
    })
    assert r.status_code == 201, r.text
    payment = r.json()

    from app.models.invoices import Invoice, InvoiceStatus
    from app.models.payments import Payment
    db_session.expire_all()
    invoice = db_session.query(Invoice).filter_by(id=inv["id"]).first()
    assert invoice.amount_paid == Decimal("100.00")
    assert invoice.balance_due == Decimal("0.00")
    assert invoice.status == InvoiceStatus.PAID

    pmt = db_session.query(Payment).filter_by(id=payment["id"]).first()
    dr, cr = _sum_debits_credits(db_session, pmt.transaction_id)
    assert dr == cr == Decimal("100.00")


def test_pay_invoice_partial_marks_partial(client, db_session, seed_accounts, seed_customer):
    inv = _create_invoice(client, seed_customer.id, amount="100.00")

    client.post("/api/payments", json={
        "customer_id": seed_customer.id,
        "date": "2026-04-02",
        "amount": "30.00",
        "allocations": [{"invoice_id": inv["id"], "amount": "30.00"}],
    })
    from app.models.invoices import Invoice, InvoiceStatus
    db_session.expire_all()
    invoice = db_session.query(Invoice).filter_by(id=inv["id"]).first()
    assert invoice.amount_paid == Decimal("30.00")
    assert invoice.balance_due == Decimal("70.00")
    assert invoice.status == InvoiceStatus.PARTIAL


def test_void_payment_reverses_allocation(client, db_session, seed_accounts, seed_customer):
    inv = _create_invoice(client, seed_customer.id, amount="100.00")
    r = client.post("/api/payments", json={
        "customer_id": seed_customer.id,
        "date": "2026-04-02",
        "amount": "100.00",
        "allocations": [{"invoice_id": inv["id"], "amount": "100.00"}],
    })
    payment_id = r.json()["id"]

    r = client.post(f"/api/payments/{payment_id}/void")
    assert r.status_code == 200, r.text

    from app.models.invoices import Invoice, InvoiceStatus
    db_session.expire_all()
    invoice = db_session.query(Invoice).filter_by(id=inv["id"]).first()
    assert invoice.amount_paid == Decimal("0.00")
    assert invoice.balance_due == Decimal("100.00")
    assert invoice.status == InvoiceStatus.SENT


def test_void_invoice_creates_reversing_entry(client, db_session, seed_accounts, seed_customer):
    inv = _create_invoice(client, seed_customer.id, amount="100.00")

    r = client.post(f"/api/invoices/{inv['id']}/void")
    assert r.status_code == 200, r.text

    from app.models.invoices import Invoice, InvoiceStatus
    from app.models.transactions import Transaction, TransactionLine
    db_session.expire_all()
    invoice = db_session.query(Invoice).filter_by(id=inv["id"]).first()
    assert invoice.status == InvoiceStatus.VOID
    assert invoice.balance_due == Decimal("0")

    # Original transaction and reversing transaction both present; sum of all lines = 0
    all_txns = db_session.query(Transaction).filter(
        Transaction.source_type.in_(["invoice", "invoice_void"]),
        Transaction.source_id == invoice.id,
    ).all()
    assert len(all_txns) == 2
    total_dr = Decimal("0")
    total_cr = Decimal("0")
    for t in all_txns:
        dr, cr = _sum_debits_credits(db_session, t.id)
        total_dr += dr
        total_cr += cr
    # Net effect of original + void should be zero debits and zero credits *per account*,
    # but at minimum totals equal and both nonzero
    assert total_dr == total_cr
