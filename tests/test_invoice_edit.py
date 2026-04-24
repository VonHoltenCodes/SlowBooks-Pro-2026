"""Regression tests for bugs in the invoice edit path.

All tests here should FAIL against the pre-fix code and PASS after the fix.
"""
from decimal import Decimal


def _create_invoice(client, customer_id, amount="100.00", tax_rate="0", qty="1"):
    body = {
        "customer_id": customer_id,
        "date": "2026-04-01",
        "terms": "Net 30",
        "tax_rate": tax_rate,
        "lines": [
            {"description": "Service", "quantity": qty, "rate": amount, "line_order": 0}
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


def test_editing_invoice_total_below_amount_paid_clamps_balance_due_at_zero(
    client, db_session, seed_accounts, seed_customer
):
    inv = _create_invoice(client, seed_customer.id, amount="100.00")

    client.post("/api/payments", json={
        "customer_id": seed_customer.id,
        "date": "2026-04-02",
        "amount": "60.00",
        "allocations": [{"invoice_id": inv["id"], "amount": "60.00"}],
    })

    r = client.put(f"/api/invoices/{inv['id']}", json={
        "lines": [{"description": "Service", "quantity": "1", "rate": "40.00", "line_order": 0}],
        "tax_rate": "0",
    })
    assert r.status_code == 200, r.text

    from app.models.invoices import Invoice
    db_session.expire_all()
    invoice = db_session.query(Invoice).filter_by(id=inv["id"]).first()
    assert invoice.total == Decimal("40.00")
    assert invoice.amount_paid == Decimal("60.00")
    assert invoice.balance_due >= Decimal("0.00"), (
        "balance_due went negative: an invoice with $60 paid shouldn't get negative "
        "balance when its total is edited down to $40"
    )


def test_editing_only_tax_rate_recomputes_totals(
    client, db_session, seed_accounts, seed_customer
):
    inv = _create_invoice(client, seed_customer.id, amount="100.00", tax_rate="0")

    r = client.put(f"/api/invoices/{inv['id']}", json={"tax_rate": "0.10"})
    assert r.status_code == 200, r.text

    from app.models.invoices import Invoice
    db_session.expire_all()
    invoice = db_session.query(Invoice).filter_by(id=inv["id"]).first()
    assert invoice.tax_rate == Decimal("0.1000")
    assert invoice.tax_amount == Decimal("10.00"), (
        f"tax_amount not recomputed after tax_rate change: got {invoice.tax_amount}"
    )
    assert invoice.total == Decimal("110.00"), (
        f"total not recomputed after tax_rate change: got {invoice.total}"
    )
    assert invoice.balance_due == Decimal("110.00")


def test_editing_lines_keeps_journal_balanced(
    client, db_session, seed_accounts, seed_customer
):
    inv = _create_invoice(client, seed_customer.id, amount="100.00")

    r = client.put(f"/api/invoices/{inv['id']}", json={
        "lines": [
            {"description": "A", "quantity": "2", "rate": "50.00", "line_order": 0},
            {"description": "B", "quantity": "1", "rate": "25.00", "line_order": 1},
        ],
        "tax_rate": "0.08",
    })
    assert r.status_code == 200, r.text

    from app.models.invoices import Invoice
    db_session.expire_all()
    invoice = db_session.query(Invoice).filter_by(id=inv["id"]).first()
    assert invoice.subtotal == Decimal("125.00")
    assert invoice.tax_amount == Decimal("10.00")
    assert invoice.total == Decimal("135.00")

    dr, cr = _sum_debits_credits(db_session, invoice.transaction_id)
    assert dr == cr == Decimal("135.00"), (
        f"journal unbalanced after line edit: dr={dr}, cr={cr}"
    )


def test_editing_only_tax_rate_keeps_journal_balanced(
    client, db_session, seed_accounts, seed_customer
):
    inv = _create_invoice(client, seed_customer.id, amount="100.00", tax_rate="0")

    r = client.put(f"/api/invoices/{inv['id']}", json={"tax_rate": "0.10"})
    assert r.status_code == 200, r.text

    from app.models.invoices import Invoice
    db_session.expire_all()
    invoice = db_session.query(Invoice).filter_by(id=inv["id"]).first()
    dr, cr = _sum_debits_credits(db_session, invoice.transaction_id)
    # After the fix, journal should track the new total of 110.
    assert dr == cr == Decimal("110.00"), (
        f"journal not updated to match new tax: dr={dr}, cr={cr}, invoice.total={invoice.total}"
    )
