"""Tests for the one-shot rounding-drift detection / repair script.

We seed entities directly with pre-fix drifted values (header subtotal
disagreeing with sum of line amounts), then verify the script reports
the drift and — with apply_repairs — corrects only the header columns,
never the line amounts.
"""

from datetime import date
from decimal import Decimal

from scripts.repair_rounding_drift import apply_repairs, detect


def _seed_drifted_invoice(db_session, customer_id):
    """Two lines stored at 49.99 each, but header subtotal kept at 99.99
    and total at 99.99 — the exact pre-fix drift pattern."""
    from app.models.invoices import Invoice, InvoiceLine

    inv = Invoice(
        invoice_number="DRIFT-1",
        customer_id=customer_id,
        date=date(2026, 4, 1),
        subtotal=Decimal("99.99"),
        tax_rate=Decimal("0"),
        tax_amount=Decimal("0"),
        total=Decimal("99.99"),
        balance_due=Decimal("99.99"),
        amount_paid=Decimal("0"),
    )
    db_session.add(inv)
    db_session.flush()
    for i in range(2):
        db_session.add(
            InvoiceLine(
                invoice_id=inv.id,
                description=f"Half {i}",
                quantity=Decimal("1.5"),
                rate=Decimal("33.33"),
                amount=Decimal("49.99"),
                line_order=i,
            )
        )
    db_session.commit()
    return inv


def _seed_clean_invoice(db_session, customer_id):
    from app.models.invoices import Invoice, InvoiceLine

    inv = Invoice(
        invoice_number="CLEAN-1",
        customer_id=customer_id,
        date=date(2026, 4, 1),
        subtotal=Decimal("100.00"),
        tax_rate=Decimal("0"),
        tax_amount=Decimal("0"),
        total=Decimal("100.00"),
        balance_due=Decimal("100.00"),
    )
    db_session.add(inv)
    db_session.flush()
    db_session.add(
        InvoiceLine(
            invoice_id=inv.id,
            description="Whole",
            quantity=Decimal("1"),
            rate=Decimal("100.00"),
            amount=Decimal("100.00"),
            line_order=0,
        )
    )
    db_session.commit()
    return inv


def test_detect_reports_drift_only(db_session, seed_customer):
    drifted = _seed_drifted_invoice(db_session, seed_customer.id)
    _seed_clean_invoice(db_session, seed_customer.id)

    report = detect(db_session)

    assert report.scanned["invoice"] == 2
    assert len(report.header_drift) == 1
    d = report.header_drift[0]
    assert d.entity == "invoice"
    assert d.id == drifted.id
    assert d.old_subtotal == "99.99"
    assert d.new_subtotal == "99.98"
    assert d.old_total == "99.99"
    assert d.new_total == "99.98"
    assert d.old_balance_due == "99.99"
    assert d.new_balance_due == "99.98"


def test_apply_repairs_writes_corrected_header(db_session, seed_customer):
    drifted = _seed_drifted_invoice(db_session, seed_customer.id)

    report = detect(db_session)
    updated = apply_repairs(db_session, report)

    assert updated == 1
    db_session.expire_all()

    from app.models.invoices import Invoice, InvoiceLine

    inv = db_session.query(Invoice).filter_by(id=drifted.id).first()
    assert inv.subtotal == Decimal("99.98")
    assert inv.total == Decimal("99.98")
    assert inv.balance_due == Decimal("99.98")

    # Line amounts must NOT be rewritten — they represent what the operator
    # originally saw on the document.
    lines = db_session.query(InvoiceLine).filter_by(invoice_id=inv.id).all()
    assert all(Decimal(str(l.amount)) == Decimal("49.99") for l in lines)


def test_apply_respects_amount_paid_for_balance_due(db_session, seed_customer):
    """If the customer already paid against the (drifted) old total, the
    corrected balance_due is total - amount_paid, clamped at 0."""
    from app.models.invoices import Invoice, InvoiceLine

    inv = Invoice(
        invoice_number="PARTIAL-1",
        customer_id=seed_customer.id,
        date=date(2026, 4, 1),
        subtotal=Decimal("99.99"),
        tax_rate=Decimal("0"),
        tax_amount=Decimal("0"),
        total=Decimal("99.99"),
        amount_paid=Decimal("50.00"),
        balance_due=Decimal("49.99"),
    )
    db_session.add(inv)
    db_session.flush()
    for i in range(2):
        db_session.add(
            InvoiceLine(
                invoice_id=inv.id,
                quantity=Decimal("1.5"),
                rate=Decimal("33.33"),
                amount=Decimal("49.99"),
                line_order=i,
            )
        )
    db_session.commit()

    report = detect(db_session)
    apply_repairs(db_session, report)
    db_session.expire_all()

    inv = db_session.query(Invoice).filter_by(invoice_number="PARTIAL-1").first()
    assert inv.total == Decimal("99.98")
    assert inv.balance_due == Decimal("49.98")  # 99.98 - 50.00


def test_journal_drift_reported_not_repaired(db_session, seed_accounts, seed_customer):
    """An invoice whose stored JE is unbalanced gets flagged in the
    journal_drift list but the script never touches transactions."""
    from app.models.invoices import Invoice, InvoiceLine
    from app.models.transactions import Transaction, TransactionLine

    inv = Invoice(
        invoice_number="JE-1",
        customer_id=seed_customer.id,
        date=date(2026, 4, 1),
        subtotal=Decimal("100.00"),
        tax_rate=Decimal("0"),
        tax_amount=Decimal("0"),
        total=Decimal("100.00"),
        balance_due=Decimal("100.00"),
    )
    db_session.add(inv)
    db_session.flush()
    db_session.add(
        InvoiceLine(
            invoice_id=inv.id,
            quantity=Decimal("1"),
            rate=Decimal("100.00"),
            amount=Decimal("100.00"),
            line_order=0,
        )
    )

    ar = seed_accounts["1100"]
    income = seed_accounts["4000"]
    txn = Transaction(date=date(2026, 4, 1), description="Bad JE")
    db_session.add(txn)
    db_session.flush()
    db_session.add(
        TransactionLine(
            transaction_id=txn.id,
            account_id=ar.id,
            debit=Decimal("100.00"),
            credit=Decimal("0"),
        )
    )
    db_session.add(
        TransactionLine(
            transaction_id=txn.id,
            account_id=income.id,
            debit=Decimal("0"),
            credit=Decimal("99.99"),  # intentional 1c off
        )
    )
    inv.transaction_id = txn.id
    db_session.commit()

    report = detect(db_session)
    assert len(report.journal_drift) == 1
    j = report.journal_drift[0]
    assert j.entity == "invoice"
    assert j.debits == "100.00"
    assert j.credits == "99.99"

    # Apply must not touch the JE
    apply_repairs(db_session, report)
    db_session.expire_all()

    lines = db_session.query(TransactionLine).filter_by(transaction_id=txn.id).all()
    debits = sum((Decimal(str(l.debit)) for l in lines), Decimal("0"))
    credits = sum((Decimal(str(l.credit)) for l in lines), Decimal("0"))
    assert debits == Decimal("100.00")
    assert credits == Decimal("99.99")
