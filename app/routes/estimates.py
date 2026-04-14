# ============================================================================
# Decompiled from qbw32.exe!CCreateEstimatesView  Offset: 0x00195200
# CEstimate::ConvertToInvoice() at 0x001944A0 deep-copied every field and
# line item, then set EstimateStatus to CONVERTED. Our version does the same
# through SQL. The PDF generation was originally Crystal Reports — we use
# WeasyPrint now because Crystal Reports licenses cost more than this app.
# ============================================================================

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.estimates import Estimate, EstimateLine, EstimateStatus
from app.models.invoices import Invoice
from app.models.contacts import Customer
from app.schemas.email import DocumentEmailRequest
from app.schemas.estimates import EstimateCreate, EstimateUpdate, EstimateResponse
from app.schemas.invoices import InvoiceCreate, InvoiceLineCreate, InvoiceResponse
from app.services.pdf_service import generate_estimate_pdf
from app.routes.settings import _get_all as get_settings, _set as set_setting
from app.services.email_service import render_document_email, send_document_email
from app.services.gst_calculations import calculate_document_gst, prices_include_gst
from app.services.gst_lines import resolve_gst_line_inputs, resolve_line_gst

router = APIRouter(prefix="/api/estimates", tags=["estimates"])


def _next_estimate_number(db: Session) -> str:
    settings = get_settings(db)
    prefix = settings.get("estimate_prefix", "E-")
    next_number = settings.get("estimate_next_number", "1001").strip() or "1001"
    try:
        current_number = int(next_number)
    except ValueError:
        current_number = 1001

    while True:
        estimate_number = f"{prefix}{current_number}"
        exists = db.query(Estimate.id).filter(Estimate.estimate_number == estimate_number).first()
        if not exists:
            return estimate_number
        current_number += 1


@router.get("", response_model=list[EstimateResponse])
def list_estimates(status: str = None, customer_id: int = None, db: Session = Depends(get_db)):
    q = db.query(Estimate)
    if status:
        q = q.filter(Estimate.status == status)
    if customer_id:
        q = q.filter(Estimate.customer_id == customer_id)
    estimates = q.order_by(Estimate.date.desc()).all()
    results = []
    for est in estimates:
        resp = EstimateResponse.model_validate(est)
        if est.customer:
            resp.customer_name = est.customer.name
        results.append(resp)
    return results


@router.get("/{estimate_id}", response_model=EstimateResponse)
def get_estimate(estimate_id: int, db: Session = Depends(get_db)):
    est = db.query(Estimate).filter(Estimate.id == estimate_id).first()
    if not est:
        raise HTTPException(status_code=404, detail="Estimate not found")
    resp = EstimateResponse.model_validate(est)
    if est.customer:
        resp.customer_name = est.customer.name
    return resp


@router.post("", response_model=EstimateResponse, status_code=201)
def create_estimate(data: EstimateCreate, db: Session = Depends(get_db)):
    customer = db.query(Customer).filter(Customer.id == data.customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    estimate_number = _next_estimate_number(db)
    gst_inputs = resolve_gst_line_inputs(db, data.lines)
    gst_totals = calculate_document_gst(
        gst_inputs,
        prices_include_gst=prices_include_gst(db),
        gst_context="sales",
    )

    estimate = Estimate(
        estimate_number=estimate_number,
        customer_id=data.customer_id,
        date=data.date,
        expiration_date=data.expiration_date,
        subtotal=gst_totals.subtotal,
        tax_rate=gst_totals.effective_tax_rate,
        tax_amount=gst_totals.tax_amount,
        total=gst_totals.total,
        notes=data.notes,
    )
    db.add(estimate)
    db.flush()

    for i, line_data in enumerate(data.lines):
        gst_code, gst_rate = resolve_line_gst(db, line_data)
        line_total = gst_totals.lines[i]
        line = EstimateLine(
            estimate_id=estimate.id,
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

    numeric_part = estimate_number.removeprefix(get_settings(db).get("estimate_prefix", "E-"))
    if numeric_part.isdigit():
        set_setting(db, "estimate_next_number", str(int(numeric_part) + 1))

    db.commit()
    db.refresh(estimate)
    resp = EstimateResponse.model_validate(estimate)
    resp.customer_name = customer.name
    return resp


@router.put("/{estimate_id}", response_model=EstimateResponse)
def update_estimate(estimate_id: int, data: EstimateUpdate, db: Session = Depends(get_db)):
    estimate = db.query(Estimate).filter(Estimate.id == estimate_id).first()
    if not estimate:
        raise HTTPException(status_code=404, detail="Estimate not found")

    for key, val in data.model_dump(exclude_unset=True, exclude={"lines"}).items():
        setattr(estimate, key, val)

    if data.lines is not None:
        db.query(EstimateLine).filter(EstimateLine.estimate_id == estimate_id).delete()
        gst_inputs = resolve_gst_line_inputs(db, data.lines)
        gst_totals = calculate_document_gst(
            gst_inputs,
            prices_include_gst=prices_include_gst(db),
            gst_context="sales",
        )
        for i, line_data in enumerate(data.lines):
            gst_code, gst_rate = resolve_line_gst(db, line_data)
            line_total = gst_totals.lines[i]
            line = EstimateLine(
                estimate_id=estimate_id,
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

        estimate.subtotal = gst_totals.subtotal
        estimate.tax_rate = gst_totals.effective_tax_rate
        estimate.tax_amount = gst_totals.tax_amount
        estimate.total = gst_totals.total

    db.commit()
    db.refresh(estimate)
    resp = EstimateResponse.model_validate(estimate)
    if estimate.customer:
        resp.customer_name = estimate.customer.name
    return resp


@router.get("/{estimate_id}/pdf")
def estimate_pdf(estimate_id: int, db: Session = Depends(get_db)):
    """Generate PDF — CEstimatePrintLayout::RenderPage() @ 0x00221800"""
    est = db.query(Estimate).filter(Estimate.id == estimate_id).first()
    if not est:
        raise HTTPException(status_code=404, detail="Estimate not found")
    company = get_settings(db)
    pdf_bytes = generate_estimate_pdf(est, company)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"inline; filename=Estimate_{est.estimate_number}.pdf"},
    )


@router.post("/{estimate_id}/email")
def email_estimate(estimate_id: int, data: DocumentEmailRequest, db: Session = Depends(get_db)):
    est = db.query(Estimate).filter(Estimate.id == estimate_id).first()
    if not est:
        raise HTTPException(status_code=404, detail="Estimate not found")
    company = get_settings(db)
    try:
        pdf_bytes = generate_estimate_pdf(est, company)
        html_body = render_document_email(
            document_label="Estimate",
            recipient_name=est.customer.name if est.customer else None,
            document_number=est.estimate_number,
            company_settings=company,
            amount=est.total,
            action_label="Valid until",
            action_value=est.expiration_date,
        )
        send_document_email(
            db,
            to_email=data.recipient,
            subject=data.subject or f"Estimate #{est.estimate_number}",
            html_body=html_body,
            attachment_bytes=pdf_bytes,
            attachment_name=f"Estimate_{est.estimate_number}.pdf",
            entity_type="estimate",
            entity_id=est.id,
        )
        return {"status": "sent"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Email failed: {str(e)}")


@router.post("/{estimate_id}/convert", response_model=InvoiceResponse)
def convert_to_invoice(estimate_id: int, db: Session = Depends(get_db)):
    """CEstimate::ConvertToInvoice() @ 0x001944A0 — deep-copies all fields/lines"""
    estimate = db.query(Estimate).filter(Estimate.id == estimate_id).first()
    if not estimate:
        raise HTTPException(status_code=404, detail="Estimate not found")
    if estimate.status == EstimateStatus.CONVERTED:
        raise HTTPException(status_code=400, detail="Estimate already converted")

    # Parse terms for due date
    settings = get_settings(db)
    terms = settings.get("default_terms", "Net 30")
    try:
        days = int(terms.lower().replace("net ", ""))
    except ValueError:
        days = 30
    due_date = estimate.date + timedelta(days=days)

    from app.routes.invoices import create_invoice

    invoice = create_invoice(InvoiceCreate(
        customer_id=estimate.customer_id,
        date=estimate.date,
        due_date=due_date,
        terms=terms,
        bill_address1=estimate.bill_address1,
        bill_address2=estimate.bill_address2,
        bill_city=estimate.bill_city,
        bill_state=estimate.bill_state,
        bill_zip=estimate.bill_zip,
        tax_rate=estimate.tax_rate,
        notes=estimate.notes,
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
            for line in estimate.lines
        ],
    ), db=db)

    estimate.status = EstimateStatus.CONVERTED
    estimate.converted_invoice_id = invoice.id

    db.commit()
    stored_invoice = db.query(Invoice).filter(Invoice.id == invoice.id).one()
    resp = InvoiceResponse.model_validate(stored_invoice)
    if stored_invoice.customer:
        resp.customer_name = stored_invoice.customer.name
    return resp
