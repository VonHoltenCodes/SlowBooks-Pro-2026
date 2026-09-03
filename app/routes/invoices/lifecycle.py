from datetime import date, timedelta
from decimal import Decimal

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.accounts import Account
from app.models.invoices import Invoice, InvoiceLine, InvoiceStatus
from app.models.items import Item
from app.schemas.invoices import InvoiceResponse
from app.services.accounting import (
    create_journal_entry,
    get_ar_account_id,
    get_default_income_account_id,
    get_sales_tax_account_id,
    _q,
)
from app.services.numbering import next_invoice_number
from app.services.settings_service import get_all_settings as get_settings
from app.services.closing_date import check_closing_date

from app.routes.invoices._router import router


@router.post("/{invoice_id}/void", response_model=InvoiceResponse)
def void_invoice(invoice_id: int, db: Session = Depends(get_db)):
    """Void — creates a reversing journal entry."""
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if invoice.status == InvoiceStatus.VOID:
        raise HTTPException(status_code=400, detail="Invoice already voided")
    # Voiding an invoice with payments applied would reverse the full A/R
    # while the payment's cash-receipt JE + allocations stay on the books —
    # double-counting cash and reversing A/R twice. Require the payment(s)
    # to be voided first so the ledger stays consistent.
    if (invoice.amount_paid or Decimal("0")) > 0:
        raise HTTPException(
            status_code=400,
            detail=(
                "Cannot void an invoice with payments applied. Void the "
                "payment(s) first, then void the invoice."
            ),
        )
    check_closing_date(db, invoice.date)

    # Create reversing journal entry if original had one
    if invoice.transaction_id:
        from app.models.transactions import TransactionLine

        original_lines = (
            db.query(TransactionLine)
            .filter(TransactionLine.transaction_id == invoice.transaction_id)
            .all()
        )
        reverse_lines = []
        for ol in original_lines:
            reverse_lines.append(
                {
                    "account_id": ol.account_id,
                    "debit": ol.credit,  # swap debit/credit
                    "credit": ol.debit,
                    "description": f"VOID: {ol.description or ''}",
                }
            )
        if reverse_lines:
            create_journal_entry(
                db,
                invoice.date,
                f"VOID Invoice #{invoice.invoice_number}",
                reverse_lines,
                source_type="invoice_void",
                source_id=invoice.id,
                class_id=invoice.class_id,
                job_id=invoice.job_id,
                reference=invoice.invoice_number,
            )

    # ---- Phase 11: reverse inventory movements ----
    # Pass the ORIGINAL (invoice, id) so reverse_sale looks up the sale's
    # historical unit_cost — this keeps the reversal balanced even if
    # avg_cost moved between the sale and the void.
    from app.services.inventory_service import reverse_sale

    for line in invoice.lines:
        if not line.item_id:
            continue
        item = db.query(Item).filter(Item.id == line.item_id).first()
        if item and item.track_inventory:
            reverse_sale(
                db,
                item,
                quantity=Decimal(str(line.quantity)),
                source_type="invoice_void",
                source_id=invoice.id,
                original_source_type="invoice",
                original_source_id=invoice.id,
                txn_date=invoice.date,
            )

    invoice.status = InvoiceStatus.VOID
    invoice.balance_due = Decimal("0")
    db.commit()
    db.refresh(invoice)
    resp = InvoiceResponse.model_validate(invoice)
    if invoice.customer:
        resp.customer_name = invoice.customer.name
    return resp


@router.post("/{invoice_id}/send", response_model=InvoiceResponse)
def mark_invoice_sent(invoice_id: int, db: Session = Depends(get_db)):
    """Mark the invoice as sent."""
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if invoice.status != InvoiceStatus.DRAFT:
        raise HTTPException(
            status_code=400, detail="Only draft invoices can be marked as sent"
        )
    invoice.status = InvoiceStatus.SENT
    db.commit()
    db.refresh(invoice)
    resp = InvoiceResponse.model_validate(invoice)
    if invoice.customer:
        resp.customer_name = invoice.customer.name
    return resp


@router.post("/apply-late-fees")
def apply_late_fees(db: Session = Depends(get_db)):
    """Apply late fees to overdue invoices past the grace period."""
    from app.models.transactions import Transaction

    settings_dict = get_settings(db)

    if settings_dict.get("late_fee_enabled") != "true":
        raise HTTPException(
            status_code=400, detail="Late fees are not enabled in settings"
        )

    rate = Decimal(settings_dict.get("late_fee_rate", "1.5")) / 100
    grace_days = int(settings_dict.get("late_fee_grace_days", "15"))
    today = date.today()

    overdue = (
        db.query(Invoice)
        .filter(
            # DRAFT invoices are unsent — never charge late fees on an
            # invoice the customer hasn't received.
            Invoice.status.in_([InvoiceStatus.SENT, InvoiceStatus.PARTIAL])
        )
        .filter(Invoice.balance_due > 0)
        .filter(Invoice.due_date <= today - timedelta(days=grace_days))
        .all()
    )

    # Ensure Late Fee Income account exists (4800)
    late_fee_account = (
        db.query(Account).filter(Account.account_number == "4800").first()
    )
    if not late_fee_account:
        from app.models.accounts import AccountType as AT

        late_fee_account = Account(
            name="Late Fee Income",
            account_number="4800",
            account_type=AT.INCOME,
            is_system=False,
            balance=Decimal("0"),
        )
        db.add(late_fee_account)
        db.flush()

    ar_id = get_ar_account_id(db)
    if not ar_id:
        raise HTTPException(
            status_code=400, detail="Accounts Receivable (1100) not found"
        )

    applied = 0
    for inv in overdue:
        # Check if late fee already applied (look for journal entry with source_type=late_fee, source_id=inv.id)
        existing = (
            db.query(Transaction)
            .filter(
                Transaction.source_type == "late_fee",
                Transaction.source_id == inv.id,
            )
            .first()
        )
        if existing:
            continue

        fee_amount = _q(inv.balance_due * rate)
        if fee_amount <= 0:
            continue

        # Create journal entry: DR A/R, CR Late Fee Income
        journal_lines = [
            {
                "account_id": ar_id,
                "debit": fee_amount,
                "credit": Decimal("0"),
                "description": f"Late fee - Invoice #{inv.invoice_number}",
            },
            {
                "account_id": late_fee_account.id,
                "debit": Decimal("0"),
                "credit": fee_amount,
                "description": f"Late fee - Invoice #{inv.invoice_number}",
            },
        ]
        create_journal_entry(
            db,
            today,
            f"Late fee - Invoice #{inv.invoice_number}",
            journal_lines,
            source_type="late_fee",
            source_id=inv.id,
            class_id=inv.class_id,
        )

        # Update invoice totals (add to subtotal too so total == subtotal + tax_amount)
        inv.subtotal += fee_amount
        inv.total += fee_amount
        inv.balance_due += fee_amount
        applied += 1

    db.commit()
    return {"applied": applied, "total_overdue": len(overdue)}


@router.post("/{invoice_id}/duplicate", response_model=InvoiceResponse, status_code=201)
def duplicate_invoice(invoice_id: int, db: Session = Depends(get_db)):
    """Duplicate — copy the invoice under a new number."""
    original = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not original:
        raise HTTPException(status_code=404, detail="Invoice not found")

    new_number = next_invoice_number(db)
    today = date.today()

    # Parse terms for due date
    due_date = today + timedelta(days=30)
    if original.terms:
        try:
            days = int(original.terms.lower().replace("net ", ""))
            due_date = today + timedelta(days=days)
        except ValueError:
            pass

    new_invoice = Invoice(
        invoice_number=new_number,
        customer_id=original.customer_id,
        status=InvoiceStatus.DRAFT,
        date=today,
        due_date=due_date,
        terms=original.terms,
        bill_address1=original.bill_address1,
        bill_address2=original.bill_address2,
        bill_city=original.bill_city,
        bill_state=original.bill_state,
        bill_zip=original.bill_zip,
        ship_address1=original.ship_address1,
        ship_address2=original.ship_address2,
        ship_city=original.ship_city,
        ship_state=original.ship_state,
        ship_zip=original.ship_zip,
        subtotal=original.subtotal,
        tax_rate=original.tax_rate,
        tax_amount=original.tax_amount,
        total=original.total,
        balance_due=original.total,
        notes=original.notes,
        class_id=original.class_id,
    )
    db.add(new_invoice)
    db.flush()

    for oline in original.lines:
        new_line = InvoiceLine(
            invoice_id=new_invoice.id,
            item_id=oline.item_id,
            description=oline.description,
            quantity=oline.quantity,
            rate=oline.rate,
            amount=oline.amount,
            class_name=oline.class_name,
            line_order=oline.line_order,
        )
        db.add(new_line)

    # Journal Entry — mirror what create_invoice does (DR A/R, CR Income per line)
    ar_id = get_ar_account_id(db)
    default_income_id = get_default_income_account_id(db)
    tax_account_id = get_sales_tax_account_id(db)

    if ar_id and default_income_id:
        journal_lines = []
        # Debit A/R for total
        journal_lines.append(
            {
                "account_id": ar_id,
                "debit": Decimal(str(new_invoice.total)),
                "credit": Decimal("0"),
                "description": f"Invoice #{new_number}",
            }
        )
        # Credit income for each line
        for oline in original.lines:
            line_amount = Decimal(str(oline.amount))
            if line_amount == 0:
                continue
            income_id = default_income_id
            if oline.item_id:
                item = db.query(Item).filter(Item.id == oline.item_id).first()
                if item and item.income_account_id:
                    income_id = item.income_account_id
            journal_lines.append(
                {
                    "account_id": income_id,
                    "debit": Decimal("0"),
                    "credit": line_amount,
                    "description": oline.description or "",
                }
            )
        # Credit sales tax if any
        if new_invoice.tax_amount and new_invoice.tax_amount > 0 and tax_account_id:
            journal_lines.append(
                {
                    "account_id": tax_account_id,
                    "debit": Decimal("0"),
                    "credit": Decimal(str(new_invoice.tax_amount)),
                    "description": "Sales tax",
                }
            )

        customer = original.customer
        txn = create_journal_entry(
            db,
            today,
            f"Invoice #{new_number} - {customer.name if customer else ''}",
            journal_lines,
            source_type="invoice",
            source_id=new_invoice.id,
            class_id=new_invoice.class_id,
            reference=new_number,
        )
        new_invoice.transaction_id = txn.id

    # Phase 11 (audit fix): a duplicated invoice is a FRESH sale, so it
    # must hit the inventory ledger just like create_invoice does.
    db.flush()
    db.refresh(new_invoice)
    from app.services.inventory_hooks import post_sale_for_invoice

    post_sale_for_invoice(db, new_invoice, txn_date=today)

    db.commit()
    db.refresh(new_invoice)
    resp = InvoiceResponse.model_validate(new_invoice)
    if new_invoice.customer:
        resp.customer_name = new_invoice.customer.name
    return resp
