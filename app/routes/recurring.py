# ============================================================================
# Recurring Invoices — CRUD + manual generate
# Feature 2: Schedule automatic invoice generation
# ============================================================================

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.recurring import RecurringInvoice, RecurringInvoiceLine
from app.models.contacts import Customer
from app.schemas.recurring import RecurringCreate, RecurringUpdate, RecurringResponse
from app.services.recurring_service import generate_due_invoices
from app.services.gst_lines import resolve_line_gst
from app.services.auth import require_permissions

router = APIRouter(prefix="/api/recurring", tags=["recurring"])


@router.get("", response_model=list[RecurringResponse])
def list_recurring(active_only: bool = False, db: Session = Depends(get_db), auth=Depends(require_permissions("sales.view"))):
    q = db.query(RecurringInvoice)
    if active_only:
        q = q.filter(RecurringInvoice.is_active == True)
    recs = q.order_by(RecurringInvoice.next_due).all()
    results = []
    for r in recs:
        resp = RecurringResponse.model_validate(r)
        if r.customer:
            resp.customer_name = r.customer.name
        results.append(resp)
    return results


@router.get("/{rec_id}", response_model=RecurringResponse)
def get_recurring(rec_id: int, db: Session = Depends(get_db), auth=Depends(require_permissions("sales.view"))):
    rec = db.query(RecurringInvoice).filter(RecurringInvoice.id == rec_id).first()
    if not rec:
        raise HTTPException(status_code=404, detail="Recurring invoice not found")
    resp = RecurringResponse.model_validate(rec)
    if rec.customer:
        resp.customer_name = rec.customer.name
    return resp


@router.post("", response_model=RecurringResponse, status_code=201)
def create_recurring(data: RecurringCreate, db: Session = Depends(get_db), auth=Depends(require_permissions("sales.manage"))):
    customer = db.query(Customer).filter(Customer.id == data.customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    rec = RecurringInvoice(
        customer_id=data.customer_id, frequency=data.frequency,
        start_date=data.start_date, end_date=data.end_date,
        next_due=data.start_date, terms=data.terms,
        tax_rate=data.tax_rate, notes=data.notes,
    )
    db.add(rec)
    db.flush()

    for i, line_data in enumerate(data.lines):
        gst_code, gst_rate = resolve_line_gst(db, line_data)
        db.add(RecurringInvoiceLine(
            recurring_invoice_id=rec.id, item_id=line_data.item_id,
            description=line_data.description, quantity=line_data.quantity,
            rate=line_data.rate, gst_code=gst_code, gst_rate=gst_rate,
            line_order=line_data.line_order or i,
        ))

    db.commit()
    db.refresh(rec)
    resp = RecurringResponse.model_validate(rec)
    resp.customer_name = customer.name
    return resp


@router.put("/{rec_id}", response_model=RecurringResponse)
def update_recurring(rec_id: int, data: RecurringUpdate, db: Session = Depends(get_db), auth=Depends(require_permissions("sales.manage"))):
    rec = db.query(RecurringInvoice).filter(RecurringInvoice.id == rec_id).first()
    if not rec:
        raise HTTPException(status_code=404, detail="Recurring invoice not found")

    for key, val in data.model_dump(exclude_unset=True, exclude={"lines"}).items():
        setattr(rec, key, val)

    if data.lines is not None:
        db.query(RecurringInvoiceLine).filter(RecurringInvoiceLine.recurring_invoice_id == rec_id).delete()
        for i, line_data in enumerate(data.lines):
            gst_code, gst_rate = resolve_line_gst(db, line_data)
            db.add(RecurringInvoiceLine(
                recurring_invoice_id=rec_id, item_id=line_data.item_id,
                description=line_data.description, quantity=line_data.quantity,
                rate=line_data.rate, gst_code=gst_code, gst_rate=gst_rate,
                line_order=line_data.line_order or i,
            ))

    db.commit()
    db.refresh(rec)
    resp = RecurringResponse.model_validate(rec)
    if rec.customer:
        resp.customer_name = rec.customer.name
    return resp


@router.delete("/{rec_id}")
def delete_recurring(rec_id: int, db: Session = Depends(get_db), auth=Depends(require_permissions("sales.manage"))):
    rec = db.query(RecurringInvoice).filter(RecurringInvoice.id == rec_id).first()
    if not rec:
        raise HTTPException(status_code=404, detail="Recurring invoice not found")
    db.delete(rec)
    db.commit()
    return {"message": "Recurring invoice deleted"}


@router.post("/generate")
def generate_now(db: Session = Depends(get_db), auth=Depends(require_permissions("sales.manage"))):
    """Manually trigger generation of all due recurring invoices."""
    created_ids = generate_due_invoices(db)
    return {"invoices_created": len(created_ids), "invoice_ids": created_ids}
