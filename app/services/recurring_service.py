# ============================================================================
# Recurring Invoice Service — generates invoices from recurring templates
# Feature 2: Infrastructure C (background scheduler / cron)
# ============================================================================

from datetime import date, timedelta
from decimal import Decimal
from dateutil.relativedelta import relativedelta

from sqlalchemy.orm import Session
from sqlalchemy import func as sqlfunc

from app.models.recurring import RecurringInvoice
from app.models.invoices import Invoice, InvoiceLine
from app.models.items import Item
from app.services.accounting import (
    create_journal_entry, get_ar_account_id,
    get_default_income_account_id, get_sales_tax_account_id,
)


def _next_invoice_number(db: Session) -> str:
    last = db.query(sqlfunc.max(Invoice.invoice_number)).scalar()
    if last and last.isdigit():
        return str(int(last) + 1).zfill(len(last))
    return "1001"


def _advance_next_due(current: date, frequency: str) -> date:
    if frequency == "weekly":
        return current + timedelta(weeks=1)
    elif frequency == "monthly":
        return current + relativedelta(months=1)
    elif frequency == "quarterly":
        return current + relativedelta(months=3)
    elif frequency == "yearly":
        return current + relativedelta(years=1)
    return current + relativedelta(months=1)


def generate_due_invoices(db: Session, as_of: date = None) -> list[int]:
    """Generate all invoices that are due on or before as_of date.
    Returns list of created invoice IDs."""
    if as_of is None:
        as_of = date.today()

    recurrings = db.query(RecurringInvoice).filter(
        RecurringInvoice.is_active == True,
        RecurringInvoice.next_due <= as_of,
    ).all()

    created_ids = []
    ar_id = get_ar_account_id(db)
    default_income_id = get_default_income_account_id(db)
    tax_account_id = get_sales_tax_account_id(db)

    for rec in recurrings:
        # Check end date
        if rec.end_date and rec.next_due > rec.end_date:
            rec.is_active = False
            continue

        invoice_number = _next_invoice_number(db)

        # Compute totals
        subtotal = sum(Decimal(str(l.quantity)) * Decimal(str(l.rate)) for l in rec.lines)
        tax_rate = rec.tax_rate or Decimal("0")
        tax_amount = subtotal * tax_rate
        total = subtotal + tax_amount

        # Parse terms for due date
        due_date = rec.next_due + timedelta(days=30)
        if rec.terms:
            try:
                days = int(rec.terms.lower().replace("net ", ""))
                due_date = rec.next_due + timedelta(days=days)
            except ValueError:
                pass

        invoice = Invoice(
            invoice_number=invoice_number, customer_id=rec.customer_id,
            date=rec.next_due, due_date=due_date, terms=rec.terms,
            subtotal=subtotal, tax_rate=tax_rate, tax_amount=tax_amount,
            total=total, balance_due=total, notes=rec.notes,
        )
        db.add(invoice)
        db.flush()

        for rline in rec.lines:
            db.add(InvoiceLine(
                invoice_id=invoice.id, item_id=rline.item_id,
                description=rline.description, quantity=rline.quantity,
                rate=rline.rate, amount=Decimal(str(rline.quantity)) * Decimal(str(rline.rate)),
                line_order=rline.line_order,
            ))

        # Journal entry
        if ar_id and default_income_id:
            journal_lines = [{
                "account_id": ar_id, "debit": total, "credit": Decimal("0"),
                "description": f"Recurring Invoice #{invoice_number}",
            }]
            for rline in rec.lines:
                line_amt = Decimal(str(rline.quantity)) * Decimal(str(rline.rate))
                if line_amt == 0:
                    continue
                income_id = default_income_id
                if rline.item_id:
                    item = db.query(Item).filter(Item.id == rline.item_id).first()
                    if item and item.income_account_id:
                        income_id = item.income_account_id
                journal_lines.append({
                    "account_id": income_id, "debit": Decimal("0"), "credit": line_amt,
                    "description": rline.description or "",
                })
            if tax_amount > 0 and tax_account_id:
                journal_lines.append({
                    "account_id": tax_account_id, "debit": Decimal("0"), "credit": tax_amount,
                    "description": "Sales tax",
                })
            txn = create_journal_entry(
                db, rec.next_due, f"Recurring Invoice #{invoice_number}",
                journal_lines, source_type="invoice", source_id=invoice.id,
                reference=invoice_number,
            )
            invoice.transaction_id = txn.id

        # Phase 11 (audit fix): recurring-generated invoices are real sales
        # and must hit the inventory ledger.
        db.flush()
        db.refresh(invoice)
        from app.services.inventory_hooks import post_sale_for_invoice
        post_sale_for_invoice(db, invoice, txn_date=rec.next_due)

        # Advance next due date
        rec.next_due = _advance_next_due(rec.next_due, rec.frequency)
        rec.invoices_created += 1
        if rec.end_date and rec.next_due > rec.end_date:
            rec.is_active = False

        created_ids.append(invoice.id)

    db.commit()
    return created_ids
