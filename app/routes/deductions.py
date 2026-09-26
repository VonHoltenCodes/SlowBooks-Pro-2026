# ============================================================================
# Garnishment orders. Voluntary deductions and benefits live in the benefits
# engine (app/routes/benefits.py).
# ============================================================================

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.payroll import Employee
from app.models.deductions import (
    GarnishmentOrder,
    GarnishmentType,
    GarnishmentMethod,
)
from app.schemas.deductions import (
    GarnishmentOrderCreate,
    GarnishmentOrderResponse,
)

router = APIRouter(prefix="/api/deductions", tags=["deductions"])


# --- Garnishment orders ----------------------------------------------------
@router.get("/garnishments", response_model=list[GarnishmentOrderResponse])
def list_garnishments(
    employee_id: int = Query(default=None), db: Session = Depends(get_db)
):
    q = db.query(GarnishmentOrder)
    if employee_id:
        q = q.filter(GarnishmentOrder.employee_id == employee_id)
    return q.order_by(GarnishmentOrder.priority).all()


@router.post("/garnishments", response_model=GarnishmentOrderResponse, status_code=201)
def create_garnishment(data: GarnishmentOrderCreate, db: Session = Depends(get_db)):
    if not db.query(Employee).filter(Employee.id == data.employee_id).first():
        raise HTTPException(status_code=404, detail="Employee not found")
    try:
        gtype = GarnishmentType(data.garnishment_type)
        method = GarnishmentMethod(data.calc_method)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid value: {e}")
    order = GarnishmentOrder(
        employee_id=data.employee_id,
        garnishment_type=gtype,
        calc_method=method,
        amount=data.amount,
        priority=data.priority,
        case_number=data.case_number,
        supports_secondary_family=data.supports_secondary_family,
        in_arrears_12_weeks=data.in_arrears_12_weeks,
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    return order


@router.post("/garnishments/{order_id}/end", response_model=GarnishmentOrderResponse)
def end_garnishment(order_id: int, db: Session = Depends(get_db)):
    """End a garnishment order: it is no longer withheld from future pay runs
    (runs read active orders only), and its record — case number, type,
    amount, priority — stays, as a court-ordered withholding's should. It
    used to be deleted outright from the Deductions page."""
    order = db.query(GarnishmentOrder).filter(GarnishmentOrder.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Garnishment order not found")
    if not order.is_active:
        raise HTTPException(
            status_code=400, detail="This garnishment order has already ended."
        )
    order.is_active = False
    db.commit()
    db.refresh(order)
    return order


@router.delete("/garnishments/{order_id}")
def remove_garnishment(order_id: int, db: Session = Depends(get_db)):
    """A garnishment order is ended, not deleted, so its record stays."""
    raise HTTPException(
        status_code=405,
        detail=(
            "A garnishment order is ended, not deleted, so its record stays: "
            f"use POST /api/deductions/garnishments/{order_id}/end"
        ),
    )
