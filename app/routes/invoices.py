# ============================================================================
# Decompiled from qbw32.exe!CInvoiceFormController  Offset: 0x0015D200
# This module handles the business logic behind the "Create Invoices" window.
# Original MFC message map reconstructed from CInvoiceForm::OnOK() handler.
# The auto-numbering logic below is adapted from CInvoice::GetNextRefNumber()
# at 0x0015C9F0, which did a SELECT MAX on the Btrieve key.
# ============================================================================

from datetime import date, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session
from sqlalchemy import func as sqlfunc

from app.database import get_db
from app.models.invoices import Invoice, InvoiceLine, InvoiceStatus
from app.models.items import Item
from app.models.contacts import Customer
from app.schemas.invoices import InvoiceCreate, InvoiceUpdate, InvoiceResponse
from app.services.pdf_service import generate_invoice_pdf
from app.services.accounting import (
    create_journal_entry, get_ar_account_id,
    get_default_income_account_id, get_gst_account_id,
)
from app.routes.settings import _get_all as get_settings
from app.services.closing_date import check_closing_date
from app.services.gst_calculations import calculate_document_gst, prices_include_gst
from app.services.gst_lines import resolve_gst_line_inputs, resolve_line_gst

router = APIRouter(prefix="/api/invoices", tags=["invoices"])


def _next_invoice_number(db: Session) -> str:
    """Reconstructed from CInvoice::GetNextRefNumber() @ 0x0015C9F0"""
    last = db.query(sqlfunc.max(Invoice.invoice_number)).scalar()
    if last and last.isdigit():
        return str(int(last) + 1).zfill(len(last))
    return "1001"


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

    gst_inputs = resolve_gst_line_inputs(db, data.lines)
    gst_totals = calculate_document_gst(
        gst_inputs,
        prices_include_gst=prices_include_gst(db),
        gst_context="sales",
    )

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
        subtotal=gst_totals.subtotal,
        tax_rate=gst_totals.effective_tax_rate,
        tax_amount=gst_totals.tax_amount,
        total=gst_totals.total,
        balance_due=gst_totals.total,
        notes=data.notes,
    )
    db.add(invoice)
    db.flush()

    for i, line_data in enumerate(data.lines):
        gst_code, gst_rate = resolve_line_gst(db, line_data)
        line_total = gst_totals.lines[i]
        line = InvoiceLine(
            invoice_id=invoice.id,
            item_id=line_data.item_id,
            description=line_data.description,
            quantity=line_data.quantity,
            rate=line_data.rate,
            amount=line_total.net_amount,
            gst_code=gst_code,
            gst_rate=gst_rate,
            class_name=line_data.class_name,
            line_order=line_data.line_order or i,
        )
        db.add(line)

    # ================================================================
    # Journal Entry — CInvoice::PostToJournal() @ 0x0015D800
    # DR  Accounts Receivable (1100)     total
    # CR  Income per line item           line amount
    # CR  GST (2200)                     tax amount (if any)
    # ================================================================
    ar_id = get_ar_account_id(db)
    default_income_id = get_default_income_account_id(db)
    tax_account_id = get_gst_account_id(db)

    if ar_id and default_income_id:
        journal_lines = []
        # Debit A/R for total
        journal_lines.append({
            "account_id": ar_id,
            "debit": Decimal(str(gst_totals.total)),
            "credit": Decimal("0"),
            "description": f"Invoice #{invoice_number}",
        })
        # Credit income for each line (use item's income account or default)
        for i, line_data in enumerate(data.lines):
            line_amount = gst_totals.lines[i].net_amount
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
        # Credit GST if any
        if gst_totals.tax_amount > 0 and tax_account_id:
            journal_lines.append({
                "account_id": tax_account_id,
                "debit": Decimal("0"),
                "credit": Decimal(str(gst_totals.tax_amount)),
                "description": "GST",
            })

        txn = create_journal_entry(
            db, data.date, f"Invoice #{invoice_number} - {customer.name}",
            journal_lines, source_type="invoice", source_id=invoice.id,
            reference=invoice_number,
        )
        invoice.transaction_id = txn.id

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

    for key, val in data.model_dump(exclude_unset=True, exclude={"lines"}).items():
        setattr(invoice, key, val)

    if data.lines is not None:
        # Replace lines
        db.query(InvoiceLine).filter(InvoiceLine.invoice_id == invoice_id).delete()
        gst_inputs = resolve_gst_line_inputs(db, data.lines)
        gst_totals = calculate_document_gst(
            gst_inputs,
            prices_include_gst=prices_include_gst(db),
            gst_context="sales",
        )
        for i, line_data in enumerate(data.lines):
            gst_code, gst_rate = resolve_line_gst(db, line_data)
            line_total = gst_totals.lines[i]
            line = InvoiceLine(
                invoice_id=invoice_id,
                item_id=line_data.item_id,
                description=line_data.description,
                quantity=line_data.quantity,
                rate=line_data.rate,
                amount=line_total.net_amount,
                gst_code=gst_code,
                gst_rate=gst_rate,
                class_name=line_data.class_name,
                line_order=line_data.line_order or i,
            )
            db.add(line)

        invoice.subtotal = gst_totals.subtotal
        invoice.tax_rate = gst_totals.effective_tax_rate
        invoice.tax_amount = gst_totals.tax_amount
        invoice.total = gst_totals.total
        invoice.balance_due = gst_totals.total - invoice.amount_paid

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
def email_invoice(invoice_id: int, data: dict, db: Session = Depends(get_db)):
    """Email invoice as PDF attachment — Feature 8"""
    inv = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    company = get_settings(db)
    try:
        from app.services.email_service import send_email, render_invoice_email
        from app.models.email_log import EmailLog

        pdf_bytes = generate_invoice_pdf(inv, company)
        html_body = render_invoice_email(inv, company)
        send_email(
            to_email=data.get("recipient", ""),
            subject=data.get("subject", f"Invoice #{inv.invoice_number}"),
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
            recipient=data.get("recipient", ""),
            subject=data.get("subject", f"Invoice #{inv.invoice_number}"),
            status="sent",
        )
        db.add(log)
        db.commit()
        return {"status": "sent"}
    except Exception as e:
        from app.models.email_log import EmailLog
        log = EmailLog(
            entity_type="invoice", entity_id=inv.id,
            recipient=data.get("recipient", ""),
            subject=data.get("subject", ""),
            status="failed", error_message=str(e),
        )
        db.add(log)
        db.commit()
        raise HTTPException(status_code=500, detail=f"Email failed: {str(e)}")


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
            gst_code=oline.gst_code,
            gst_rate=oline.gst_rate,
            class_name=oline.class_name,
            line_order=oline.line_order,
        )
        db.add(new_line)

    db.commit()
    db.refresh(new_invoice)
    resp = InvoiceResponse.model_validate(new_invoice)
    if new_invoice.customer:
        resp.customer_name = new_invoice.customer.name
    return resp
