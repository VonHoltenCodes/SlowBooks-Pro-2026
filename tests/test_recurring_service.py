"""Tests for the recurring invoice generator.

The drift case (qty=1.5, rate=33.33 → 49.995 per line) exposes a rounding
mismatch: the service computes the journal entry from unrounded Decimals
that balance in-memory, but each TransactionLine.credit column rounds
49.995 -> 50.00 while A/R debit rounds the sum 99.99 -> 99.99. Once stored,
debits != credits and the invoice's stored subtotal disagrees with the sum
of its stored line amounts.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.models.recurring import RecurringInvoice, RecurringInvoiceLine
from app.models.invoices import Invoice, InvoiceLine
from app.models.transactions import TransactionLine
from app.services.recurring_service import generate_due_invoices


def _make_recurring(db_session, customer_id, lines, tax_rate=Decimal("0")):
    rec = RecurringInvoice(
        customer_id=customer_id,
        frequency="monthly",
        start_date=date(2026, 1, 1),
        next_due=date(2026, 1, 1),
        is_active=True,
        terms="Net 30",
        tax_rate=tax_rate,
    )
    db_session.add(rec)
    db_session.flush()
    for i, (qty, rate, desc) in enumerate(lines):
        db_session.add(
            RecurringInvoiceLine(
                recurring_invoice_id=rec.id,
                quantity=qty,
                rate=rate,
                description=desc,
                line_order=i,
            )
        )
    db_session.commit()
    return rec


def _sum_debits_credits(db_session, txn_id):
    rows = db_session.query(TransactionLine).filter_by(transaction_id=txn_id).all()
    dr = sum((Decimal(str(r.debit)) for r in rows), Decimal("0"))
    cr = sum((Decimal(str(r.credit)) for r in rows), Decimal("0"))
    return dr, cr


def test_simple_recurring_invoice_balances(db_session, seed_accounts, seed_customer):
    _make_recurring(
        db_session,
        seed_customer.id,
        [(Decimal("1"), Decimal("100.00"), "Service")],
    )

    ids = generate_due_invoices(db_session, as_of=date(2026, 2, 1))
    assert len(ids) == 1

    invoice = db_session.query(Invoice).filter_by(id=ids[0]).first()
    assert invoice.total == Decimal("100.00")
    assert invoice.balance_due == Decimal("100.00")

    dr, cr = _sum_debits_credits(db_session, invoice.transaction_id)
    assert dr == cr == Decimal("100.00")


def test_recurring_invoice_with_tax_balances(
    db_session, seed_accounts, seed_customer
):
    _make_recurring(
        db_session,
        seed_customer.id,
        [(Decimal("1"), Decimal("100.00"), "Service")],
        tax_rate=Decimal("0.0875"),
    )

    ids = generate_due_invoices(db_session, as_of=date(2026, 2, 1))
    invoice = db_session.query(Invoice).filter_by(id=ids[0]).first()

    assert invoice.subtotal == Decimal("100.00")
    assert invoice.tax_amount == Decimal("8.75")
    assert invoice.total == Decimal("108.75")

    dr, cr = _sum_debits_credits(db_session, invoice.transaction_id)
    assert dr == cr == Decimal("108.75")


def test_subcent_line_amount_keeps_journal_balanced(
    db_session, seed_accounts, seed_customer
):
    """qty=1.5, rate=33.33 → 49.995 per line. Two lines.

    Before the fix: each line credit stored as 50.00, A/R debit stored as
    99.99 → unbalanced after persistence. After the fix: lines round to
    49.99 each pre-sum, so both sides land at 99.98.
    """
    _make_recurring(
        db_session,
        seed_customer.id,
        [
            (Decimal("1.5"), Decimal("33.33"), "Half rate A"),
            (Decimal("1.5"), Decimal("33.33"), "Half rate B"),
        ],
    )

    ids = generate_due_invoices(db_session, as_of=date(2026, 2, 1))
    invoice = db_session.query(Invoice).filter_by(id=ids[0]).first()

    # Reload from DB to see what was actually persisted (catches in-memory
    # vs stored drift).
    db_session.expire_all()
    invoice = db_session.query(Invoice).filter_by(id=ids[0]).first()

    line_amounts = (
        db_session.query(InvoiceLine).filter_by(invoice_id=invoice.id).all()
    )
    sum_of_stored_lines = sum(
        (Decimal(str(l.amount)) for l in line_amounts), Decimal("0")
    )

    # Stored subtotal must equal the sum of stored line amounts. Pre-fix this
    # fails: subtotal stores as 99.99 while line amounts each store as 50.00
    # (sum 100.00).
    assert invoice.subtotal == sum_of_stored_lines, (
        f"subtotal {invoice.subtotal} != sum of line amounts {sum_of_stored_lines}"
    )
    assert invoice.total == invoice.balance_due

    dr, cr = _sum_debits_credits(db_session, invoice.transaction_id)
    assert dr == cr, f"Journal entry unbalanced: debits={dr} credits={cr}"


def test_end_date_deactivates_recurring(
    db_session, seed_accounts, seed_customer
):
    rec = _make_recurring(
        db_session,
        seed_customer.id,
        [(Decimal("1"), Decimal("50.00"), "Service")],
    )
    rec.end_date = date(2026, 1, 15)
    db_session.commit()

    ids = generate_due_invoices(db_session, as_of=date(2026, 2, 1))
    assert len(ids) == 1  # First invoice generates on 2026-01-01

    db_session.expire_all()
    rec = db_session.query(RecurringInvoice).filter_by(id=rec.id).first()
    # next_due advanced to 2026-02-01, past end_date → deactivated
    assert rec.is_active is False
    assert rec.invoices_created == 1


def test_next_due_advances_by_frequency(
    db_session, seed_accounts, seed_customer
):
    rec = _make_recurring(
        db_session,
        seed_customer.id,
        [(Decimal("1"), Decimal("50.00"), "Service")],
    )
    rec.frequency = "quarterly"
    db_session.commit()

    generate_due_invoices(db_session, as_of=date(2026, 1, 1))

    db_session.expire_all()
    rec = db_session.query(RecurringInvoice).filter_by(id=rec.id).first()
    assert rec.next_due == date(2026, 4, 1)
