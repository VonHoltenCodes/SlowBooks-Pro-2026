# ============================================================================
# Onboarding — new-hire checklist (W-4, I-9, E-Verify, direct deposit, state
# new-hire reporting, policy acknowledgments) and the state new-hire report.
# ============================================================================

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.payroll import Employee
from app.models.hr import OnboardingTask, OnboardingTaskType, OnboardingTaskStatus
from app.schemas.hr import (
    OnboardingTaskCreate,
    OnboardingTaskUpdate,
    OnboardingTaskResponse,
    OnboardingChecklistResponse,
)
from app.services.onboarding import seed_onboarding_tasks, checklist_summary
from app.services.settings_service import get_all_settings
from app.services.new_hire_report import (
    compute_new_hire_report,
    generate_new_hire_report_pdf,
)
from app import config

router = APIRouter(prefix="/api/onboarding", tags=["onboarding"])


def _employer() -> dict:
    return {
        "name": config.COMPANY_NAME,
        "address": config.COMPANY_ADDRESS,
        "ein": config.EMPLOYER_EIN,
        "state": config.EMPLOYER_STATE,
    }


def _checklist(db: Session, emp: Employee) -> OnboardingChecklistResponse:
    tasks = (
        db.query(OnboardingTask)
        .filter(OnboardingTask.employee_id == emp.id)
        .order_by(OnboardingTask.id)
        .all()
    )
    summary = checklist_summary(tasks)
    return OnboardingChecklistResponse(
        employee_id=emp.id,
        employee_name=emp.full_name,
        complete=summary["complete"],
        total=summary["total"],
        percent_complete=summary["percent_complete"],
        tasks=[OnboardingTaskResponse.model_validate(t) for t in tasks],
    )


@router.get("/{emp_id}", response_model=OnboardingChecklistResponse)
def get_checklist(emp_id: int, db: Session = Depends(get_db)):
    """Return an employee's onboarding checklist, seeding it on first access."""
    emp = db.query(Employee).filter(Employee.id == emp_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    if (
        not db.query(OnboardingTask)
        .filter(OnboardingTask.employee_id == emp_id)
        .first()
    ):
        seed_onboarding_tasks(db, emp_id)
        db.commit()
    return _checklist(db, emp)


@router.post("/{emp_id}/seed", response_model=OnboardingChecklistResponse)
def seed_checklist(emp_id: int, db: Session = Depends(get_db)):
    emp = db.query(Employee).filter(Employee.id == emp_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    seed_onboarding_tasks(db, emp_id)
    db.commit()
    return _checklist(db, emp)


@router.post("/tasks", response_model=OnboardingTaskResponse, status_code=201)
def create_task(data: OnboardingTaskCreate, db: Session = Depends(get_db)):
    if not db.query(Employee).filter(Employee.id == data.employee_id).first():
        raise HTTPException(status_code=404, detail="Employee not found")
    try:
        task_type = OnboardingTaskType(data.task_type)
    except ValueError:
        raise HTTPException(
            status_code=400, detail=f"Invalid task_type: {data.task_type}"
        )
    task = OnboardingTask(
        employee_id=data.employee_id, task_type=task_type, notes=data.notes
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


@router.put("/tasks/{task_id}", response_model=OnboardingTaskResponse)
def update_task(
    task_id: int, data: OnboardingTaskUpdate, db: Session = Depends(get_db)
):
    task = db.query(OnboardingTask).filter(OnboardingTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Onboarding task not found")
    fields = data.model_dump(exclude_unset=True)
    now = datetime.now(timezone.utc)

    if "status" in fields:
        try:
            task.status = OnboardingTaskStatus(fields["status"])
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid status")
        task.completed_at = (
            now if task.status == OnboardingTaskStatus.COMPLETE else None
        )
    if "signed" in fields:
        task.signed = bool(fields["signed"])
        task.signed_at = now if task.signed else None
    for key in ("notes", "completed_by", "document_id"):
        if key in fields:
            setattr(task, key, fields[key])

    db.commit()
    db.refresh(task)
    return task


@router.post("/tasks/{task_id}/complete", response_model=OnboardingTaskResponse)
def complete_task(
    task_id: int, completed_by: str = "admin", db: Session = Depends(get_db)
):
    task = db.query(OnboardingTask).filter(OnboardingTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Onboarding task not found")
    task.status = OnboardingTaskStatus.COMPLETE
    task.completed_at = datetime.now(timezone.utc)
    task.completed_by = completed_by
    db.commit()
    db.refresh(task)
    return task


@router.get("/{emp_id}/new-hire-report")
def new_hire_report(emp_id: int, db: Session = Depends(get_db)):
    """State new-hire report data — must be filed within 20 days of hire."""
    try:
        return compute_new_hire_report(db, emp_id, _employer())
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/{emp_id}/new-hire-report/pdf")
def new_hire_report_pdf(emp_id: int, db: Session = Depends(get_db)):
    try:
        pdf = generate_new_hire_report_pdf(
            db, emp_id, _employer(), company_settings=get_all_settings(db)
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f"inline; filename=new_hire_{emp_id}.pdf"},
    )
