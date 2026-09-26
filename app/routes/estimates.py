# ============================================================================
# Estimates — "Convert to Invoice" deep-copies every field and line item,
# then marks the estimate CONVERTED. PDFs are rendered with WeasyPrint.
# ============================================================================

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from fastapi.responses import Response
from sqlalchemy.orm import Session, joinedload, selectinload

from app.database import get_db
from app.routes.invoices.helpers import (
    _due_date_from_terms,
    refuse_zero_total,
    resolve_line_taxable,
)
from app.routes._helpers import clamp_pagination
from app.models.estimates import Estimate, EstimateLine, EstimateStatus
from app.models.invoices import Invoice, InvoiceLine, InvoiceStatus
from app.models.contacts import Customer
from app.schemas.estimates import EstimateCreate, EstimateUpdate, EstimateResponse
from app.schemas.invoices import InvoiceResponse
from app.services.pdf_service import generate_estimate_pdf
from app.services.numbering import next_estimate_number, next_invoice_number
from app.services.settings_service import get_all_settings as get_settings, set_setting
from app.services.accounting import (
    _q,
    compute_line_totals,
    create_journal_entry,
    get_ar_account_id,
    get_default_income_account_id,
    get_sales_tax_account_id,
)
from app.services.donor_documents import document_label
from app.services.terminology import document_reference, terms_from_db

router = APIRouter(prefix="/api/estimates", tags=["estimates"])


_ADDRESS_PARTS = ("address1", "address2", "city", "state", "zip")


def _customer_bill_to(customer) -> dict:
    return {f"bill_{k}": getattr(customer, f"bill_{k}", None) for k in _ADDRESS_PARTS}


@router.get("", response_model=list[EstimateResponse])
def list_estimates(
    status: str = None,
    customer_id: int = None,
    skip: int = 0,
    limit: int = 500,
    db: Session = Depends(get_db),
):
    skip, limit = clamp_pagination(skip, limit)
    # Eager-load .customer and .lines to avoid N+1 during model_validate.
    q = db.query(Estimate).options(
        joinedload(Estimate.customer),
        selectinload(Estimate.lines),
    )
    if status:
        q = q.filter(Estimate.status == status)
    if customer_id:
        q = q.filter(Estimate.customer_id == customer_id)
    estimates = q.order_by(Estimate.date.desc()).offset(skip).limit(limit).all()
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

    cust_id = customer.id
    cust_name = customer.name
    # The estimate is addressed to the customer, as an invoice is; it was
    # never filled, so its PDF and the invoice it became had no address.
    bill_to = _customer_bill_to(customer)
    resolve_line_taxable(db, data.lines, customer)
    subtotal, tax_amount, total = compute_line_totals(data.lines, data.tax_rate)

    estimate = None
    estimate_number = None
    last_err = None
    # Same race as create_invoice: next_estimate_number's check-then-insert
    # window lets two concurrent creates pick the same number. Retry on
    # IntegrityError; the UNIQUE constraint is the safety net.
    for _ in range(10):
        estimate_number = next_estimate_number(db)
        estimate = Estimate(
            estimate_number=estimate_number,
            customer_id=cust_id,
            date=data.date,
            expiration_date=data.expiration_date,
            subtotal=subtotal,
            tax_rate=data.tax_rate,
            tax_amount=tax_amount,
            total=total,
            notes=data.notes,
            class_id=data.class_id,
            job_id=data.job_id,
            **bill_to,
        )
        db.add(estimate)
        try:
            db.flush()
            break
        except IntegrityError as e:
            if "estimate_number" not in str(e.orig).lower():
                raise
            last_err = e
            db.rollback()
            estimate = None
    if estimate is None:
        raise HTTPException(
            status_code=503,
            detail="Could not assign a unique estimate number; please retry.",
        ) from last_err

    for i, line_data in enumerate(data.lines):
        line = EstimateLine(
            estimate_id=estimate.id,
            item_id=line_data.item_id,
            description=line_data.description,
            quantity=line_data.quantity,
            rate=line_data.rate,
            amount=_q(Decimal(str(line_data.quantity)) * Decimal(str(line_data.rate))),
            class_name=line_data.class_name,
            job_id=line_data.job_id,
            cost_code_id=line_data.cost_code_id,
            unit_cost=line_data.unit_cost,
            is_taxable=(
                line_data.is_taxable if line_data.is_taxable is not None else True
            ),
            line_order=line_data.line_order or i,
        )
        db.add(line)

    numeric_part = estimate_number.removeprefix(
        get_settings(db).get("estimate_prefix", "E-")
    )
    if numeric_part.isdigit():
        set_setting(db, "estimate_next_number", str(int(numeric_part) + 1))

    db.commit()
    db.refresh(estimate)
    resp = EstimateResponse.model_validate(estimate)
    resp.customer_name = cust_name
    return resp


@router.put("/{estimate_id}", response_model=EstimateResponse)
def update_estimate(
    estimate_id: int, data: EstimateUpdate, db: Session = Depends(get_db)
):
    estimate = db.query(Estimate).filter(Estimate.id == estimate_id).first()
    if not estimate:
        raise HTTPException(status_code=404, detail="Estimate not found")

    changes = data.model_dump(exclude_unset=True, exclude={"lines"})
    if changes.get("customer_id") not in (None, estimate.customer_id):
        customer = db.get(Customer, changes["customer_id"])
        if not customer:
            raise HTTPException(status_code=404, detail="Customer not found")
        # a different customer, so their address
        changes.update(_customer_bill_to(customer))
    for key, val in changes.items():
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
                amount=_q(
                    Decimal(str(line_data.quantity)) * Decimal(str(line_data.rate))
                ),
                class_name=line_data.class_name,
                job_id=line_data.job_id,
                cost_code_id=line_data.cost_code_id,
                unit_cost=line_data.unit_cost,
                is_taxable=(
                    line_data.is_taxable if line_data.is_taxable is not None else True
                ),
                line_order=line_data.line_order or i,
            )
            db.add(line)

        tax_rate = data.tax_rate if data.tax_rate is not None else estimate.tax_rate
        resolve_line_taxable(db, data.lines, estimate.customer)
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
    """Generate the estimate PDF."""
    est = db.query(Estimate).filter(Estimate.id == estimate_id).first()
    if not est:
        raise HTTPException(status_code=404, detail="Estimate not found")
    company = get_settings(db)
    pdf_bytes = generate_estimate_pdf(est, company)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"inline; filename=Estimate_{est.estimate_number}.pdf"
        },
    )


@router.get("/{estimate_id}/print-preview")
def estimate_print_preview(estimate_id: int, db: Session = Depends(get_db)):
    """Render estimate as HTML page for browser print dialog (window.print())"""
    est = db.query(Estimate).filter(Estimate.id == estimate_id).first()
    if not est:
        raise HTTPException(status_code=404, detail="Estimate not found")
    company = get_settings(db)
    from fastapi.responses import HTMLResponse

    from app.services.pdf_service import _render

    # The PDF's own renderer: one set of filters and helpers for both.
    html_str = _render("estimate_pdf.html", company, est=est)
    html_str = html_str.replace(
        "</body>", "<script>window.onload=function(){window.print();}</script></body>"
    )
    return HTMLResponse(content=html_str)


@router.post("/{estimate_id}/convert", response_model=InvoiceResponse)
def convert_to_invoice(estimate_id: int, db: Session = Depends(get_db)):
    """Convert to invoice — deep-copies all fields and lines."""
    words = terms_from_db(db)
    from app.services.closing_date import check_closing_date

    estimate = db.query(Estimate).filter(Estimate.id == estimate_id).first()
    if not estimate:
        raise HTTPException(status_code=404, detail="Estimate not found")
    if estimate.status == EstimateStatus.CONVERTED:
        raise HTTPException(status_code=400, detail="Estimate already converted")
    # The invoice is dated the day it is made, as QuickBooks does: keeping
    # the estimate's date back-dated the sale and its due date (2.17.3
    # exploratory W-L10). Its journal posts on that date, so that is the
    # date the closing lock is checked against.
    today = date.today()
    check_closing_date(db, today)

    # A new invoice gets the customer's CURRENT tax treatment, computed —
    # not the estimate's stored tax (see taxed_copy_lines).
    from app.services.accounting import compute_line_totals, taxed_copy_lines

    copied = taxed_copy_lines(estimate.lines, estimate.customer)
    subtotal, tax_amount, total = compute_line_totals(copied, estimate.tax_rate)
    refuse_zero_total(total, "estimate", "converting it")

    invoice_number = next_invoice_number(db)

    # Terms and due date as a new invoice for this customer gets them: the
    # customer's terms, else the company's (the form does the same).
    settings = get_settings(db)
    customer = estimate.customer
    terms = (customer.terms if customer else None) or settings.get(
        "default_terms", "Net 30"
    )
    due_date = _due_date_from_terms(today, terms)

    # The bill-to is the estimate's, else the customer's, and the ship-to
    # the customer's; the notes are the estimate's, else the company's
    # default invoice notes. The converted invoice printed with no address
    # and without the default notes (2.17.3 exploratory F21).
    address = {f"bill_{k}": getattr(estimate, f"bill_{k}") for k in _ADDRESS_PARTS}
    if customer and not any(address.values()):
        address = _customer_bill_to(customer)
    if customer:
        address.update(
            {f"ship_{k}": getattr(customer, f"ship_{k}") for k in _ADDRESS_PARTS}
        )

    invoice = Invoice(
        invoice_number=invoice_number,
        customer_id=estimate.customer_id,
        status=InvoiceStatus.DRAFT,
        date=today,
        due_date=due_date,
        terms=terms,
        subtotal=subtotal,
        tax_rate=estimate.tax_rate,
        tax_amount=tax_amount,
        total=total,
        balance_due=total,
        class_id=estimate.class_id,
        job_id=estimate.job_id,
        notes=estimate.notes or settings.get("invoice_notes") or None,
        **address,
    )
    face = document_label(invoice, words)
    db.add(invoice)
    db.flush()

    for eline, cline in zip(estimate.lines, copied):
        iline = InvoiceLine(
            invoice_id=invoice.id,
            item_id=eline.item_id,
            description=eline.description,
            quantity=eline.quantity,
            rate=eline.rate,
            amount=eline.amount,
            class_name=eline.class_name,
            job_id=eline.job_id,
            cost_code_id=eline.cost_code_id,
            is_taxable=cline.is_taxable,
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
        from app.models.items import Item

        journal_lines = []
        # Debit A/R for total
        journal_lines.append(
            {
                "account_id": ar_id,
                "debit": Decimal(str(invoice.total)),
                "credit": Decimal("0"),
                "description": document_reference(face, invoice_number),
            }
        )
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
            journal_lines.append(
                {
                    "account_id": income_id,
                    "debit": Decimal("0"),
                    "credit": line_amount,
                    "description": eline.description or "",
                }
            )
        # Credit sales tax if any
        if invoice.tax_amount and invoice.tax_amount > 0 and tax_account_id:
            journal_lines.append(
                {
                    "account_id": tax_account_id,
                    "debit": Decimal("0"),
                    "credit": Decimal(str(invoice.tax_amount)),
                    "description": "Sales tax",
                }
            )

        txn = create_journal_entry(
            db,
            today,
            document_reference(face, invoice_number, customer.name if customer else ""),
            journal_lines,
            source_type="invoice",
            source_id=invoice.id,
            reference=invoice_number,
            class_id=invoice.class_id,
            job_id=invoice.job_id,
        )
        invoice.transaction_id = txn.id

    # Phase 11 (audit fix): estimate→invoice conversion is a NEW sale from
    # an accounting standpoint. Post inventory movements for each inventory
    # line so the ledger stays consistent with the A/R journal entry.
    db.flush()
    db.refresh(invoice)
    from app.services.inventory_hooks import post_sale_for_invoice

    post_sale_for_invoice(db, invoice, txn_date=today)

    db.commit()
    db.refresh(invoice)
    resp = InvoiceResponse.model_validate(invoice)
    if invoice.customer:
        resp.customer_name = invoice.customer.name
    return resp
