# ============================================================================
# Time entries — daily time tracking with a submit / approve workflow
# Tier 1.4: approved entries feed pay runs (see routes/payroll.py).
# ============================================================================

from datetime import datetime, timezone, date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.payroll import Employee
from app.models.time_entries import TimeEntry, TimeEntryStatus
from app.schemas.time_entries import (
    TimeEntryCreate,
    TimeEntryUpdate,
    TimeEntryResponse,
    TimeEntryApprove,
)
from app.services.overtime import classify_period

router = APIRouter(prefix="/api/time-entries", tags=["time-entries"])


def _resp(entry: TimeEntry) -> TimeEntryResponse:
    r = TimeEntryResponse.model_validate(entry)
    if entry.employee:
        r.employee_name = entry.employee.full_name
    return r


@router.get("", response_model=list[TimeEntryResponse])
def list_time_entries(
    employee_id: int = Query(default=None),
    start: date = Query(default=None),
    end: date = Query(default=None),
    status: str = Query(default=None),
    db: Session = Depends(get_db),
):
    q = db.query(TimeEntry)
    if employee_id:
        q = q.filter(TimeEntry.employee_id == employee_id)
    if start:
        q = q.filter(TimeEntry.date >= start)
    if end:
        q = q.filter(TimeEntry.date <= end)
    if status:
        try:
            q = q.filter(TimeEntry.status == TimeEntryStatus(status))
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid status: {status}")
    return [_resp(e) for e in q.order_by(TimeEntry.date.desc()).all()]


@router.post("", response_model=TimeEntryResponse, status_code=201)
def create_time_entry(data: TimeEntryCreate, db: Session = Depends(get_db)):
    if not db.query(Employee).filter(Employee.id == data.employee_id).first():
        raise HTTPException(status_code=404, detail="Employee not found")
    entry = TimeEntry(**data.model_dump())
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return _resp(entry)


@router.put("/{entry_id}", response_model=TimeEntryResponse)
def update_time_entry(
    entry_id: int, data: TimeEntryUpdate, db: Session = Depends(get_db)
):
    entry = db.query(TimeEntry).filter(TimeEntry.id == entry_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Time entry not found")
    if entry.pay_run_id is not None:
        raise HTTPException(
            status_code=400,
            detail="Time entry is locked to a pay run and cannot be edited",
        )
    fields = data.model_dump(exclude_unset=True)
    if "status" in fields:
        try:
            fields["status"] = TimeEntryStatus(fields["status"])
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid status")
    for key, val in fields.items():
        setattr(entry, key, val)
    db.commit()
    db.refresh(entry)
    return _resp(entry)


@router.delete("/{entry_id}")
def delete_time_entry(entry_id: int, db: Session = Depends(get_db)):
    entry = db.query(TimeEntry).filter(TimeEntry.id == entry_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Time entry not found")
    if entry.pay_run_id is not None:
        raise HTTPException(
            status_code=400,
            detail="Time entry is locked to a pay run and cannot be deleted",
        )
    db.delete(entry)
    db.commit()
    return {"status": "deleted", "id": entry_id}


@router.post("/{entry_id}/submit", response_model=TimeEntryResponse)
def submit_time_entry(entry_id: int, db: Session = Depends(get_db)):
    entry = db.query(TimeEntry).filter(TimeEntry.id == entry_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Time entry not found")
    entry.status = TimeEntryStatus.SUBMITTED
    db.commit()
    db.refresh(entry)
    return _resp(entry)


@router.post("/{entry_id}/approve", response_model=TimeEntryResponse)
def approve_time_entry(
    entry_id: int, data: TimeEntryApprove, db: Session = Depends(get_db)
):
    entry = db.query(TimeEntry).filter(TimeEntry.id == entry_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Time entry not found")
    entry.status = TimeEntryStatus.APPROVED
    entry.approved_by = data.approved_by
    entry.approved_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(entry)
    return _resp(entry)


@router.post("/{entry_id}/reject", response_model=TimeEntryResponse)
def reject_time_entry(entry_id: int, db: Session = Depends(get_db)):
    entry = db.query(TimeEntry).filter(TimeEntry.id == entry_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Time entry not found")
    entry.status = TimeEntryStatus.REJECTED
    db.commit()
    db.refresh(entry)
    return _resp(entry)


class ClassifyRequest(BaseModel):
    weeks: list[list[float]]  # each inner list = daily hours for one workweek
    state: str = "WA"


@router.post("/classify")
def classify_hours(data: ClassifyRequest):
    """Run the overtime engine over raw daily hours (FLSA + state overrides)."""
    weeks = [[Decimal(str(h)) for h in week] for week in data.weeks]
    result = classify_period(weeks, data.state)
    return {k: float(v) for k, v in result.items()}


@router.get("/summary")
def pay_period_summary(
    period_start: date = Query(...),
    period_end: date = Query(...),
    db: Session = Depends(get_db),
):
    """Hours-by-employee summary for an upcoming pay period.

    Only approved entries that haven't been swept into a pay run yet
    (pay_run_id IS NULL) are included — same filter the pay-run create
    flow applies when `use_time_entries=true`. The SPA uses this to
    pre-fill / preview the "Calculate Payroll" form.
    """
    if period_end < period_start:
        raise HTTPException(status_code=400, detail="period_end before period_start")

    rows = (
        db.query(TimeEntry)
        .filter(
            TimeEntry.status == TimeEntryStatus.APPROVED,
            TimeEntry.pay_run_id.is_(None),
            TimeEntry.date >= period_start,
            TimeEntry.date <= period_end,
        )
        .all()
    )

    by_emp: dict[int, dict] = {}
    for te in rows:
        bucket = by_emp.setdefault(
            te.employee_id,
            {
                "employee_id": te.employee_id,
                "employee_name": te.employee.full_name if te.employee else None,
                "regular": Decimal("0"),
                "overtime": Decimal("0"),
                "doubletime": Decimal("0"),
                "entry_count": 0,
            },
        )
        bucket["regular"] += Decimal(str(te.hours_regular or 0))
        bucket["overtime"] += Decimal(str(te.hours_overtime or 0))
        bucket["doubletime"] += Decimal(str(te.hours_doubletime or 0))
        bucket["entry_count"] += 1

    return [
        {
            **b,
            "regular": float(b["regular"]),
            "overtime": float(b["overtime"]),
            "doubletime": float(b["doubletime"]),
            "total": float(b["regular"] + b["overtime"] + b["doubletime"]),
        }
        for b in by_emp.values()
    ]
