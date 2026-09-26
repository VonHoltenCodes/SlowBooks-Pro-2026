# ============================================================================
# Recurring Invoices — CRUD + manual generate
# Feature 2: Schedule automatic invoice generation
# ============================================================================

from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.routes.invoices.helpers import refuse_zero_total, resolve_line_taxable
from app.models.recurring import RecurringInvoice, RecurringInvoiceLine
from app.models.contacts import Customer
from app.schemas.recurring import RecurringCreate, RecurringUpdate, RecurringResponse
from app.services.accounting import compute_line_totals
from app.services.recurring_service import generate_due_invoices
from app.services.terminology import terms_from_db

router = APIRouter(prefix="/api/recurring", tags=["recurring"])


@router.get("", response_model=list[RecurringResponse])
def list_recurring(active_only: bool = False, db: Session = Depends(get_db)):
    q = db.query(RecurringInvoice)
    if active_only:
        q = q.filter(RecurringInvoice.is_active)
    recs = q.order_by(RecurringInvoice.next_due).all()
    results = []
    for r in recs:
        resp = RecurringResponse.model_validate(r)
        if r.customer:
            resp.customer_name = r.customer.name
        results.append(resp)
    return results


@router.get("/{rec_id}", response_model=RecurringResponse)
def get_recurring(rec_id: int, db: Session = Depends(get_db)):
    rec = db.query(RecurringInvoice).filter(RecurringInvoice.id == rec_id).first()
    if not rec:
        raise HTTPException(status_code=404, detail="Recurring invoice not found")
    resp = RecurringResponse.model_validate(rec)
    if rec.customer:
        resp.customer_name = rec.customer.name
    return resp


def _schedule_noun(db: Session) -> str:
    """The schedule as the company's vocabulary names it: "recurring
    invoice", or "recurring pledge" for a nonprofit."""
    return "recurring " + terms_from_db(db)("invoice")


@router.post("", response_model=RecurringResponse, status_code=201)
def create_recurring(data: RecurringCreate, db: Session = Depends(get_db)):
    customer = db.query(Customer).filter(Customer.id == data.customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    # Refused here rather than skipped at every run: a template that adds up
    # to nothing generated $0.00 invoices that then showed as overdue.
    resolve_line_taxable(db, data.lines, customer)
    refuse_zero_total(
        compute_line_totals(data.lines, data.tax_rate)[2], _schedule_noun(db)
    )

    rec = RecurringInvoice(
        customer_id=data.customer_id,
        frequency=data.frequency,
        start_date=data.start_date,
        end_date=data.end_date,
        next_due=data.start_date,
        terms=data.terms,
        tax_rate=data.tax_rate,
        notes=data.notes,
        class_id=data.class_id,
        job_id=data.job_id,
    )
    db.add(rec)
    db.flush()

    for i, line_data in enumerate(data.lines):
        db.add(
            RecurringInvoiceLine(
                recurring_invoice_id=rec.id,
                item_id=line_data.item_id,
                description=line_data.description,
                quantity=line_data.quantity,
                rate=line_data.rate,
                is_taxable=(
                    line_data.is_taxable if line_data.is_taxable is not None else True
                ),
                line_order=line_data.line_order or i,
            )
        )

    db.commit()
    db.refresh(rec)
    resp = RecurringResponse.model_validate(rec)
    resp.customer_name = customer.name
    return resp


@router.put("/{rec_id}", response_model=RecurringResponse)
def update_recurring(rec_id: int, data: RecurringUpdate, db: Session = Depends(get_db)):
    rec = db.query(RecurringInvoice).filter(RecurringInvoice.id == rec_id).first()
    if not rec:
        raise HTTPException(status_code=404, detail="Recurring invoice not found")

    if data.lines is not None:
        resolve_line_taxable(db, data.lines, rec.customer)
        tax_rate = data.tax_rate if data.tax_rate is not None else rec.tax_rate
        refuse_zero_total(
            compute_line_totals(data.lines, tax_rate)[2], _schedule_noun(db)
        )

    for key, val in data.model_dump(exclude_unset=True, exclude={"lines"}).items():
        setattr(rec, key, val)

    if data.lines is not None:
        db.query(RecurringInvoiceLine).filter(
            RecurringInvoiceLine.recurring_invoice_id == rec_id
        ).delete()
        for i, line_data in enumerate(data.lines):
            db.add(
                RecurringInvoiceLine(
                    recurring_invoice_id=rec_id,
                    item_id=line_data.item_id,
                    description=line_data.description,
                    quantity=line_data.quantity,
                    rate=line_data.rate,
                    is_taxable=(
                        line_data.is_taxable
                        if line_data.is_taxable is not None
                        else True
                    ),
                    line_order=line_data.line_order or i,
                )
            )

    db.commit()
    db.refresh(rec)
    resp = RecurringResponse.model_validate(rec)
    if rec.customer:
        resp.customer_name = rec.customer.name
    return resp


@router.delete("/{rec_id}")
def delete_recurring(rec_id: int, db: Session = Depends(get_db)):
    rec = db.query(RecurringInvoice).filter(RecurringInvoice.id == rec_id).first()
    if not rec:
        raise HTTPException(status_code=404, detail="Recurring invoice not found")
    db.delete(rec)
    db.commit()
    return {"message": "Recurring invoice deleted"}


@router.post("/generate")
def generate_now(as_of: date = Query(default=None), db: Session = Depends(get_db)):
    """Manually trigger generation of all due recurring invoices — one
    installment per template per call. `as_of` (default today) lets a
    catch-up or a test run generate a past installment deterministically."""
    skipped: list = []
    created_ids = generate_due_invoices(db, as_of, skipped=skipped)
    return {
        "invoices_created": len(created_ids),
        "invoice_ids": created_ids,
        "skipped": skipped,
    }
