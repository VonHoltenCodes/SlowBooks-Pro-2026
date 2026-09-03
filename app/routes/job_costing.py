# ============================================================================
# Job costing routes: cost types, equipment, Job Cost Entries (incl.
# allocations and void). Bookkeeper-writable like the rest of daily books.
# ============================================================================

from datetime import date
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models.accounts import Account, AccountType
from app.models.cost_codes import CostCode
from app.models.job_costing import CostType, Equipment, JobCost, JobCostLine
from app.models.jobs import Job
from app.schemas.job_costing import (
    AllocationCreate,
    CostTypeCreate,
    CostTypeResponse,
    CostTypeUpdate,
    EquipmentCreate,
    EquipmentResponse,
    EquipmentUpdate,
    JobCostCreate,
    JobCostLineResponse,
    JobCostResponse,
)
from app.services.closing_date import check_closing_date
from app.services.job_costing import (
    allocate_cost,
    cost_type_map,
    ensure_default_cost_types,
    next_job_cost_number,
    post_job_cost,
    resolve_line_accounts,
    void_job_cost,
)

# ── Cost types ───────────────────────────────────────────────────────────

cost_types_router = APIRouter(prefix="/api/cost-types", tags=["cost-types"])


def _ct_response(row: CostType) -> CostTypeResponse:
    data = CostTypeResponse.model_validate(row)
    data.default_account_name = (
        row.default_account.name if row.default_account else None
    )
    data.offset_account_name = row.offset_account.name if row.offset_account else None
    data.burden_offset_account_name = (
        row.burden_offset_account.name if row.burden_offset_account else None
    )
    return data


def _account_or_404(db: Session, account_id: Optional[int]):
    if account_id is not None and not db.get(Account, account_id):
        raise HTTPException(status_code=404, detail="Account not found")


@cost_types_router.get("", response_model=list[CostTypeResponse])
def list_cost_types(include_inactive: bool = False, db: Session = Depends(get_db)):
    ensure_default_cost_types(db)
    db.commit()
    q = db.query(CostType)
    if not include_inactive:
        q = q.filter(CostType.is_active.is_(True))
    return [
        _ct_response(r) for r in q.order_by(CostType.sort_order, CostType.code).all()
    ]


@cost_types_router.post("", response_model=CostTypeResponse, status_code=201)
def create_cost_type(data: CostTypeCreate, db: Session = Depends(get_db)):
    ensure_default_cost_types(db)
    if db.query(CostType).filter(CostType.code == data.code).first():
        raise HTTPException(
            status_code=409, detail=f"Cost type '{data.code}' already exists"
        )
    for f in ("default_account_id", "offset_account_id", "burden_offset_account_id"):
        _account_or_404(db, getattr(data, f))
    row = CostType(**data.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return _ct_response(row)


@cost_types_router.put("/{type_id}", response_model=CostTypeResponse)
def update_cost_type(type_id: int, data: CostTypeUpdate, db: Session = Depends(get_db)):
    row = db.get(CostType, type_id)
    if not row:
        raise HTTPException(status_code=404, detail="Cost type not found")
    changes = data.model_dump(exclude_unset=True)
    for f in ("default_account_id", "offset_account_id", "burden_offset_account_id"):
        if f in changes:
            _account_or_404(db, changes[f])
    if changes.get("name") is not None and not changes["name"].strip():
        raise HTTPException(status_code=422, detail="Cost type name cannot be blank")
    for k, v in changes.items():
        setattr(row, k, v)
    db.commit()
    db.refresh(row)
    return _ct_response(row)


@cost_types_router.delete("/{type_id}")
def delete_cost_type(type_id: int, db: Session = Depends(get_db)):
    row = db.get(CostType, type_id)
    if not row:
        raise HTTPException(status_code=404, detail="Cost type not found")
    in_use = (
        db.query(CostCode.id).filter(CostCode.cost_type == row.code).first()
        or db.query(JobCostLine.id).filter(JobCostLine.cost_type == row.code).first()
    )
    if in_use or row.code in ("labor", "material", "subcontract", "equipment", "other"):
        raise HTTPException(
            status_code=400,
            detail="Cost type is in use or is a standard type — mark it inactive instead",
        )
    db.delete(row)
    db.commit()
    return {"message": "Cost type deleted"}


_OFFSET_ACCOUNTS = (
    # (key, name, type, number) — the applied-cost pattern: job cost is debited,
    # the applied account is credited, and the real payroll / equipment /
    # overhead expense sits alongside it, so the company P&L is unchanged
    # while every job carries its share.
    ("payroll_clearing", "Payroll Clearing", AccountType.LIABILITY, "2150"),
    ("applied_burden", "Applied Labor Burden", AccountType.EXPENSE, "6910"),
    ("applied_equipment", "Applied Equipment Cost", AccountType.EXPENSE, "6920"),
    ("applied_overhead", "Applied Overhead", AccountType.EXPENSE, "6930"),
    ("job_cost", "Job Costs", AccountType.COGS, "5100"),
)


@cost_types_router.post("/setup-offsets", response_model=list[CostTypeResponse])
def setup_offset_accounts(db: Session = Depends(get_db)):
    """Create the applied-cost accounts (if missing) and point every cost
    type at them, so Job Cost Entries and time postings work out of the box.
    Existing choices on a cost type are left alone."""
    ensure_default_cost_types(db)
    found: dict[str, Account] = {}
    for key, name, atype, number in _OFFSET_ACCOUNTS:
        acct = db.query(Account).filter(Account.name == name).first()
        if not acct:
            # Keep the suggested number only if the chart hasn't used it
            taken = (
                db.query(Account.id).filter(Account.account_number == number).first()
            )
            acct = Account(
                name=name, account_type=atype, account_number=None if taken else number
            )
            db.add(acct)
            db.flush()
        found[key] = acct
    for ct in db.query(CostType).all():
        if not ct.default_account_id:
            ct.default_account_id = found["job_cost"].id
        if not ct.offset_account_id:
            if ct.is_labor:
                ct.offset_account_id = found["payroll_clearing"].id
            elif ct.code == "equipment":
                ct.offset_account_id = found["applied_equipment"].id
            else:
                ct.offset_account_id = found["applied_overhead"].id
        if ct.is_labor and not ct.burden_offset_account_id:
            ct.burden_offset_account_id = found["applied_burden"].id
    db.commit()
    return list_cost_types(include_inactive=False, db=db)


# ── Equipment ────────────────────────────────────────────────────────────

equipment_router = APIRouter(prefix="/api/equipment", tags=["equipment"])


def _eq_response(row: Equipment) -> EquipmentResponse:
    data = EquipmentResponse.model_validate(row)
    data.cost_code_label = row.cost_code.label if row.cost_code else None
    return data


@equipment_router.get("", response_model=list[EquipmentResponse])
def list_equipment(include_inactive: bool = False, db: Session = Depends(get_db)):
    q = db.query(Equipment).options(joinedload(Equipment.cost_code))
    if not include_inactive:
        q = q.filter(Equipment.is_active.is_(True))
    return [_eq_response(r) for r in q.order_by(Equipment.name).all()]


@equipment_router.post("", response_model=EquipmentResponse, status_code=201)
def create_equipment(data: EquipmentCreate, db: Session = Depends(get_db)):
    _account_or_404(db, data.recovery_account_id)
    row = Equipment(**data.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return _eq_response(row)


@equipment_router.put("/{eq_id}", response_model=EquipmentResponse)
def update_equipment(eq_id: int, data: EquipmentUpdate, db: Session = Depends(get_db)):
    row = db.get(Equipment, eq_id)
    if not row:
        raise HTTPException(status_code=404, detail="Equipment not found")
    changes = data.model_dump(exclude_unset=True)
    if "recovery_account_id" in changes:
        _account_or_404(db, changes["recovery_account_id"])
    for k, v in changes.items():
        setattr(row, k, v)
    db.commit()
    db.refresh(row)
    return _eq_response(row)


@equipment_router.delete("/{eq_id}")
def delete_equipment(eq_id: int, db: Session = Depends(get_db)):
    row = db.get(Equipment, eq_id)
    if not row:
        raise HTTPException(status_code=404, detail="Equipment not found")
    if db.query(JobCostLine.id).filter(JobCostLine.equipment_id == eq_id).first():
        raise HTTPException(
            status_code=400,
            detail="Equipment has posted job costs — mark it inactive instead",
        )
    db.delete(row)
    db.commit()
    return {"message": "Equipment deleted"}


# ── Job Cost Entries ─────────────────────────────────────────────────────

job_costs_router = APIRouter(prefix="/api/job-costs", tags=["job-costs"])


def _jc_response(jc: JobCost) -> JobCostResponse:
    data = JobCostResponse.model_validate(jc)
    data.job_name = jc.job.full_name if jc.job else None
    data.lines = []
    for ln in jc.lines:
        lr = JobCostLineResponse.model_validate(ln)
        lr.job_name = (
            ln.job.full_name if ln.job else (jc.job.full_name if jc.job else None)
        )
        lr.cost_code_label = ln.cost_code.label if ln.cost_code else None
        lr.debit_account_name = ln.debit_account.name if ln.debit_account else None
        lr.credit_account_name = ln.credit_account.name if ln.credit_account else None
        data.lines.append(lr)
    return data


def _jc_get(db: Session, jc_id: int) -> JobCost:
    jc = (
        db.query(JobCost)
        .options(joinedload(JobCost.job), joinedload(JobCost.lines))
        .filter(JobCost.id == jc_id)
        .first()
    )
    if not jc:
        raise HTTPException(status_code=404, detail="Job cost entry not found")
    return jc


@job_costs_router.get("", response_model=list[JobCostResponse])
def list_job_costs(
    job_id: Optional[int] = None,
    status: Optional[str] = None,
    start_date: Optional[date] = Query(default=None),
    end_date: Optional[date] = Query(default=None),
    db: Session = Depends(get_db),
):
    q = db.query(JobCost).options(joinedload(JobCost.job), joinedload(JobCost.lines))
    if job_id is not None:
        q = q.filter(
            (JobCost.job_id == job_id)
            | JobCost.id.in_(
                db.query(JobCostLine.job_cost_id).filter(JobCostLine.job_id == job_id)
            )
        )
    if status:
        q = q.filter(JobCost.status == status)
    if start_date:
        q = q.filter(JobCost.date >= start_date)
    if end_date:
        q = q.filter(JobCost.date <= end_date)
    return [
        _jc_response(jc)
        for jc in q.order_by(JobCost.date.desc(), JobCost.id.desc()).all()
    ]


@job_costs_router.get("/{jc_id}", response_model=JobCostResponse)
def get_job_cost(jc_id: int, db: Session = Depends(get_db)):
    return _jc_response(_jc_get(db, jc_id))


@job_costs_router.post("", response_model=JobCostResponse, status_code=201)
def create_job_cost(data: JobCostCreate, db: Session = Depends(get_db)):
    check_closing_date(db, data.date)
    if data.job_id is not None and not db.get(Job, data.job_id):
        raise HTTPException(status_code=404, detail="Job not found")
    types = cost_type_map(db)
    jc = JobCost(
        number=next_job_cost_number(db),
        date=data.date,
        job_id=data.job_id,
        memo=data.memo,
        source="manual",
    )
    db.add(jc)
    db.flush()
    for i, ln in enumerate(data.lines):
        if not (ln.job_id or data.job_id):
            raise HTTPException(status_code=422, detail=f"Line {i + 1}: pick a job")
        if ln.job_id and not db.get(Job, ln.job_id):
            raise HTTPException(status_code=404, detail=f"Line {i + 1}: job not found")
        code = db.get(CostCode, ln.cost_code_id) if ln.cost_code_id else None
        if ln.cost_code_id and not code:
            raise HTTPException(
                status_code=404, detail=f"Line {i + 1}: cost code not found"
            )
        equipment = db.get(Equipment, ln.equipment_id) if ln.equipment_id else None
        ctype = (
            ln.cost_type
            or (code.cost_type if code else None)
            or ("equipment" if equipment else "other")
        )
        if ctype not in types:
            raise HTTPException(
                status_code=422, detail=f"Line {i + 1}: unknown cost type '{ctype}'"
            )
        try:
            debit, credit = resolve_line_accounts(
                db,
                types,
                code,
                ctype,
                ln.debit_account_id,
                ln.credit_account_id,
                is_burden=ln.is_burden,
                equipment=equipment,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"Line {i + 1}: {exc}")
        rate = ln.rate
        if equipment and (rate is None or rate == 0):
            rate = Decimal(str(equipment.hourly_rate))
        # The schema pre-computes amount = qty × rate; when the rate came from
        # the equipment list that pre-computation was 0, so redo it here.
        amount = ln.amount
        if amount is None or (amount == 0 and rate and ln.quantity):
            amount = (Decimal(str(ln.quantity)) * Decimal(str(rate))).quantize(
                Decimal("0.01")
            )
        jc.lines.append(
            JobCostLine(
                job_cost_id=jc.id,
                job_id=ln.job_id,
                cost_code_id=(
                    code.id if code else (equipment.cost_code_id if equipment else None)
                ),
                cost_type=ctype,
                description=ln.description,
                quantity=ln.quantity,
                rate=rate,
                amount=amount,
                debit_account_id=debit,
                credit_account_id=credit,
                employee_id=ln.employee_id,
                equipment_id=ln.equipment_id,
                is_burden=ln.is_burden,
                is_billable=ln.is_billable,
                line_order=i,
            )
        )
    db.flush()
    try:
        post_job_cost(db, jc)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    db.commit()
    return _jc_response(_jc_get(db, jc.id))


@job_costs_router.post("/allocate", response_model=JobCostResponse, status_code=201)
def allocate(data: AllocationCreate, db: Session = Depends(get_db)):
    check_closing_date(db, data.date)
    job_ids = [t.job_id for t in data.targets]
    if not job_ids:
        job_ids = [j.id for j in db.query(Job).filter(Job.is_active.is_(True)).all()]
    if not job_ids:
        raise HTTPException(status_code=422, detail="No jobs to allocate to")
    explicit = (
        {t.job_id: t.weight for t in data.targets} if data.method == "percent" else None
    )
    try:
        jc = allocate_cost(
            db,
            txn_date=data.date,
            amount=data.amount,
            method=data.method,
            job_ids=job_ids,
            cost_code_id=data.cost_code_id,
            cost_type=data.cost_type,
            debit_account_id=data.debit_account_id,
            credit_account_id=data.credit_account_id,
            memo=data.memo,
            start_date=data.start_date,
            end_date=data.end_date,
            explicit=explicit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    db.commit()
    return _jc_response(_jc_get(db, jc.id))


@job_costs_router.post("/{jc_id}/void", response_model=JobCostResponse)
def void(jc_id: int, db: Session = Depends(get_db)):
    jc = _jc_get(db, jc_id)
    check_closing_date(db, jc.date)
    try:
        void_job_cost(db, jc)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    db.commit()
    return _jc_response(_jc_get(db, jc.id))
