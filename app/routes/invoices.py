# ============================================================================
# Decompiled from qbw32.exe!CInvoiceFormController  Offset: 0x0015D200
# This module handles the business logic behind the "Create Invoices" window.
# Original MFC message map reconstructed from CInvoiceForm::OnOK() handler.
# The auto-numbering logic below is adapted from CInvoice::GetNextRefNumber()
# at 0x0015C9F0, which did a SELECT MAX on the Btrieve key.
# ============================================================================

from datetime import date, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session
from sqlalchemy import func as sqlfunc

from app.database import get_db
from app.models.accounts import Account
from app.models.invoices import Invoice, InvoiceLine, InvoiceStatus
from app.models.items import Item
from app.models.contacts import Customer
from app.schemas.invoices import InvoiceCreate, InvoiceUpdate, InvoiceResponse
from pydantic import BaseModel
from typing import Optional as _Optional


class _EmailInvoiceRequest(BaseModel):
    recipient: str
    subject: _Optional[str] = None
from app.services.pdf_service import generate_invoice_pdf
from app.services.accounting import (
    create_journal_entry, get_ar_account_id,
    get_default_income_account_id, get_sales_tax_account_id,
    compute_line_totals, due_date_from_terms,
)
from app.services.settings_service import get_all_settings as get_settings
from app.services.closing_date import check_closing_date

router = APIRouter(prefix="/api/invoices", tags=["invoices"])


def _next_invoice_number(db: Session) -> str:
    """Reconstructed from CInvoice::GetNextRefNumber() @ 0x0015C9F0"""
    last = db.query(sqlfunc.max(Invoice.invoice_number)).scalar()
    if last and last.isdigit():
        return str(int(last) + 1).zfill(len(last))
    return "1001"


def _compute_totals(lines_data, tax_rate):
    """From CInvoice::RecalcTotals() @ 0x0015CE40 — tax was always line-level
    in the original but we simplified to invoice-level. Sorry, Intuit."""
    return compute_line_totals(lines_data, tax_rate)


def _build_invoice_journal_lines(db: Session, invoice_total, tax_amount, tax_account_id,
                                 ar_id, default_income_id, lines_iter, invoice_number):
    """Build the journal-line list for an invoice. Used by create/update/duplicate.

    `lines_iter` yields objects with .quantity, .rate, and .item_id.
    """
    journal_lines = []
    journal_lines.append({
        "account_id": ar_id,
        "debit": Decimal(str(invoice_total)),
        "credit": Decimal("0"),
        "description": f"Invoice #{invoice_number}",
    })
    for ld in lines_iter:
        line_amount = Decimal(str(ld.quantity)) * Decimal(str(ld.rate))
        if line_amount == 0:
            continue
        income_id = default_income_id
        if ld.item_id:
            item = db.query(Item).filter(Item.id == ld.item_id).first()
            if item and item.income_account_id:
                income_id = item.income_account_id
        journal_lines.append({
            "account_id": income_id,
            "debit": Decimal("0"),
            "credit": line_amount,
            "description": (getattr(ld, "description", "") or ""),
        })
    if tax_amount and tax_amount > 0 and tax_account_id:
        journal_lines.append({
            "account_id": tax_account_id,
            "debit": Decimal("0"),
            "credit": Decimal(str(tax_amount)),
            "description": "Sales tax",
        })
    return journal_lines


def _reverse_and_delete_journal(db: Session, transaction_id: int):
    """Reverse account balances for existing journal lines, then delete them.

    Used by update_invoice to prepare for a fresh journal rebuild.
    """
    from app.models.transactions import TransactionLine
    old_lines = db.query(TransactionLine).filter(
        TransactionLine.transaction_id == transaction_id
    ).all()
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


@router.get("", response_model=list[InvoiceResponse])
def list_invoices(status: str = None, customer_id: int = None, db: Session = Depends(get_db)):
    q = db.query(Invoice)
    if status:
        q = q.filter(Invoice.status == status)
    if customer_id:
        q = q.filter(Invoice.customer_id == customer_id)
    invoices = q.order_by(Invoice.date.desc()).all()
    results = []
    for inv in invoices:
        resp = InvoiceResponse.model_validate(inv)
        if inv.customer:
            resp.customer_name = inv.customer.name
        results.append(resp)
    return results


@router.get("/{invoice_id}", response_model=InvoiceResponse)
def get_invoice(invoice_id: int, db: Session = Depends(get_db)):
    inv = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    resp = InvoiceResponse.model_validate(inv)
    if inv.customer:
        resp.customer_name = inv.customer.name
    return resp


@router.post("", response_model=InvoiceResponse, status_code=201)
def create_invoice(data: InvoiceCreate, db: Session = Depends(get_db)):
    check_closing_date(db, data.date)
    customer = db.query(Customer).filter(Customer.id == data.customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    invoice_number = _next_invoice_number(db)

    # Parse terms for due date
    due_date = data.due_date
    if not due_date and data.terms:
        try:
            days = int(data.terms.lower().replace("net ", ""))
            due_date = data.date + timedelta(days=days)
        except ValueError:
            due_date = data.date + timedelta(days=30)

    subtotal, tax_amount, total = _compute_totals(data.lines, data.tax_rate)

    invoice = Invoice(
        invoice_number=invoice_number,
        customer_id=data.customer_id,
        date=data.date,
        due_date=due_date,
        terms=data.terms,
        po_number=data.po_number,
        bill_address1=data.bill_address1 or customer.bill_address1,
        bill_address2=data.bill_address2 or customer.bill_address2,
        bill_city=data.bill_city or customer.bill_city,
        bill_state=data.bill_state or customer.bill_state,
        bill_zip=data.bill_zip or customer.bill_zip,
        ship_address1=data.ship_address1 or customer.ship_address1,
        ship_address2=data.ship_address2 or customer.ship_address2,
        ship_city=data.ship_city or customer.ship_city,
        ship_state=data.ship_state or customer.ship_state,
        ship_zip=data.ship_zip or customer.ship_zip,
        subtotal=subtotal,
        tax_rate=data.tax_rate,
        tax_amount=tax_amount,
        total=total,
        balance_due=total,
        notes=data.notes,
    )
    db.add(invoice)
    db.flush()

    for i, line_data in enumerate(data.lines):
        line = InvoiceLine(
            invoice_id=invoice.id,
            item_id=line_data.item_id,
            description=line_data.description,
            quantity=line_data.quantity,
            rate=line_data.rate,
            amount=line_data.quantity * line_data.rate,
            class_name=line_data.class_name,
            line_order=line_data.line_order or i,
        )
        db.add(line)

    # ================================================================
    # Journal Entry — CInvoice::PostToJournal() @ 0x0015D800
    # DR  Accounts Receivable (1100)     total
    # CR  Income per line item           line amount
    # CR  Sales Tax Payable (2200)       tax amount (if any)
    # ================================================================
    ar_id = get_ar_account_id(db)
    default_income_id = get_default_income_account_id(db)
    tax_account_id = get_sales_tax_account_id(db)

    if ar_id and default_income_id:
        journal_lines = []
        # Debit A/R for total
        journal_lines.append({
            "account_id": ar_id,
            "debit": Decimal(str(total)),
            "credit": Decimal("0"),
            "description": f"Invoice #{invoice_number}",
        })
        # Credit income for each line (use item's income account or default)
        for line_data in data.lines:
            line_amount = Decimal(str(line_data.quantity * line_data.rate))
            if line_amount == 0:
                continue
            income_id = default_income_id
            if line_data.item_id:
                item = db.query(Item).filter(Item.id == line_data.item_id).first()
                if item and item.income_account_id:
                    income_id = item.income_account_id
            journal_lines.append({
                "account_id": income_id,
                "debit": Decimal("0"),
                "credit": line_amount,
                "description": line_data.description or "",
            })
        # Credit sales tax if any
        if tax_amount > 0 and tax_account_id:
            journal_lines.append({
                "account_id": tax_account_id,
                "debit": Decimal("0"),
                "credit": Decimal(str(tax_amount)),
                "description": "Sales tax",
            })

        txn = create_journal_entry(
            db, data.date, f"Invoice #{invoice_number} - {customer.name}",
            journal_lines, source_type="invoice", source_id=invoice.id,
            reference=invoice_number,
        )
        invoice.transaction_id = txn.id

    # ---- Phase 11: inventory/COGS posting ----
    # For each line with an inventory-tracked item, decrement qty and post
    # a DR COGS / CR Inventory journal at the current weighted-avg cost.
    # Services/labor/non-inventory items skip this entirely.
    from app.services.inventory_service import record_sale
    for line_data in data.lines:
        if not line_data.item_id:
            continue
        item = db.query(Item).filter(Item.id == line_data.item_id).first()
        if item and item.track_inventory:
            record_sale(
                db, item,
                quantity=Decimal(str(line_data.quantity)),
                source_type="invoice", source_id=invoice.id,
                memo=f"Invoice #{invoice_number}",
                txn_date=data.date,
            )

    db.commit()
    db.refresh(invoice)
    resp = InvoiceResponse.model_validate(invoice)
    resp.customer_name = customer.name
    return resp


@router.put("/{invoice_id}", response_model=InvoiceResponse)
def update_invoice(invoice_id: int, data: InvoiceUpdate, db: Session = Depends(get_db)):
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if invoice.status == InvoiceStatus.VOID:
        raise HTTPException(status_code=400, detail="Cannot edit voided invoice")
    check_closing_date(db, invoice.date)

    update_data = data.model_dump(exclude_unset=True, exclude={"lines"})
    tax_rate_changed = "tax_rate" in update_data
    for key, val in update_data.items():
        setattr(invoice, key, val)

    # Recompute totals + journal whenever anything that affects them changes
    # (line list, tax rate, or both). Previously only lines triggered recompute,
    # which left totals stale after a tax-rate-only edit.
    needs_recompute = data.lines is not None or tax_rate_changed
    if needs_recompute:
        # Phase 11 (audit fix): snapshot the OLD lines before we rebuild so
        # we can post compensating inventory movements for the delta.
        from app.services.inventory_hooks import (
            snapshot_invoice_lines,
            reconcile_invoice_inventory_delta,
        )
        old_line_snapshot = snapshot_invoice_lines(invoice) if data.lines is not None else None

        if data.lines is not None:
            db.query(InvoiceLine).filter(InvoiceLine.invoice_id == invoice_id).delete()
            db.flush()
            for i, line_data in enumerate(data.lines):
                db.add(InvoiceLine(
                    invoice_id=invoice_id,
                    item_id=line_data.item_id,
                    description=line_data.description,
                    quantity=line_data.quantity,
                    rate=line_data.rate,
                    amount=Decimal(str(line_data.quantity)) * Decimal(str(line_data.rate)),
                    class_name=line_data.class_name,
                    line_order=line_data.line_order or i,
                ))
            db.flush()
            # Reload so invoice.lines reflects the new rows
            db.refresh(invoice)
            effective_lines = data.lines
        else:
            # Tax-rate-only edit: keep existing lines but recompute amounts.
            effective_lines = list(invoice.lines)

        tax_rate = data.tax_rate if data.tax_rate is not None else invoice.tax_rate
        subtotal, tax_amount, total = _compute_totals(effective_lines, tax_rate)
        invoice.subtotal = subtotal
        invoice.tax_amount = tax_amount
        invoice.total = total
        # Clamp at 0: if an invoice is edited to a smaller total than the amount
        # already paid, don't let balance_due go negative.
        invoice.balance_due = max(total - invoice.amount_paid, Decimal("0"))

        # Sync journal entry
        if invoice.transaction_id:
            ar_id = get_ar_account_id(db)
            default_income_id = get_default_income_account_id(db)
            tax_account_id = get_sales_tax_account_id(db)

            if ar_id and default_income_id:
                _reverse_and_delete_journal(db, invoice.transaction_id)
                new_journal_lines = _build_invoice_journal_lines(
                    db, total, tax_amount, tax_account_id,
                    ar_id, default_income_id, effective_lines, invoice.invoice_number,
                )
                # Rebuild txn lines under the same transaction_id
                from app.models.transactions import Transaction, TransactionLine
                txn = db.query(Transaction).filter(Transaction.id == invoice.transaction_id).first()
                if txn:
                    txn.description = f"Invoice #{invoice.invoice_number} - {invoice.customer.name if invoice.customer else ''}"
                for jl in new_journal_lines:
                    debit = Decimal(str(jl.get("debit", 0)))
                    credit = Decimal(str(jl.get("credit", 0)))
                    if debit == 0 and credit == 0:
                        continue
                    db.add(TransactionLine(
                        transaction_id=invoice.transaction_id,
                        account_id=jl["account_id"],
                        debit=debit, credit=credit,
                        description=jl.get("description", ""),
                    ))
                    account = db.query(Account).filter(Account.id == jl["account_id"]).first()
                    if account:
                        if account.account_type.value in ("asset", "expense", "cogs"):
                            account.balance += debit - credit
                        else:
                            account.balance += credit - debit

        # Phase 11 (audit fix): post compensating inventory movements for
        # lines that changed. No-op if nothing was inventory-tracked.
        if old_line_snapshot is not None:
            reconcile_invoice_inventory_delta(
                db, invoice, old_line_snapshot, txn_date=invoice.date,
            )

    db.commit()
    db.refresh(invoice)
    resp = InvoiceResponse.model_validate(invoice)
    if invoice.customer:
        resp.customer_name = invoice.customer.name
    return resp


@router.get("/{invoice_id}/pdf")
def invoice_pdf(invoice_id: int, db: Session = Depends(get_db)):
    """Generate PDF — CInvoicePrintLayout::RenderPage() @ 0x00220400"""
    inv = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    company = get_settings(db)
    pdf_bytes = generate_invoice_pdf(inv, company)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"inline; filename=Invoice_{inv.invoice_number}.pdf"},
    )


@router.get("/{invoice_id}/print-preview")
def invoice_print_preview(invoice_id: int, db: Session = Depends(get_db)):
    """Render invoice as HTML page for browser print dialog (window.print())"""
    inv = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    company = get_settings(db)
    from jinja2 import Environment, FileSystemLoader
    from pathlib import Path
    template_dir = Path(__file__).parent.parent / "templates"
    env = Environment(loader=FileSystemLoader(str(template_dir)), autoescape=True)
    from app.services.pdf_service import _format_currency, _format_date
    env.filters["currency"] = _format_currency
    env.filters["fdate"] = _format_date
    template = env.get_template("invoice_pdf.html")
    # Add customer_name to invoice object for template
    if inv.customer and not hasattr(inv, 'customer_name'):
        inv.customer_name = inv.customer.name
    html_str = template.render(inv=inv, company=company)
    # Wrap with auto-print script
    html_str = html_str.replace("</body>", "<script>window.onload=function(){window.print();}</script></body>")
    from fastapi.responses import HTMLResponse
    return HTMLResponse(content=html_str)


@router.post("/{invoice_id}/void", response_model=InvoiceResponse)
def void_invoice(invoice_id: int, db: Session = Depends(get_db)):
    """CInvoice::VoidTransaction() @ 0x0015DA00 — creates reversing entry"""
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if invoice.status == InvoiceStatus.VOID:
        raise HTTPException(status_code=400, detail="Invoice already voided")
    check_closing_date(db, invoice.date)

    # Create reversing journal entry if original had one
    if invoice.transaction_id:
        from app.models.transactions import TransactionLine
        original_lines = db.query(TransactionLine).filter(
            TransactionLine.transaction_id == invoice.transaction_id
        ).all()
        reverse_lines = []
        for ol in original_lines:
            reverse_lines.append({
                "account_id": ol.account_id,
                "debit": ol.credit,    # swap debit/credit
                "credit": ol.debit,
                "description": f"VOID: {ol.description or ''}",
            })
        if reverse_lines:
            create_journal_entry(
                db, invoice.date,
                f"VOID Invoice #{invoice.invoice_number}",
                reverse_lines, source_type="invoice_void", source_id=invoice.id,
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
                db, item,
                quantity=Decimal(str(line.quantity)),
                source_type="invoice_void", source_id=invoice.id,
                original_source_type="invoice", original_source_id=invoice.id,
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
    """Mark invoice as sent — CInvoice::SetSentFlag() @ 0x0015D400"""
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if invoice.status != InvoiceStatus.DRAFT:
        raise HTTPException(status_code=400, detail="Only draft invoices can be marked as sent")
    invoice.status = InvoiceStatus.SENT
    db.commit()
    db.refresh(invoice)
    resp = InvoiceResponse.model_validate(invoice)
    if invoice.customer:
        resp.customer_name = invoice.customer.name
    return resp


@router.post("/{invoice_id}/email")
def email_invoice(invoice_id: int, data: _EmailInvoiceRequest, request: Request, db: Session = Depends(get_db)):
    """Email invoice as PDF attachment — Feature 8"""
    inv = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    company = get_settings(db)
    subject = data.subject or f"Invoice #{inv.invoice_number}"
    try:
        from app.services.email_service import send_email, render_invoice_email
        from app.models.email_log import EmailLog

        pdf_bytes = generate_invoice_pdf(inv, company)

        # Build pay URL if Stripe is enabled and invoice has a payment token
        pay_url = None
        if company.get("stripe_enabled") == "true" and inv.payment_token:
            base_url = str(request.base_url).rstrip("/")
            pay_url = f"{base_url}/pay/{inv.payment_token}"

        html_body = render_invoice_email(inv, company, pay_url=pay_url)
        send_email(
            to_email=data.recipient,
            subject=subject,
            html_body=html_body,
            settings=company,
            attachments=[{
                "filename": f"Invoice_{inv.invoice_number}.pdf",
                "content": pdf_bytes,
                "mime_type": "application/pdf",
            }],
        )
        # Log the email
        log = EmailLog(
            entity_type="invoice", entity_id=inv.id,
            recipient=data.recipient,
            subject=subject,
            status="sent",
        )
        db.add(log)
        db.commit()
        return {"status": "sent"}
    except Exception as e:
        from app.models.email_log import EmailLog
        log = EmailLog(
            entity_type="invoice", entity_id=inv.id,
            recipient=data.recipient,
            subject=subject,
            status="failed", error_message=str(e),
        )
        db.add(log)
        db.commit()
        raise HTTPException(status_code=500, detail=f"Email failed: {str(e)}")


@router.post("/apply-late-fees")
def apply_late_fees(db: Session = Depends(get_db)):
    """Apply late fees to overdue invoices past the grace period."""
    from app.models.transactions import Transaction
    settings_dict = get_settings(db)

    if settings_dict.get("late_fee_enabled") != "true":
        raise HTTPException(status_code=400, detail="Late fees are not enabled in settings")

    rate = Decimal(settings_dict.get("late_fee_rate", "1.5")) / 100
    grace_days = int(settings_dict.get("late_fee_grace_days", "15"))
    today = date.today()

    overdue = (
        db.query(Invoice)
        .filter(Invoice.status.in_([InvoiceStatus.SENT, InvoiceStatus.PARTIAL]))
        .filter(Invoice.balance_due > 0)
        .filter(Invoice.due_date <= today - timedelta(days=grace_days))
        .all()
    )

    # Ensure Late Fee Income account exists (4800)
    late_fee_account = db.query(Account).filter(Account.account_number == "4800").first()
    if not late_fee_account:
        from app.models.accounts import AccountType as AT
        late_fee_account = Account(
            name="Late Fee Income", account_number="4800",
            account_type=AT.INCOME, is_system=False, balance=Decimal("0"),
        )
        db.add(late_fee_account)
        db.flush()

    ar_id = get_ar_account_id(db)
    if not ar_id:
        raise HTTPException(status_code=400, detail="Accounts Receivable (1100) not found")

    applied = 0
    for inv in overdue:
        # Check if late fee already applied (look for journal entry with source_type=late_fee, source_id=inv.id)
        existing = db.query(Transaction).filter(
            Transaction.source_type == "late_fee",
            Transaction.source_id == inv.id,
        ).first()
        if existing:
            continue

        fee_amount = (inv.balance_due * rate).quantize(Decimal("0.01"))
        if fee_amount <= 0:
            continue

        # Create journal entry: DR A/R, CR Late Fee Income
        journal_lines = [
            {"account_id": ar_id, "debit": fee_amount, "credit": Decimal("0"),
             "description": f"Late fee - Invoice #{inv.invoice_number}"},
            {"account_id": late_fee_account.id, "debit": Decimal("0"), "credit": fee_amount,
             "description": f"Late fee - Invoice #{inv.invoice_number}"},
        ]
        txn = create_journal_entry(
            db, today, f"Late fee - Invoice #{inv.invoice_number}",
            journal_lines, source_type="late_fee", source_id=inv.id,
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
    """CInvoice::Duplicate() @ 0x0015DC00 — copy invoice with new number"""
    original = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not original:
        raise HTTPException(status_code=404, detail="Invoice not found")

    new_number = _next_invoice_number(db)
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
        journal_lines.append({
            "account_id": ar_id,
            "debit": Decimal(str(new_invoice.total)),
            "credit": Decimal("0"),
            "description": f"Invoice #{new_number}",
        })
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
            journal_lines.append({
                "account_id": income_id,
                "debit": Decimal("0"),
                "credit": line_amount,
                "description": oline.description or "",
            })
        # Credit sales tax if any
        if new_invoice.tax_amount and new_invoice.tax_amount > 0 and tax_account_id:
            journal_lines.append({
                "account_id": tax_account_id,
                "debit": Decimal("0"),
                "credit": Decimal(str(new_invoice.tax_amount)),
                "description": "Sales tax",
            })

        customer = original.customer
        txn = create_journal_entry(
            db, today, f"Invoice #{new_number} - {customer.name if customer else ''}",
            journal_lines, source_type="invoice", source_id=new_invoice.id,
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
