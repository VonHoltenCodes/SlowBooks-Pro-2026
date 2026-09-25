# ============================================================================
# Business logic behind the "Create Invoices" window. Auto-numbering
# lives in app/services/numbering.py.
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
from app.services.terminology import document_reference


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


def resolve_line_taxable(db: Session, lines_data, customer=None) -> None:
    """Decide is_taxable on every line. A customer marked non-taxable (a
    reseller permit, an exempt organization) pays no sales tax on ANY line,
    whatever the line says — the page always sends each line's tax box,
    defaulted from the item, so an exemption that only filled unset lines
    was never consulted for anything made at the window (2.16.2 gate,
    skytech: 8.90 charged where 0.00 was owed). Otherwise an unset line
    takes the item's flag, else taxable. Mutates the pydantic line objects
    in place so the same objects feed both the totals and the stored rows."""
    from app.models.items import Item

    cust_taxable = True if customer is None else (customer.is_taxable is not False)
    for ln in lines_data:
        if not cust_taxable:
            ln.is_taxable = False
            continue
        if getattr(ln, "is_taxable", None) is not None:
            continue
        default = cust_taxable
        if default and getattr(ln, "item_id", None):
            item = db.get(Item, ln.item_id)
            if item is not None and item.is_taxable is False:
                default = False
        ln.is_taxable = default


def _compute_totals(lines_data, tax_rate):
    """Tax applies to the lines flagged taxable (QuickBooks-style per-line
    tax); the rate itself stays on the document."""
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
    face: str = "Invoice",
):
    """Build the journal-line list for an invoice. Used by create/update/duplicate.

    `lines_iter` yields objects with .quantity, .rate, and .item_id.
    `face` is the document's own name (donor_documents.document_label).
    """
    journal_lines = []
    journal_lines.append(
        {
            "account_id": ar_id,
            "debit": Decimal(str(invoice_total)),
            "credit": Decimal("0"),
            "description": document_reference(face, invoice_number),
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
                "class_id": getattr(ld, "class_id", None),
                "job_id": getattr(ld, "job_id", None),
                "cost_code_id": getattr(ld, "cost_code_id", None),
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


def _post_invoice_journal(
    db: Session, invoice, lines, customer_name, *, existing_transaction=None
):
    """One construction/conversion/posting path for invoice create and edit."""
    from app.services.accounting import (
        create_journal_entry,
        get_ar_account_id,
        get_default_income_account_id,
        get_sales_tax_account_id,
    )
    from app.services.currency import convert_lines
    from app.services.donor_documents import document_label
    from app.services.terminology import terms_from_db

    face = document_label(invoice, terms_from_db(db))
    journal_lines = _build_invoice_journal_lines(
        db,
        invoice.total,
        invoice.tax_amount,
        get_sales_tax_account_id(db),
        get_ar_account_id(db),
        get_default_income_account_id(db),
        lines,
        invoice.invoice_number,
        face=face,
    )
    return create_journal_entry(
        db,
        invoice.date,
        document_reference(face, invoice.invoice_number, customer_name),
        convert_lines(journal_lines, Decimal(str(invoice.exchange_rate or 1))),
        source_type="invoice",
        source_id=invoice.id,
        reference=invoice.invoice_number,
        class_id=invoice.class_id,
        job_id=invoice.job_id,
        existing_transaction=existing_transaction,
    )


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
