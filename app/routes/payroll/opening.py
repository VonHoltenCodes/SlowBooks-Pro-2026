# ============================================================================
# Opening YTD balances (#433) — entered on the employee page or imported for
# everyone from a CSV. Payroll-only: no journal entry, no pay run.
# ============================================================================

from datetime import date

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.payroll import Employee
from app.models.payroll_opening import PayrollOpeningBalance
from app.routes.payroll._router import router
from app.schemas.payroll import PayrollOpeningInput
from app.services import payroll_opening as po
from app.services.upload_limits import read_limited

# The employee page's paths, served here so there is one implementation.
employee_router = APIRouter(prefix="/api/employees", tags=["payroll"])


def _employee(db: Session, emp_id: int) -> Employee:
    emp = db.query(Employee).filter(Employee.id == emp_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    return emp


def _response(db: Session, emp_id: int, year: int) -> dict:
    opening = po.opening_for(db, emp_id, year)
    first = po.first_processed_pay_date(db, emp_id, year)
    return {
        "employee_id": emp_id,
        "year": year,
        "opening": po.serialize(opening) if opening else None,
        "locked": first is not None,
        "first_pay_date": first.isoformat() if first else None,
        "lock_message": po.lock_message(first) if first else None,
    }


@router.get("/opening-ytd")
def list_opening_ytd(
    year: int = Query(default=None), db: Session = Depends(get_db)
) -> list[dict]:
    """Every employee's opening balance for the year."""
    year = year or date.today().year
    rows = po.openings_for_year(db, year).values()
    return [
        po.serialize(o)
        for o in sorted(
            rows,
            key=lambda o: (
                (o.employee.last_name, o.employee.first_name)
                if o.employee
                else ("", "")
            ),
        )
    ]


@router.get("/opening-ytd/template")
def opening_ytd_template(request: Request):
    """The CSV template the importer reads for sure."""
    from app.routes.csv import _csv_response
    from app.services.payroll_opening_import import opening_template_csv

    return _csv_response(opening_template_csv(), "opening-ytd-template.csv", request)


@router.post("/opening-ytd/import")
async def import_opening_ytd(
    file: UploadFile = File(...),
    dry_run: bool = Query(True),
    as_of: date = Query(default=None),
    reason: str = Query(default=None),
    db: Session = Depends(get_db),
):
    """Import every employee's opening balance from the SlowBooks template
    or a QuickBooks Payroll Summary. `dry_run=1` (the default) returns the
    plan and writes nothing; `dry_run=0` writes the rows that are not
    errors. `as_of` dates a Payroll Summary whose header says no "through"
    date; `reason` lets the import correct a locked employee's balance."""
    from app.routes.csv import _decode_csv_upload
    from app.services.payroll_opening_import import apply_plan, plan_import

    content = _decode_csv_upload(await read_limited(file, label="CSV file"))
    if not content.strip():
        raise HTTPException(status_code=400, detail="The file is empty.")
    try:
        reason = po.check_reason(reason)
    except po.OpeningError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    plan = plan_import(db, content, as_of=as_of, reason=reason)
    applied = 0
    if not dry_run and not plan["errors"]:
        try:
            applied = apply_plan(db, plan, reason)
        except po.OpeningError as e:
            db.rollback()
            raise HTTPException(status_code=e.status_code, detail=e.detail)
    for row in plan["rows"]:
        row.pop("_clean", None)
    plan["dry_run"] = dry_run
    plan["applied"] = applied
    plan["counts"] = {
        a: sum(1 for r in plan["rows"] if r["action"] == a)
        for a in ("create", "replace", "error")
    }
    return plan


@employee_router.get("/{emp_id}/opening-ytd")
def get_opening_ytd(
    emp_id: int, year: int = Query(default=None), db: Session = Depends(get_db)
):
    """The employee's opening balance for the year (null when none), and
    whether it is locked by a processed pay run."""
    _employee(db, emp_id)
    return _response(db, emp_id, year or date.today().year)


@employee_router.put("/{emp_id}/opening-ytd")
def put_opening_ytd(
    emp_id: int, data: PayrollOpeningInput, db: Session = Depends(get_db)
):
    """Create or replace the employee's opening balance for `year`."""
    _employee(db, emp_id)
    payload = data.model_dump(exclude={"reason"})
    old = po.opening_for(db, emp_id, data.year)
    had_benefits = bool(old and old.benefits)
    try:
        row, _action = po.save_opening(db, emp_id, payload, data.reason)
    except po.OpeningError as e:
        db.rollback()
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    db.commit()
    if had_benefits or row.benefits:
        # the annual caps count the opening's benefit amounts
        po.refresh_benefit_ytd(db, data.year)
    return _response(db, emp_id, data.year)


@employee_router.delete("/{emp_id}/opening-ytd")
def delete_opening_ytd(
    emp_id: int,
    year: int = Query(...),
    reason: str = Query(default=None),
    db: Session = Depends(get_db),
):
    """Remove the employee's opening balance for the year; once locked, a
    reason is required."""
    _employee(db, emp_id)
    opening = (
        db.query(PayrollOpeningBalance)
        .filter(
            PayrollOpeningBalance.employee_id == emp_id,
            PayrollOpeningBalance.year == year,
        )
        .first()
    )
    if opening is None:
        raise HTTPException(status_code=404, detail="No opening balance for that year")
    had_benefits = bool(opening.benefits)
    try:
        po.delete_opening(db, opening, reason)
    except po.OpeningError as e:
        db.rollback()
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    db.commit()
    if had_benefits:
        po.refresh_benefit_ytd(db, year)
    return _response(db, emp_id, year)
