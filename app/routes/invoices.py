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
from app.schemas.email import DocumentEmailRequest
from app.schemas.invoices import InvoiceCreate, InvoiceLineCreate, InvoiceUpdate, InvoiceResponse
from app.services.pdf_service import generate_invoice_pdf
from app.services.accounting import (
    create_journal_entry, reverse_journal_entry, get_ar_account_id,
    get_default_income_account_id, get_gst_account_id,
)
from app.routes.settings import _get_all as get_settings
from app.services.closing_date import check_closing_date
from app.services.email_service import render_invoice_email, send_document_email
from app.services.gst_calculations import calculate_document_gst, prices_include_gst
from app.services.gst_lines import resolve_gst_line_inputs, resolve_line_gst

router = APIRouter(prefix="/api/invoices", tags=["invoices"])


def _next_invoice_number(db: Session) -> str:
    """Reconstructed from CInvoice::GetNextRefNumber() @ 0x0015C9F0"""
    last = db.query(sqlfunc.max(Invoice.invoice_number)).scalar()
    if last and last.isdigit():
        return str(int(last) + 1).zfill(len(last))
    return "1001"


def _post_invoice_journal(db: Session, invoice: Invoice, customer: Customer, lines, gst_totals):
    # ================================================================
    # Journal Entry — CInvoice::PostToJournal() @ 0x0015D800
    # DR  Accounts Receivable (1100)     total
    # CR  Income per line item           line amount
    # CR  GST (2200)                     tax amount (if any)
    # ================================================================
    ar_id = get_ar_account_id(db)
    default_income_id = get_default_income_account_id(db)
    tax_account_id = get_gst_account_id(db)

    if not (ar_id and default_income_id):
        return None

    journal_lines = [{
        "account_id": ar_id,
        "debit": Decimal(str(gst_totals.total)),
        "credit": Decimal("0"),
        "description": f"Invoice #{invoice.invoice_number}",
    }]

    for i, line_data in enumerate(lines):
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

    if gst_totals.tax_amount > 0 and tax_account_id:
        journal_lines.append({
            "account_id": tax_account_id,
            "debit": Decimal("0"),
            "credit": Decimal(str(gst_totals.tax_amount)),
            "description": "GST",
        })

    txn = create_journal_entry(
        db, invoice.date, f"Invoice #{invoice.invoice_number} - {customer.name}",
        journal_lines, source_type="invoice", source_id=invoice.id,
        reference=invoice.invoice_number,
    )
    invoice.transaction_id = txn.id
    return txn


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

    _post_invoice_journal(db, invoice, customer, data.lines, gst_totals)

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
    old_transaction_id = invoice.transaction_id
    old_date = invoice.date
    update_values = data.model_dump(exclude_unset=True, exclude={"lines"})
    new_date = update_values.get("date", old_date)
    check_closing_date(db, old_date)
    if new_date != old_date:
        check_closing_date(db, new_date)

    if "customer_id" in update_values:
        customer = db.query(Customer).filter(Customer.id == update_values["customer_id"]).first()
        if not customer:
            raise HTTPException(status_code=404, detail="Customer not found")

    for key, val in update_values.items():
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

        if old_transaction_id:
            reverse_journal_entry(
                db,
                old_transaction_id,
                old_date,
                f"Reversal Invoice #{invoice.invoice_number}",
                source_type="invoice_reversal",
                source_id=invoice.id,
                reference=invoice.invoice_number,
            )
        posting_customer = db.query(Customer).filter(Customer.id == invoice.customer_id).first()
        _post_invoice_journal(db, invoice, posting_customer, data.lines, gst_totals)

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
        reverse_journal_entry(
            db,
            invoice.transaction_id,
            invoice.date,
            f"VOID Invoice #{invoice.invoice_number}",
            source_type="invoice_void",
            source_id=invoice.id,
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
def email_invoice(invoice_id: int, data: DocumentEmailRequest, db: Session = Depends(get_db)):
    """Email invoice as PDF attachment — Feature 8"""
    inv = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    company = get_settings(db)
    try:
        pdf_bytes = generate_invoice_pdf(inv, company)
        html_body = render_invoice_email(inv, company)
        send_document_email(
            db,
            to_email=data.recipient,
            subject=data.subject or f"Invoice #{inv.invoice_number}",
            html_body=html_body,
            attachment_bytes=pdf_bytes,
            attachment_name=f"Invoice_{inv.invoice_number}.pdf",
            entity_type="invoice",
            entity_id=inv.id,
        )
        return {"status": "sent"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Email failed: {str(e)}")


@router.post("/{invoice_id}/duplicate", response_model=InvoiceResponse, status_code=201)
def duplicate_invoice(invoice_id: int, db: Session = Depends(get_db)):
    """CInvoice::Duplicate() @ 0x0015DC00 — copy invoice with new number"""
    original = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not original:
        raise HTTPException(status_code=404, detail="Invoice not found")

    today = date.today()

    # Parse terms for due date
    due_date = today + timedelta(days=30)
    if original.terms:
        try:
            days = int(original.terms.lower().replace("net ", ""))
            due_date = today + timedelta(days=days)
        except ValueError:
            pass

    return create_invoice(InvoiceCreate(
        customer_id=original.customer_id,
        date=today,
        due_date=due_date,
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
        tax_rate=original.tax_rate,
        notes=original.notes,
        lines=[
            InvoiceLineCreate(
                item_id=line.item_id,
                description=line.description,
                quantity=line.quantity,
                rate=line.rate,
                gst_code=line.gst_code,
                gst_rate=line.gst_rate,
                class_name=line.class_name,
                line_order=line.line_order,
            )
            for line in original.lines
        ],
    ), db=db)
