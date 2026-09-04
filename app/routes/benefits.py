# ============================================================================
# Benefits engine — codes (with dated rates), employee groups, assignments,
# YTD accumulators, remittance. See app/services/benefits_engine.py.
# ============================================================================

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models.benefits import (
    BenefitCode,
    BenefitYTD,
    EmployeeBenefit,
    EmployeeGroup,
    EmployeeGroupBenefit,
)
from app.models.payroll import Employee
from app.schemas.benefits import (
    BenefitCodeCreate,
    BenefitCodeResponse,
    BenefitCodeUpdate,
    BenefitRateIn,
    BenefitRateResponse,
    BenefitYTDResponse,
    EmployeeBenefitCreate,
    EmployeeBenefitResponse,
    EmployeeBenefitUpdate,
    EmployeeGroupCreate,
    EmployeeGroupResponse,
    EmployeeGroupUpdate,
    GroupCodeIn,
    GroupMembersIn,
    RemittanceBillIn,
    ResolvedBenefit,
)
from app.services import benefits_engine as engine

router = APIRouter(prefix="/api/benefits", tags=["benefits"])


def _code_response(code: BenefitCode, as_of: date = None) -> BenefitCodeResponse:
    resp = BenefitCodeResponse.model_validate(code)
    cur = engine.resolve_rate(code, as_of or date.today())
    resp.current_rate = BenefitRateResponse.model_validate(cur) if cur else None
    return resp


def _get_code(db: Session, code_id: int) -> BenefitCode:
    code = (
        db.query(BenefitCode)
        .options(joinedload(BenefitCode.rates))
        .filter(BenefitCode.id == code_id)
        .first()
    )
    if not code:
        raise HTTPException(status_code=404, detail="Benefit code not found")
    return code


# --- Codes -----------------------------------------------------------------
@router.get("/codes", response_model=list[BenefitCodeResponse])
def list_codes(
    include_inactive: bool = Query(default=False), db: Session = Depends(get_db)
):
    q = db.query(BenefitCode).options(joinedload(BenefitCode.rates))
    if not include_inactive:
        q = q.filter(BenefitCode.is_active)
    return [
        _code_response(c) for c in q.order_by(BenefitCode.sequence, BenefitCode.code)
    ]


@router.post("/codes", response_model=BenefitCodeResponse, status_code=201)
def create_code(data: BenefitCodeCreate, db: Session = Depends(get_db)):
    code_str = data.code.strip().upper()
    if db.query(BenefitCode).filter(BenefitCode.code == code_str).first():
        raise HTTPException(status_code=409, detail=f"Code {code_str} already exists")
    payload = data.model_dump(exclude={"rate"})
    payload["code"] = code_str
    code = BenefitCode(**payload)
    db.add(code)
    db.flush()
    rate = data.rate or BenefitRateIn()
    engine.add_rate(db, code, rate.model_dump())
    db.commit()
    return _code_response(_get_code(db, code.id))


@router.post("/codes/seed-standard", response_model=list[BenefitCodeResponse])
def seed_standard(db: Session = Depends(get_db)):
    """Create the common codes (Section 125, HSA, 401(k) with a tiered
    match, Roth, union dues, loan, employer health, GTL) if missing."""
    engine.ensure_accounts(db)
    codes = engine.seed_standard_codes(db)
    db.commit()
    return [_code_response(_get_code(db, c.id)) for c in codes]


@router.post("/setup-accounts")
def setup_accounts(db: Session = Depends(get_db)):
    """Create the benefit / PTO accounts (2380, 2390, 6150, 6160) a company
    file from before the engine doesn't have."""
    created = engine.ensure_accounts(db)
    db.commit()
    return {"created": created}


@router.get("/codes/{code_id}", response_model=BenefitCodeResponse)
def get_code(code_id: int, db: Session = Depends(get_db)):
    return _code_response(_get_code(db, code_id))


@router.put("/codes/{code_id}", response_model=BenefitCodeResponse)
def update_code(code_id: int, data: BenefitCodeUpdate, db: Session = Depends(get_db)):
    code = _get_code(db, code_id)
    changes = data.model_dump(exclude_unset=True)
    if "code" in changes and changes["code"]:
        new = changes["code"].strip().upper()
        dup = (
            db.query(BenefitCode)
            .filter(BenefitCode.code == new, BenefitCode.id != code.id)
            .first()
        )
        if dup:
            raise HTTPException(status_code=409, detail=f"Code {new} already exists")
        changes["code"] = new
    for k, v in changes.items():
        setattr(code, k, v)
    db.commit()
    return _code_response(_get_code(db, code.id))


@router.delete("/codes/{code_id}")
def deactivate_code(code_id: int, db: Session = Depends(get_db)):
    """Codes are never deleted — posted stubs snapshot them — only retired."""
    code = _get_code(db, code_id)
    code.is_active = False
    db.commit()
    return {"status": "deactivated", "id": code_id}


@router.get("/codes/{code_id}/rates", response_model=list[BenefitRateResponse])
def list_rates(code_id: int, db: Session = Depends(get_db)):
    return _get_code(db, code_id).rates


@router.post(
    "/codes/{code_id}/rates", response_model=BenefitRateResponse, status_code=201
)
def add_rate(code_id: int, data: BenefitRateIn, db: Session = Depends(get_db)):
    """A new dated rate. The previous open row closes the day before, so
    runs already posted keep resolving to the rate they used."""
    code = _get_code(db, code_id)
    if data.effective_from is None:
        raise HTTPException(status_code=400, detail="effective_from is required")
    for r in code.rates:
        if r.effective_from == data.effective_from:
            raise HTTPException(
                status_code=409,
                detail=f"A rate already starts on {data.effective_from}",
            )
    row = engine.add_rate(db, code, data.model_dump())
    db.commit()
    db.refresh(row)
    return row


@router.delete("/codes/{code_id}/rates/{rate_id}")
def delete_rate(code_id: int, rate_id: int, db: Session = Depends(get_db)):
    code = _get_code(db, code_id)
    row = next((r for r in code.rates if r.id == rate_id), None)
    if not row:
        raise HTTPException(status_code=404, detail="Rate not found")
    if len(code.rates) == 1:
        raise HTTPException(
            status_code=400, detail="A code needs at least one dated rate"
        )
    db.delete(row)
    db.flush()
    # reopen the row that preceded it
    remaining = sorted(
        (r for r in code.rates if r.id != rate_id), key=lambda r: r.effective_from
    )
    if remaining:
        remaining[-1].effective_to = None
    db.commit()
    return {"status": "deleted", "id": rate_id}


# --- Groups ----------------------------------------------------------------
def _group_response(db: Session, g: EmployeeGroup) -> EmployeeGroupResponse:
    resp = EmployeeGroupResponse.model_validate(g)
    for gc_resp, gc in zip(resp.codes, g.codes):
        if gc.benefit_code:
            gc_resp.code = gc.benefit_code.code
            gc_resp.name = gc.benefit_code.name
    resp.member_count = (
        db.query(Employee).filter(Employee.employee_group_id == g.id).count()
    )
    return resp


def _get_group(db: Session, group_id: int) -> EmployeeGroup:
    g = (
        db.query(EmployeeGroup)
        .options(
            joinedload(EmployeeGroup.codes).joinedload(
                EmployeeGroupBenefit.benefit_code
            )
        )
        .filter(EmployeeGroup.id == group_id)
        .first()
    )
    if not g:
        raise HTTPException(status_code=404, detail="Employee group not found")
    return g


@router.get("/groups", response_model=list[EmployeeGroupResponse])
def list_groups(db: Session = Depends(get_db)):
    groups = (
        db.query(EmployeeGroup)
        .options(
            joinedload(EmployeeGroup.codes).joinedload(
                EmployeeGroupBenefit.benefit_code
            )
        )
        .order_by(EmployeeGroup.name)
        .all()
    )
    return [_group_response(db, g) for g in groups]


@router.post("/groups", response_model=EmployeeGroupResponse, status_code=201)
def create_group(data: EmployeeGroupCreate, db: Session = Depends(get_db)):
    if db.query(EmployeeGroup).filter(EmployeeGroup.name == data.name).first():
        raise HTTPException(status_code=409, detail="Group name already exists")
    g = EmployeeGroup(name=data.name, description=data.description)
    db.add(g)
    db.flush()
    _set_group_codes(db, g, data.codes)
    db.commit()
    return _group_response(db, _get_group(db, g.id))


def _set_group_codes(db: Session, g: EmployeeGroup, codes: list[GroupCodeIn]):
    seen = set()
    for c in codes:
        if c.benefit_code_id in seen:
            continue
        seen.add(c.benefit_code_id)
        if (
            not db.query(BenefitCode)
            .filter(BenefitCode.id == c.benefit_code_id)
            .first()
        ):
            raise HTTPException(
                status_code=404, detail=f"Benefit code {c.benefit_code_id} not found"
            )
    db.query(EmployeeGroupBenefit).filter(
        EmployeeGroupBenefit.group_id == g.id
    ).delete()
    for c in codes:
        db.add(EmployeeGroupBenefit(group_id=g.id, **c.model_dump()))
    db.flush()


@router.put("/groups/{group_id}", response_model=EmployeeGroupResponse)
def update_group(
    group_id: int, data: EmployeeGroupUpdate, db: Session = Depends(get_db)
):
    g = _get_group(db, group_id)
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(g, k, v)
    db.commit()
    return _group_response(db, _get_group(db, g.id))


@router.put("/groups/{group_id}/codes", response_model=EmployeeGroupResponse)
def set_group_codes(
    group_id: int, codes: list[GroupCodeIn], db: Session = Depends(get_db)
):
    g = _get_group(db, group_id)
    _set_group_codes(db, g, codes)
    db.commit()
    return _group_response(db, _get_group(db, g.id))


@router.put("/groups/{group_id}/members", response_model=EmployeeGroupResponse)
def set_group_members(
    group_id: int, data: GroupMembersIn, db: Session = Depends(get_db)
):
    """Replace the group's membership: listed employees join, everyone else
    currently in the group leaves."""
    g = _get_group(db, group_id)
    wanted = set(data.employee_ids)
    for emp in db.query(Employee).filter(Employee.employee_group_id == g.id).all():
        if emp.id not in wanted:
            emp.employee_group_id = None
    for emp_id in wanted:
        emp = db.query(Employee).filter(Employee.id == emp_id).first()
        if not emp:
            raise HTTPException(status_code=404, detail=f"Employee {emp_id} not found")
        emp.employee_group_id = g.id
    db.commit()
    return _group_response(db, _get_group(db, g.id))


@router.delete("/groups/{group_id}")
def delete_group(group_id: int, db: Session = Depends(get_db)):
    g = _get_group(db, group_id)
    for emp in db.query(Employee).filter(Employee.employee_group_id == g.id).all():
        emp.employee_group_id = None
    db.delete(g)
    db.commit()
    return {"status": "deleted", "id": group_id}


# --- Assignments -----------------------------------------------------------
def _assignment_response(a: EmployeeBenefit) -> EmployeeBenefitResponse:
    resp = EmployeeBenefitResponse.model_validate(a)
    if a.benefit_code:
        resp.code = a.benefit_code.code
        resp.name = a.benefit_code.name
    return resp


@router.get("/enrollments", response_model=list[EmployeeBenefitResponse])
def list_enrollments(
    employee_id: int = Query(default=None),
    include_inactive: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    q = db.query(EmployeeBenefit).options(joinedload(EmployeeBenefit.benefit_code))
    if employee_id:
        q = q.filter(EmployeeBenefit.employee_id == employee_id)
    if not include_inactive:
        q = q.filter(EmployeeBenefit.is_active)
    return [_assignment_response(a) for a in q.order_by(EmployeeBenefit.id).all()]


@router.post("/enrollments", response_model=EmployeeBenefitResponse, status_code=201)
def create_enrollment(data: EmployeeBenefitCreate, db: Session = Depends(get_db)):
    if not db.query(Employee).filter(Employee.id == data.employee_id).first():
        raise HTTPException(status_code=404, detail="Employee not found")
    code = db.query(BenefitCode).filter(BenefitCode.id == data.benefit_code_id).first()
    if not code:
        raise HTTPException(status_code=404, detail="Benefit code not found")
    dup = (
        db.query(EmployeeBenefit)
        .filter(
            EmployeeBenefit.employee_id == data.employee_id,
            EmployeeBenefit.benefit_code_id == data.benefit_code_id,
            EmployeeBenefit.is_active,
        )
        .first()
    )
    if dup:
        raise HTTPException(
            status_code=409,
            detail=f"Employee already has an active {code.code} assignment (#{dup.id})",
        )
    payload = data.model_dump()
    if code.tracks_balance and payload.get("balance_remaining") is None:
        raise HTTPException(
            status_code=400,
            detail=f"{code.code} tracks a balance — give the starting balance",
        )
    a = EmployeeBenefit(**payload)
    db.add(a)
    db.commit()
    db.refresh(a)
    return _assignment_response(a)


@router.put("/enrollments/{enrollment_id}", response_model=EmployeeBenefitResponse)
def update_enrollment(
    enrollment_id: int, data: EmployeeBenefitUpdate, db: Session = Depends(get_db)
):
    a = db.query(EmployeeBenefit).filter(EmployeeBenefit.id == enrollment_id).first()
    if not a:
        raise HTTPException(status_code=404, detail="Enrollment not found")
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(a, k, v)
    db.commit()
    db.refresh(a)
    return _assignment_response(a)


@router.delete("/enrollments/{enrollment_id}")
def end_enrollment(
    enrollment_id: int,
    end_date: date = Query(default=None),
    db: Session = Depends(get_db),
):
    """End an assignment. With an end_date it stays on file (history);
    without one it's removed outright."""
    a = db.query(EmployeeBenefit).filter(EmployeeBenefit.id == enrollment_id).first()
    if not a:
        raise HTTPException(status_code=404, detail="Enrollment not found")
    if end_date:
        a.end_date = end_date
        a.is_active = False
        db.commit()
        return {"status": "ended", "id": enrollment_id, "end_date": end_date}
    db.delete(a)
    db.commit()
    return {"status": "deleted", "id": enrollment_id}


# --- Resolved view for one employee ------------------------------------------
@router.get("/employee/{emp_id}/resolved", response_model=list[ResolvedBenefit])
def resolved_for_employee(
    emp_id: int,
    as_of: date = Query(default=None),
    db: Session = Depends(get_db),
):
    """What the next pay run would apply: assignments, then the group's
    codes, each at the rate in force on `as_of` (default today)."""
    emp = db.query(Employee).filter(Employee.id == emp_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    as_of = as_of or date.today()
    out = []
    for r in engine.resolve_for_employee(db, emp, as_of, as_of):
        ytd = engine.get_ytd(db, emp.id, r.code.id, as_of.year)
        out.append(
            ResolvedBenefit(
                benefit_code_id=r.code.id,
                code=r.code.code,
                name=r.code.name,
                kind=r.code.kind,
                category=r.code.category,
                sequence=r.code.sequence or 100,
                calc_method=r.code.calc_method,
                employer_calc_method=r.code.employer_calc_method,
                employee_rate=float(r.employee_rate),
                employer_rate=float(r.employer_rate),
                per_period_cap=(
                    float(r.per_period_cap) if r.per_period_cap is not None else None
                ),
                annual_cap=float(r.annual_cap) if r.annual_cap is not None else None,
                wage_base_ceiling=(
                    float(r.wage_base_ceiling)
                    if r.wage_base_ceiling is not None
                    else None
                ),
                source=r.source,
                balance_remaining=(
                    float(r.assignment.balance_remaining)
                    if r.assignment is not None
                    and r.assignment.balance_remaining is not None
                    else None
                ),
                ytd_employee=float(ytd.employee_amount) if ytd else 0.0,
                ytd_employer=float(ytd.employer_amount) if ytd else 0.0,
            )
        )
    return out


# --- YTD -------------------------------------------------------------------
@router.get("/ytd", response_model=list[BenefitYTDResponse])
def list_ytd(
    employee_id: int = Query(default=None),
    year: int = Query(default=None),
    db: Session = Depends(get_db),
):
    q = db.query(BenefitYTD).options(joinedload(BenefitYTD.benefit_code))
    if employee_id:
        q = q.filter(BenefitYTD.employee_id == employee_id)
    q = q.filter(BenefitYTD.year == (year or date.today().year))
    out = []
    for row in q.all():
        resp = BenefitYTDResponse.model_validate(row)
        if row.benefit_code:
            resp.code = row.benefit_code.code
            resp.name = row.benefit_code.name
        out.append(resp)
    return out


@router.post("/ytd/rebuild")
def rebuild_ytd(year: int = Query(...), db: Session = Depends(get_db)):
    """Repair: recompute the year's accumulators from the stub snapshots."""
    n = engine.rebuild_ytd(db, year)
    db.commit()
    return {"year": year, "rows": n}


# --- Remittance ------------------------------------------------------------
@router.get("/remittance")
def remittance(
    start_date: date = Query(...),
    end_date: date = Query(...),
    db: Session = Depends(get_db),
):
    if end_date < start_date:
        raise HTTPException(status_code=400, detail="end_date is before start_date")
    rows = engine.remittance_rows(db, start_date, end_date)
    return {
        "start_date": start_date,
        "end_date": end_date,
        "rows": rows,
        "total_employee": float(sum(Decimal(str(r["employee_amount"])) for r in rows)),
        "total_employer": float(sum(Decimal(str(r["employer_amount"])) for r in rows)),
    }


@router.post("/remittance/bill", status_code=201)
def remittance_bill(data: RemittanceBillIn, db: Session = Depends(get_db)):
    try:
        bill = engine.create_remittance_bill(
            db,
            data.vendor_id,
            data.start_date,
            data.end_date,
            data.bill_date,
            data.bill_number,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {
        "bill_id": bill.id,
        "bill_number": bill.bill_number,
        "total": float(bill.total),
    }
