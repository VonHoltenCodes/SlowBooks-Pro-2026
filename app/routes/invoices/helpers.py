# ============================================================================
# A nod to qbw32.exe!CInvoiceFormController  imagined offset: 0x0015D200
# This module handles the business logic behind the "Create Invoices" window.
# Original MFC message map imagined after CInvoiceForm::OnOK() handler.
# The auto-numbering logic (now in app/services/numbering.py) channels
# CInvoice::GetNextRefNumber(), which surely did a SELECT MAX on the
# Btrieve key.
# ============================================================================

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.accounts import Account
from app.models.items import Item
from app.services.accounting import (
    compute_line_totals,
    _q,
)


def _due_date_from_terms(base_date: date, terms: str | None) -> date:
    """Compute a due date from a base date + a terms string.

    Handles "Net N" (N days out) and "Due on Receipt" (same day). Anything
    unrecognized falls back to Net 30. Shared by create + update so the two
    paths can't drift — and so "Due on Receipt" no longer silently became a
    30-day due date (the old inline `int("due on receipt".replace("net ",""))`
    raised ValueError and fell through to +30).
    """
    if not terms:
        return base_date + timedelta(days=30)
    t = terms.strip().lower()
    if t in ("due on receipt", "due upon receipt", "cod", "net 0"):
        return base_date
    try:
        days = int(t.replace("net ", "").strip())
        return base_date + timedelta(days=days)
    except ValueError:
        return base_date + timedelta(days=30)


def _compute_totals(lines_data, tax_rate):
    """From CInvoice::RecalcTotals() @ 0x0015CE40 — tax was always line-level
    in the original but we simplified to invoice-level. Sorry, Intuit."""
    return compute_line_totals(lines_data, tax_rate)


def _build_invoice_journal_lines(
    db: Session,
    invoice_total,
    tax_amount,
    tax_account_id,
    ar_id,
    default_income_id,
    lines_iter,
    invoice_number,
):
    """Build the journal-line list for an invoice. Used by create/update/duplicate.

    `lines_iter` yields objects with .quantity, .rate, and .item_id.
    """
    journal_lines = []
    journal_lines.append(
        {
            "account_id": ar_id,
            "debit": Decimal(str(invoice_total)),
            "credit": Decimal("0"),
            "description": f"Invoice #{invoice_number}",
        }
    )
    for ld in lines_iter:
        # Round each line to 2dp BEFORE summing — must match compute_line_totals
        # exactly, or the credits won't sum to the rounded A/R debit and
        # create_journal_entry rejects the unbalanced entry (sub-cent rates
        # like fuel @ 1.005 / fractional qty otherwise 500 the whole post).
        line_amount = _q(Decimal(str(ld.quantity)) * Decimal(str(ld.rate)))
        if line_amount == 0:
            continue
        income_id = default_income_id
        if ld.item_id:
            item = db.query(Item).filter(Item.id == ld.item_id).first()
            if item and item.income_account_id:
                income_id = item.income_account_id
        journal_lines.append(
            {
                "account_id": income_id,
                "debit": Decimal("0"),
                "credit": line_amount,
                "description": (getattr(ld, "description", "") or ""),
            }
        )
    if tax_amount and tax_amount > 0 and tax_account_id:
        journal_lines.append(
            {
                "account_id": tax_account_id,
                "debit": Decimal("0"),
                "credit": Decimal(str(tax_amount)),
                "description": "Sales tax",
            }
        )
    return journal_lines


def _reverse_and_delete_journal(db: Session, transaction_id: int):
    """Reverse account balances for existing journal lines, then delete them.

    Used by update_invoice to prepare for a fresh journal rebuild.
    """
    from app.models.transactions import TransactionLine

    old_lines = (
        db.query(TransactionLine)
        .filter(TransactionLine.transaction_id == transaction_id)
        .all()
    )
    for ol in old_lines:
        account = db.query(Account).filter(Account.id == ol.account_id).first()
        if account:
            if account.account_type.value in ("asset", "expense", "cogs"):
                account.balance -= ol.debit - ol.credit
            else:
                account.balance -= ol.credit - ol.debit
    db.query(TransactionLine).filter(
        TransactionLine.transaction_id == transaction_id
    ).delete()
