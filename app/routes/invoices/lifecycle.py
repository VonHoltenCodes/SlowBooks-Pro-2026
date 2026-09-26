from datetime import date, timedelta
from decimal import Decimal

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from app.schemas.common import StrictModel
from typing import Optional
from sqlalchemy.exc import IntegrityError
from app.schemas.credit_memos import CreditMemoResponse
from datetime import date as dt_date
from app.database import get_db
from app.models.accounts import Account
from app.models.invoices import Invoice, InvoiceLine, InvoiceStatus
from app.models.items import Item
from app.schemas.invoices import InvoiceResponse
from app.services.accounting import (
    create_journal_entry,
    get_ar_account_id,
    _q,
)
from app.services.numbering import next_invoice_number
from app.services.settings_service import get_all_settings as get_settings
from app.services.closing_date import check_closing_date

from app.routes.invoices._router import router
from app.routes.invoices.helpers import (
    _due_date_from_terms,
    _post_invoice_journal,
    refuse_zero_total,
)
from app.services.donor_documents import document_label
from app.services.terminology import document_reference, terms_from_db


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
                f"VOID {document_label(invoice, terms_from_db(db))} #{invoice.invoice_number}",
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
    words = terms_from_db(db)
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
                "description": f"Late fee - {document_label(inv, words)} #{inv.invoice_number}",
            },
            {
                "account_id": late_fee_account.id,
                "debit": Decimal("0"),
                "credit": fee_amount,
                "description": f"Late fee - {document_label(inv, words)} #{inv.invoice_number}",
            },
        ]
        create_journal_entry(
            db,
            today,
            f"Late fee - {document_label(inv, words)} #{inv.invoice_number}",
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


class WriteOffRequest(StrictModel):
    date: dt_date
    amount: Optional[Decimal] = None  # default: the whole open balance
    memo: Optional[str] = None


@router.post(
    "/{invoice_id}/write-off", response_model=CreditMemoResponse, status_code=201
)
def write_off_invoice(
    invoice_id: int, data: WriteOffRequest, db: Session = Depends(get_db)
):
    """Forgive an open balance (a pledge that will never be paid): a credit
    memo flagged as a write-off, posting DR Bad Debt Expense / CR A/R and
    applied to the invoice at once, so the A/R subledger, the donor
    statement and the pledge report all agree. Undo it by voiding the
    credit memo."""
    from app.models.credit_memos import (
        CreditApplication,
        CreditMemo,
        CreditMemoLine,
        CreditMemoStatus,
    )
    from app.services.accounting import get_bad_debt_account_id
    from app.services.numbering import next_credit_memo_number

    check_closing_date(db, data.date)
    inv = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if inv.status == InvoiceStatus.VOID:
        raise HTTPException(status_code=400, detail="Invoice is void")
    balance = Decimal(str(inv.balance_due or 0))
    if balance <= 0:
        raise HTTPException(
            status_code=400, detail="Nothing to write off — no open balance"
        )
    amount = _q(Decimal(str(data.amount))) if data.amount is not None else balance
    if amount <= 0 or amount > balance:
        raise HTTPException(
            status_code=400, detail="Write-off must be between 0 and the open balance"
        )

    ar_id = get_ar_account_id(db)
    bad_debt_id = get_bad_debt_account_id(db)
    memo = (
        data.memo
        or f"Write-off: {document_label(inv, terms_from_db(db))} #{inv.invoice_number}"
    )
    cm = None
    for _ in range(10):
        cm = CreditMemo(
            memo_number=next_credit_memo_number(db),
            customer_id=inv.customer_id,
            date=data.date,
            original_invoice_id=inv.id,
            subtotal=amount,
            tax_rate=Decimal("0"),
            tax_amount=Decimal("0"),
            total=amount,
            amount_applied=amount,
            balance_remaining=Decimal("0"),
            notes=memo,
            class_id=inv.class_id,
            job_id=inv.job_id,
            status=CreditMemoStatus.APPLIED,
            is_write_off=True,
        )
        db.add(cm)
        try:
            db.flush()
            break
        except IntegrityError as e:
            if "memo_number" not in str(e.orig).lower():
                raise
            db.rollback()
            cm = None
    if cm is None:
        raise HTTPException(
            status_code=503, detail="Could not assign a credit memo number"
        )
    db.add(
        CreditMemoLine(
            credit_memo_id=cm.id,
            description=memo,
            quantity=1,
            rate=amount,
            amount=amount,
            line_order=0,
        )
    )
    txn = create_journal_entry(
        db,
        data.date,
        f"Credit Memo {cm.memo_number} - write-off of "
        + document_reference(
            document_label(inv, terms_from_db(db)), inv.invoice_number
        ),
        [
            {
                "account_id": bad_debt_id,
                "debit": amount,
                "credit": Decimal("0"),
                "description": memo,
            },
            {
                "account_id": ar_id,
                "debit": Decimal("0"),
                "credit": amount,
                "description": memo,
            },
        ],
        source_type="credit_memo",
        source_id=cm.id,
        class_id=inv.class_id,
        job_id=inv.job_id,
    )
    cm.transaction_id = txn.id
    db.add(CreditApplication(credit_memo_id=cm.id, invoice_id=inv.id, amount=amount))
    inv.amount_paid = Decimal(str(inv.amount_paid or 0)) + amount
    inv.balance_due = balance - amount
    inv.status = InvoiceStatus.PAID if inv.balance_due == 0 else InvoiceStatus.PARTIAL
    db.commit()
    db.refresh(cm)
    resp = CreditMemoResponse.model_validate(cm)
    resp.customer_name = inv.customer.name if inv.customer else None
    return resp


@router.post("/{invoice_id}/duplicate", response_model=InvoiceResponse, status_code=201)
def duplicate_invoice(invoice_id: int, db: Session = Depends(get_db)):
    """Duplicate — the same sale under a new number, dated today and not
    sent yet. The copy carries what the original says about the sale: its
    currency and the rate it was booked at, the job, class and PO number,
    and each line's item, job, class and cost code. Each line's tax is the
    customer's as they stand today. It posts the way a new invoice does.

    The copy used to drop the currency and rate, so a EUR 850.00 invoice's
    duplicate was a $850.00 invoice (and posted $850 where the original
    booked $935), and it dropped the job and every line's job, class and
    cost code, so the copy was missing from job costing (found integrating
    the 2.17.3 exploratory fixes)."""
    from app.services.accounting import compute_line_totals, taxed_copy_lines
    from app.services.inventory_hooks import post_sale_for_invoice

    words = terms_from_db(db)
    original = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not original:
        raise HTTPException(status_code=404, detail="Invoice not found")

    today = date.today()
    copied = taxed_copy_lines(original.lines, original.customer)
    subtotal, tax_amount, total = compute_line_totals(copied, original.tax_rate)
    refuse_zero_total(total, document_label(original, words).lower(), "duplicating it")

    new_invoice = Invoice(
        invoice_number=next_invoice_number(db),
        customer_id=original.customer_id,
        status=InvoiceStatus.DRAFT,
        date=today,
        due_date=_due_date_from_terms(today, original.terms),
        terms=original.terms,
        po_number=original.po_number,
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
        subtotal=subtotal,
        tax_rate=original.tax_rate,
        tax_amount=tax_amount,
        total=total,
        balance_due=total,
        currency=original.currency,
        exchange_rate=original.exchange_rate,
        is_pledge=original.is_pledge,
        fair_value_amount=original.fair_value_amount,
        fair_value_description=original.fair_value_description,
        notes=original.notes,
        class_id=original.class_id,
        job_id=original.job_id,
    )
    db.add(new_invoice)
    db.flush()

    new_lines = []
    for oline, cline in zip(original.lines, copied):
        line = InvoiceLine(
            invoice_id=new_invoice.id,
            item_id=oline.item_id,
            description=oline.description,
            quantity=oline.quantity,
            rate=oline.rate,
            amount=_q(Decimal(str(oline.quantity)) * Decimal(str(oline.rate))),
            class_name=oline.class_name,
            job_id=oline.job_id,
            class_id=oline.class_id,
            cost_code_id=oline.cost_code_id,
            is_taxable=cline.is_taxable,
            line_order=oline.line_order,
        )
        db.add(line)
        new_lines.append(line)

    # The posting a new invoice gets: DR A/R, CR income per line with its
    # job / class / cost code, CR sales tax — converted to home currency at
    # the copy's rate.
    customer = original.customer
    txn = _post_invoice_journal(
        db, new_invoice, new_lines, customer.name if customer else ""
    )
    new_invoice.transaction_id = txn.id

    # Phase 11 (audit fix): a duplicated invoice is a FRESH sale, so it
    # must hit the inventory ledger just like create_invoice does.
    db.flush()
    db.refresh(new_invoice)
    post_sale_for_invoice(db, new_invoice, txn_date=today)

    db.commit()
    db.refresh(new_invoice)
    resp = InvoiceResponse.model_validate(new_invoice)
    if new_invoice.customer:
        resp.customer_name = new_invoice.customer.name
    return resp
