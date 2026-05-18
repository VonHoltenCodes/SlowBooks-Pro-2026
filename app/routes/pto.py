# ============================================================================
# Paid time off — policies, per-employee accrual balances, time-off requests
# ============================================================================

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.payroll import Employee
from app.models.pto import (
    PTOPolicy,
    PTOAccrual,
    PTORequest,
    PTOType,
    AccrualMethod,
    PTORequestStatus,
)
from app.schemas.pto import (
    PTOPolicyCreate,
    PTOPolicyResponse,
    PTOAccrualCreate,
    PTOAccrualResponse,
    PTORequestCreate,
    PTORequestDecision,
    PTORequestResponse,
)
from app.services.pto_accrual import compute_period_accrual, apply_accrual

router = APIRouter(prefix="/api/pto", tags=["pto"])


# --- Policies --------------------------------------------------------------
@router.get("/policies", response_model=list[PTOPolicyResponse])
def list_policies(db: Session = Depends(get_db)):
    return db.query(PTOPolicy).order_by(PTOPolicy.name).all()


@router.post("/policies", response_model=PTOPolicyResponse, status_code=201)
def create_policy(data: PTOPolicyCreate, db: Session = Depends(get_db)):
    try:
        pto_type = PTOType(data.pto_type)
        method = AccrualMethod(data.accrual_method)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid value: {e}")
    policy = PTOPolicy(
        name=data.name,
        pto_type=pto_type,
        accrual_method=method,
        accrual_rate=data.accrual_rate,
        max_carryover=data.max_carryover,
        max_balance=data.max_balance,
    )
    db.add(policy)
    db.commit()
    db.refresh(policy)
    return policy


# --- Accruals --------------------------------------------------------------
@router.get("/accruals", response_model=list[PTOAccrualResponse])
def list_accruals(
    employee_id: int = Query(default=None), db: Session = Depends(get_db)
):
    q = db.query(PTOAccrual)
    if employee_id:
        q = q.filter(PTOAccrual.employee_id == employee_id)
    return q.all()


@router.post("/accruals", response_model=PTOAccrualResponse, status_code=201)
def create_accrual(data: PTOAccrualCreate, db: Session = Depends(get_db)):
    if not db.query(Employee).filter(Employee.id == data.employee_id).first():
        raise HTTPException(status_code=404, detail="Employee not found")
    if not db.query(PTOPolicy).filter(PTOPolicy.id == data.policy_id).first():
        raise HTTPException(status_code=404, detail="Policy not found")
    existing = (
        db.query(PTOAccrual)
        .filter(
            PTOAccrual.employee_id == data.employee_id,
            PTOAccrual.policy_id == data.policy_id,
        )
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=400, detail="Employee already enrolled in this policy"
        )
    accrual = PTOAccrual(
        employee_id=data.employee_id,
        policy_id=data.policy_id,
        balance=data.balance,
    )
    db.add(accrual)
    db.commit()
    db.refresh(accrual)
    return accrual


class AccrueRequest(BaseModel):
    hours_worked: float = 0  # required for per-hour-worked policies (e.g. WA sick)


@router.post("/accruals/{accrual_id}/accrue", response_model=PTOAccrualResponse)
def run_accrual(accrual_id: int, data: AccrueRequest, db: Session = Depends(get_db)):
    """Apply one accrual cycle (a pay period, or an annual grant) to a balance."""
    accrual = db.query(PTOAccrual).filter(PTOAccrual.id == accrual_id).first()
    if not accrual:
        raise HTTPException(status_code=404, detail="Accrual record not found")
    policy = db.query(PTOPolicy).filter(PTOPolicy.id == accrual.policy_id).first()
    if not policy:
        raise HTTPException(status_code=404, detail="Policy not found")

    earned = compute_period_accrual(policy, Decimal(str(data.hours_worked or 0)))
    new_balance = apply_accrual(
        Decimal(str(accrual.balance or 0)),
        earned,
        Decimal("0"),
        Decimal(str(policy.max_balance)) if policy.max_balance is not None else None,
    )
    accrual.accrued_ytd = (accrual.accrued_ytd or 0) + earned
    accrual.balance = new_balance
    db.commit()
    db.refresh(accrual)
    return accrual


# --- Requests --------------------------------------------------------------
@router.get("/requests", response_model=list[PTORequestResponse])
def list_requests(
    employee_id: int = Query(default=None),
    status: str = Query(default=None),
    db: Session = Depends(get_db),
):
    q = db.query(PTORequest)
    if employee_id:
        q = q.filter(PTORequest.employee_id == employee_id)
    if status:
        try:
            q = q.filter(PTORequest.status == PTORequestStatus(status))
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid status: {status}")
    results = []
    for req in q.order_by(PTORequest.start_date.desc()).all():
        r = PTORequestResponse.model_validate(req)
        if req.employee:
            r.employee_name = req.employee.full_name
        results.append(r)
    return results


@router.post("/requests", response_model=PTORequestResponse, status_code=201)
def create_request(data: PTORequestCreate, db: Session = Depends(get_db)):
    if not db.query(Employee).filter(Employee.id == data.employee_id).first():
        raise HTTPException(status_code=404, detail="Employee not found")
    if data.end_date < data.start_date:
        raise HTTPException(status_code=400, detail="end_date is before start_date")
    try:
        pto_type = PTOType(data.pto_type)
    except ValueError:
        raise HTTPException(
            status_code=400, detail=f"Invalid pto_type: {data.pto_type}"
        )
    req = PTORequest(
        employee_id=data.employee_id,
        start_date=data.start_date,
        end_date=data.end_date,
        hours=data.hours,
        pto_type=pto_type,
        notes=data.notes,
    )
    db.add(req)
    db.commit()
    db.refresh(req)
    return PTORequestResponse.model_validate(req)


@router.post("/requests/{request_id}/decision", response_model=PTORequestResponse)
def decide_request(
    request_id: int, data: PTORequestDecision, db: Session = Depends(get_db)
):
    req = db.query(PTORequest).filter(PTORequest.id == request_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="PTO request not found")
    if data.status not in ("approved", "denied"):
        raise HTTPException(status_code=400, detail="status must be approved or denied")
    if req.status != PTORequestStatus.PENDING:
        raise HTTPException(status_code=400, detail="Request already decided")

    req.status = PTORequestStatus(data.status)
    req.approver_id = data.approver_id

    # Approving a request draws the hours down from a matching accrual balance.
    if req.status == PTORequestStatus.APPROVED:
        accrual = (
            db.query(PTOAccrual)
            .join(PTOPolicy, PTOAccrual.policy_id == PTOPolicy.id)
            .filter(
                PTOAccrual.employee_id == req.employee_id,
                PTOPolicy.pto_type == req.pto_type,
            )
            .first()
        )
        if accrual:
            hours = Decimal(str(req.hours or 0))
            accrual.balance = max(
                Decimal("0"), Decimal(str(accrual.balance or 0)) - hours
            )
            accrual.used_ytd = (accrual.used_ytd or 0) + hours

    db.commit()
    db.refresh(req)
    return PTORequestResponse.model_validate(req)
