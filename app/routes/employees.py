# ============================================================================
# Employees — CRUD for employee records
# Feature 17: Payroll basics — employee management
# ============================================================================

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.payroll import Employee
from app.schemas.payroll import EmployeeCreate, EmployeeUpdate, EmployeeResponse
from app.schemas.payroll_filing import PayrollFilingAuditResponse, PayrollFilingAuditStatusUpdate
from app.routes.settings import _get_all as get_settings
from app.services.auth import require_permissions
from app.services.employee_filing import generate_employee_filing_csv
from app.services.payroll_filing_audit import (
    create_filing_audit,
    employee_filing_snapshot,
    list_employee_filing_history,
    update_filing_audit_status,
)
from app.models.payroll_filing import PayrollFilingAudit

router = APIRouter(prefix="/api/employees", tags=["employees"])


@router.get("", response_model=list[EmployeeResponse])
def list_employees(
    active_only: bool = False,
    db: Session = Depends(get_db),
    auth=Depends(require_permissions("employees.view_private")),
):
    q = db.query(Employee)
    if active_only:
        q = q.filter(Employee.is_active == True)
    return q.order_by(Employee.last_name, Employee.first_name).all()


@router.get("/{emp_id}", response_model=EmployeeResponse)
def get_employee(
    emp_id: int,
    db: Session = Depends(get_db),
    auth=Depends(require_permissions("employees.view_private")),
):
    emp = db.query(Employee).filter(Employee.id == emp_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    return emp


@router.post("", response_model=EmployeeResponse, status_code=201)
def create_employee(
    data: EmployeeCreate,
    db: Session = Depends(get_db),
    auth=Depends(require_permissions("employees.manage")),
):
    emp = Employee(**data.model_dump())
    db.add(emp)
    db.commit()
    db.refresh(emp)
    return emp


@router.put("/{emp_id}", response_model=EmployeeResponse)
def update_employee(
    emp_id: int,
    data: EmployeeUpdate,
    db: Session = Depends(get_db),
    auth=Depends(require_permissions("employees.manage")),
):
    emp = db.query(Employee).filter(Employee.id == emp_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    for key, val in data.model_dump(exclude_unset=True).items():
        setattr(emp, key, val)
    db.commit()
    db.refresh(emp)
    return emp


@router.get("/{emp_id}/filing/starter/export")
def export_starter_employee_filing(
    emp_id: int,
    db: Session = Depends(get_db),
    auth=Depends(require_permissions("employees.filing.export")),
):
    emp = db.query(Employee).filter(Employee.id == emp_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    try:
        settings = get_settings(db)
        content = generate_employee_filing_csv(emp, "starter", settings)
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    filename = f"StarterEmployee_{emp_id}.csv"
    user = getattr(auth, "user", None)
    create_filing_audit(
        db,
        filing_type="starter",
        employee_id=emp.id,
        export_filename=filename,
        source_snapshot=employee_filing_snapshot(emp, "starter", settings),
        generated_by_user_id=getattr(user, "id", None),
    )
    db.commit()
    return Response(
        content=content,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{emp_id}/filing/leaver/export")
def export_leaver_employee_filing(
    emp_id: int,
    db: Session = Depends(get_db),
    auth=Depends(require_permissions("employees.filing.export")),
):
    emp = db.query(Employee).filter(Employee.id == emp_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    try:
        settings = get_settings(db)
        content = generate_employee_filing_csv(emp, "leaver", settings)
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    filename = f"LeaverEmployee_{emp_id}.csv"
    user = getattr(auth, "user", None)
    create_filing_audit(
        db,
        filing_type="leaver",
        employee_id=emp.id,
        export_filename=filename,
        source_snapshot=employee_filing_snapshot(emp, "leaver", settings),
        generated_by_user_id=getattr(user, "id", None),
    )
    db.commit()
    return Response(
        content=content,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{emp_id}/filing/history", response_model=list[PayrollFilingAuditResponse])
def get_employee_filing_history(
    emp_id: int,
    db: Session = Depends(get_db),
    auth=Depends(require_permissions("employees.view_private")),
):
    emp = db.query(Employee).filter(Employee.id == emp_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    return list_employee_filing_history(db, emp_id, get_settings(db))


@router.post("/{emp_id}/filing/{audit_id}/status", response_model=PayrollFilingAuditResponse)
def update_employee_filing_record(
    emp_id: int,
    audit_id: int,
    data: PayrollFilingAuditStatusUpdate,
    db: Session = Depends(get_db),
    auth=Depends(require_permissions("employees.filing.export")),
):
    record = db.query(PayrollFilingAudit).filter(
        PayrollFilingAudit.id == audit_id,
        PayrollFilingAudit.employee_id == emp_id,
    ).first()
    if not record:
        raise HTTPException(status_code=404, detail="Employee filing record not found")
    user = getattr(auth, "user", None)
    return update_filing_audit_status(
        db,
        record=record,
        status=data.status,
        reference=data.reference,
        notes=data.notes,
        user_id=getattr(user, "id", None),
        settings=get_settings(db),
    )
