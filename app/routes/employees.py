# ============================================================================
# Employees — CRUD, direct-deposit bank accounts, year-to-date totals,
# self-service portal access, and the per-employee HR document vault.
# ============================================================================

import secrets
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Form
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.payroll import Employee
from app.models.attachments import Attachment
from app.models.bank_accounts import (
    EmployeeBankAccount,
    BankAccountKind,
    DepositType,
)
from app.schemas.payroll import (
    EmployeeCreate,
    EmployeeUpdate,
    EmployeeResponse,
    BankAccountCreate,
    BankAccountResponse,
    YTDResponse,
)
from app.schemas.hr import EmployeeDocumentResponse
from app.services.encryption import encrypt
from app.services.onboarding import seed_onboarding_tasks
from app.routes.payroll import employee_ytd
from app.routes.attachments import (
    _sanitize_filename,
    _resolve_within,
    STATIC_BASE,
    UPLOAD_BASE,
    ALLOWED_EXTENSIONS,
    ALLOWED_MIME_TYPES,
)

router = APIRouter(prefix="/api/employees", tags=["employees"])


@router.get("", response_model=list[EmployeeResponse])
def list_employees(active_only: bool = False, db: Session = Depends(get_db)):
    q = db.query(Employee)
    if active_only:
        q = q.filter(Employee.is_active == True)  # noqa: E712
    return q.order_by(Employee.last_name, Employee.first_name).all()


@router.get("/{emp_id}", response_model=EmployeeResponse)
def get_employee(emp_id: int, db: Session = Depends(get_db)):
    emp = db.query(Employee).filter(Employee.id == emp_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    return emp


@router.post("", response_model=EmployeeResponse, status_code=201)
def create_employee(data: EmployeeCreate, db: Session = Depends(get_db)):
    emp = Employee(**data.model_dump())
    # Every new hire gets a self-service portal token and an onboarding checklist.
    emp.portal_token = secrets.token_urlsafe(24)
    db.add(emp)
    db.flush()
    seed_onboarding_tasks(db, emp.id)
    db.commit()
    db.refresh(emp)
    return emp


@router.put("/{emp_id}", response_model=EmployeeResponse)
def update_employee(emp_id: int, data: EmployeeUpdate, db: Session = Depends(get_db)):
    emp = db.query(Employee).filter(Employee.id == emp_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    for key, val in data.model_dump(exclude_unset=True).items():
        setattr(emp, key, val)
    db.commit()
    db.refresh(emp)
    return emp


# --- Self-service portal access -------------------------------------------
@router.get("/{emp_id}/portal-token")
def get_portal_token(emp_id: int, db: Session = Depends(get_db)):
    """Return the employee's self-service portal token, minting one if absent."""
    emp = db.query(Employee).filter(Employee.id == emp_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    if not emp.portal_token:
        emp.portal_token = secrets.token_urlsafe(24)
        db.commit()
    return {
        "employee_id": emp.id,
        "portal_token": emp.portal_token,
        "portal_url": f"/portal/{emp.portal_token}",
    }


@router.post("/{emp_id}/portal-token")
def regenerate_portal_token(emp_id: int, db: Session = Depends(get_db)):
    """Rotate the portal token (invalidates the previous self-service link)."""
    emp = db.query(Employee).filter(Employee.id == emp_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    emp.portal_token = secrets.token_urlsafe(24)
    db.commit()
    return {
        "employee_id": emp.id,
        "portal_token": emp.portal_token,
        "portal_url": f"/portal/{emp.portal_token}",
    }


# --- Year-to-date ----------------------------------------------------------
@router.get("/{emp_id}/ytd", response_model=YTDResponse)
def get_employee_ytd(
    emp_id: int, year: int = Query(default=None), db: Session = Depends(get_db)
):
    """Year-to-date payroll totals for an employee (exposes the Bug 1 fix)."""
    emp = db.query(Employee).filter(Employee.id == emp_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    year = year or date.today().year
    totals = employee_ytd(db, emp_id, year)
    return YTDResponse(
        employee_id=emp_id,
        year=year,
        gross=float(totals["gross"]),
        federal=float(totals["federal"]),
        state=float(totals["state"]),
        state_other=float(totals["state_other"]),
        ss=float(totals["ss"]),
        medicare=float(totals["medicare"]),
        pretax_deductions=float(totals["pretax_deductions"]),
        net=float(totals["net"]),
    )


# --- Direct-deposit bank accounts ------------------------------------------
@router.get("/{emp_id}/bank-accounts", response_model=list[BankAccountResponse])
def list_bank_accounts(emp_id: int, db: Session = Depends(get_db)):
    emp = db.query(Employee).filter(Employee.id == emp_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    return (
        db.query(EmployeeBankAccount)
        .filter(EmployeeBankAccount.employee_id == emp_id)
        .order_by(EmployeeBankAccount.priority)
        .all()
    )


@router.post(
    "/{emp_id}/bank-accounts", response_model=BankAccountResponse, status_code=201
)
def add_bank_account(
    emp_id: int, data: BankAccountCreate, db: Session = Depends(get_db)
):
    emp = db.query(Employee).filter(Employee.id == emp_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    try:
        kind = BankAccountKind(data.account_kind)
        deposit = DepositType(data.deposit_type)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid value: {e}")

    routing = (data.routing_number or "").strip()
    account = (data.account_number or "").strip()
    if not routing.isdigit() or len(routing) != 9:
        raise HTTPException(status_code=400, detail="Routing number must be 9 digits")
    if not account.isdigit():
        raise HTTPException(status_code=400, detail="Account number must be numeric")

    ba = EmployeeBankAccount(
        employee_id=emp_id,
        nickname=data.nickname,
        account_kind=kind,
        routing_number_enc=encrypt(routing),
        account_number_enc=encrypt(account),
        account_last_four=account[-4:],
        deposit_type=deposit,
        deposit_value=data.deposit_value,
        priority=data.priority,
    )
    db.add(ba)
    db.commit()
    db.refresh(ba)
    return ba


@router.delete("/{emp_id}/bank-accounts/{ba_id}")
def remove_bank_account(emp_id: int, ba_id: int, db: Session = Depends(get_db)):
    ba = (
        db.query(EmployeeBankAccount)
        .filter(
            EmployeeBankAccount.id == ba_id, EmployeeBankAccount.employee_id == emp_id
        )
        .first()
    )
    if not ba:
        raise HTTPException(status_code=404, detail="Bank account not found")
    db.delete(ba)
    db.commit()
    return {"status": "deleted", "id": ba_id}


# --- HR document vault -----------------------------------------------------
@router.get("/{emp_id}/documents", response_model=list[EmployeeDocumentResponse])
def list_employee_documents(emp_id: int, db: Session = Depends(get_db)):
    emp = db.query(Employee).filter(Employee.id == emp_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    return (
        db.query(Attachment)
        .filter(Attachment.employee_id == emp_id)
        .order_by(Attachment.uploaded_at.desc())
        .all()
    )


@router.post(
    "/{emp_id}/documents", response_model=EmployeeDocumentResponse, status_code=201
)
async def upload_employee_document(
    emp_id: int,
    doc_category: str = Form(default="general"),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    emp = db.query(Employee).filter(Employee.id == emp_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")

    safe_filename = _sanitize_filename(file.filename or "")
    extension = Path(safe_filename).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400, detail=f"File extension '{extension}' not allowed"
        )
    if file.content_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=400, detail=f"MIME type '{file.content_type}' not allowed"
        )

    content = await file.read()
    if len(content) > 50 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large (max 50MB)")

    # "employee" is a static literal and emp_id is an int — safe path segments.
    upload_dir = _resolve_within(UPLOAD_BASE, "employee", str(emp_id))
    upload_dir.mkdir(parents=True, exist_ok=True)
    file_path = _resolve_within(upload_dir, safe_filename)
    file_path.write_bytes(content)

    doc = Attachment(
        entity_type="employee",
        entity_id=emp_id,
        employee_id=emp_id,
        doc_category=(doc_category or "general")[:50],
        filename=safe_filename,
        file_path=str(file_path.relative_to(STATIC_BASE)),
        mime_type=file.content_type,
        file_size=len(content),
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


@router.get("/{emp_id}/documents/{doc_id}")
def download_employee_document(emp_id: int, doc_id: int, db: Session = Depends(get_db)):
    doc = (
        db.query(Attachment)
        .filter(Attachment.id == doc_id, Attachment.employee_id == emp_id)
        .first()
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    full_path = _resolve_within(STATIC_BASE, doc.file_path)
    if not full_path.exists():
        raise HTTPException(status_code=404, detail="File missing from storage")
    return FileResponse(
        str(full_path),
        filename=doc.filename,
        media_type=doc.mime_type or "application/octet-stream",
    )


@router.delete("/{emp_id}/documents/{doc_id}")
def delete_employee_document(emp_id: int, doc_id: int, db: Session = Depends(get_db)):
    doc = (
        db.query(Attachment)
        .filter(Attachment.id == doc_id, Attachment.employee_id == emp_id)
        .first()
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    db.delete(doc)
    db.commit()
    return {"status": "deleted", "id": doc_id}
