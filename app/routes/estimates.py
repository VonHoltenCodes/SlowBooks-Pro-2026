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
from app.models.invoices import Invoice, InvoiceLine, InvoiceStatus
from app.models.contacts import Customer
from app.schemas.estimates import EstimateCreate, EstimateUpdate, EstimateResponse
from app.schemas.invoices import InvoiceResponse
from app.services.pdf_service import generate_estimate_pdf
from app.services.settings_service import get_all_settings as get_settings, set_setting

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
    subtotal, tax_amount, total = compute_line_totals(data.lines, data.tax_rate)

    estimate = Estimate(
        estimate_number=estimate_number,
        customer_id=data.customer_id,
        date=data.date,
        expiration_date=data.expiration_date,
        subtotal=subtotal,
        tax_rate=data.tax_rate,
        tax_amount=tax_amount,
        total=total,
        notes=data.notes,
    )
    db.add(estimate)
    db.flush()

    for i, line_data in enumerate(data.lines):
        line = EstimateLine(
            estimate_id=estimate.id,
            item_id=line_data.item_id,
            description=line_data.description,
            quantity=line_data.quantity,
            rate=line_data.rate,
            amount=line_data.quantity * line_data.rate,
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
        for i, line_data in enumerate(data.lines):
            line = EstimateLine(
                estimate_id=estimate_id,
                item_id=line_data.item_id,
                description=line_data.description,
                quantity=line_data.quantity,
                rate=line_data.rate,
                amount=line_data.quantity * line_data.rate,
                class_name=line_data.class_name,
                line_order=line_data.line_order or i,
            )
            db.add(line)

        tax_rate = data.tax_rate if data.tax_rate is not None else estimate.tax_rate
        subtotal, tax_amount, total = compute_line_totals(data.lines, tax_rate)
        estimate.subtotal = subtotal
        estimate.tax_amount = tax_amount
        estimate.total = total

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


@router.get("/{estimate_id}/print-preview")
def estimate_print_preview(estimate_id: int, db: Session = Depends(get_db)):
    """Render estimate as HTML page for browser print dialog (window.print())"""
    est = db.query(Estimate).filter(Estimate.id == estimate_id).first()
    if not est:
        raise HTTPException(status_code=404, detail="Estimate not found")
    company = get_settings(db)
    from jinja2 import Environment, FileSystemLoader
    from pathlib import Path
    template_dir = Path(__file__).parent.parent / "templates"
    env = Environment(loader=FileSystemLoader(str(template_dir)), autoescape=True)
    from app.services.pdf_service import _format_currency, _format_date
    env.filters["currency"] = _format_currency
    env.filters["fdate"] = _format_date
    template = env.get_template("estimate_pdf.html")
    if est.customer and not hasattr(est, 'customer_name'):
        est.customer_name = est.customer.name
    html_str = template.render(est=est, company=company)
    html_str = html_str.replace("</body>", "<script>window.onload=function(){window.print();}</script></body>")
    from fastapi.responses import HTMLResponse
    return HTMLResponse(content=html_str)


@router.post("/{estimate_id}/convert", response_model=InvoiceResponse)
def convert_to_invoice(estimate_id: int, db: Session = Depends(get_db)):
    """CEstimate::ConvertToInvoice() @ 0x001944A0 — deep-copies all fields/lines"""
    estimate = db.query(Estimate).filter(Estimate.id == estimate_id).first()
    if not estimate:
        raise HTTPException(status_code=404, detail="Estimate not found")
    if estimate.status == EstimateStatus.CONVERTED:
        raise HTTPException(status_code=400, detail="Estimate already converted")

    # Get next invoice number
    from app.routes.invoices import _next_invoice_number
    invoice_number = _next_invoice_number(db)

    # Parse terms for due date
    settings = get_settings(db)
    terms = settings.get("default_terms", "Net 30")
    try:
        days = int(terms.lower().replace("net ", ""))
    except ValueError:
        days = 30
    due_date = estimate.date + timedelta(days=days)

    invoice = Invoice(
        invoice_number=invoice_number,
        customer_id=estimate.customer_id,
        status=InvoiceStatus.DRAFT,
        date=estimate.date,
        due_date=due_date,
        terms=terms,
        bill_address1=estimate.bill_address1,
        bill_address2=estimate.bill_address2,
        bill_city=estimate.bill_city,
        bill_state=estimate.bill_state,
        bill_zip=estimate.bill_zip,
        subtotal=estimate.subtotal,
        tax_rate=estimate.tax_rate,
        tax_amount=estimate.tax_amount,
        total=estimate.total,
        balance_due=estimate.total,
        notes=estimate.notes,
    )
    db.add(invoice)
    db.flush()

    for eline in estimate.lines:
        iline = InvoiceLine(
            invoice_id=invoice.id,
            item_id=eline.item_id,
            description=eline.description,
            quantity=eline.quantity,
            rate=eline.rate,
            amount=eline.amount,
            class_name=eline.class_name,
            line_order=eline.line_order,
        )
        db.add(iline)

    estimate.status = EstimateStatus.CONVERTED
    estimate.converted_invoice_id = invoice.id

    # Journal Entry — DR A/R for total, CR income account per line item
    ar_id = get_ar_account_id(db)
    default_income_id = get_default_income_account_id(db)
    tax_account_id = get_sales_tax_account_id(db)

    if ar_id and default_income_id:
        from decimal import Decimal
        from app.models.items import Item
        journal_lines = []
        # Debit A/R for total
        journal_lines.append({
            "account_id": ar_id,
            "debit": Decimal(str(invoice.total)),
            "credit": Decimal("0"),
            "description": f"Invoice #{invoice_number}",
        })
        # Credit income for each line item
        for eline in estimate.lines:
            line_amount = Decimal(str(eline.amount))
            if line_amount == 0:
                continue
            income_id = default_income_id
            if eline.item_id:
                item = db.query(Item).filter(Item.id == eline.item_id).first()
                if item and item.income_account_id:
                    income_id = item.income_account_id
            journal_lines.append({
                "account_id": income_id,
                "debit": Decimal("0"),
                "credit": line_amount,
                "description": eline.description or "",
            })
        # Credit sales tax if any
        if invoice.tax_amount and invoice.tax_amount > 0 and tax_account_id:
            journal_lines.append({
                "account_id": tax_account_id,
                "debit": Decimal("0"),
                "credit": Decimal(str(invoice.tax_amount)),
                "description": "Sales tax",
            })

        customer = estimate.customer
        txn = create_journal_entry(
            db, estimate.date,
            f"Invoice #{invoice_number} - {customer.name if customer else ''}",
            journal_lines, source_type="invoice", source_id=invoice.id,
            reference=invoice_number,
        )
        invoice.transaction_id = txn.id

    db.commit()
    db.refresh(invoice)
    resp = InvoiceResponse.model_validate(invoice)
    if invoice.customer:
        resp.customer_name = invoice.customer.name
    return resp
