# ============================================================================
# Employees — CRUD, direct-deposit bank accounts, year-to-date totals,
# self-service portal access, and the per-employee HR document vault.
# ============================================================================

import re
import secrets
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    UploadFile,
    File,
    Form,
)
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.attachments import Attachment
from app.models.bank_accounts import (
    EmployeeBankAccount,
    BankAccountKind,
    DepositType,
)
from app.models.payroll import Employee
from app.routes._roles import is_admin
from app.routes.attachments import (
    _sanitize_filename,
    _resolve_within,
    STATIC_BASE,
    UPLOAD_BASE,
    ALLOWED_EXTENSIONS,
    ALLOWED_MIME_TYPES,
)
from app.routes.payroll import employee_ytd
from app.schemas.hr import EmployeeDocumentResponse
from app.schemas.payroll import (
    EmployeeCreate,
    EmployeeUpdate,
    EmployeeResponse,
    BankAccountCreate,
    BankAccountResponse,
    YTDResponse,
)
from app.services.encryption import encrypt
from app.services.nacha_export import validate_routing_number
from app.services.onboarding import seed_onboarding_tasks
from app.services.state_tax import is_supported as state_is_supported
from app.services.upload_limits import read_limited

# Portal tokens get a 1-year hard expiry on top of the 90-day idle window
# enforced in app/routes/portal.py. Hard expiry forces periodic re-issuance
# even for an employee who logs in regularly.
_PORTAL_TOKEN_LIFETIME = timedelta(days=365)


def _mint_portal_token(emp: Employee) -> None:
    """Assign a fresh portal token plus its idle and hard expiry timestamps."""
    now = datetime.now(timezone.utc)
    emp.portal_token = secrets.token_urlsafe(24)
    emp.portal_token_last_used = now
    emp.portal_token_expires_at = now + _PORTAL_TOKEN_LIFETIME


def _iso_utc(dt: datetime | None) -> str | None:
    """Serialize a (possibly naive) timestamp as an ISO-8601 UTC string. SQLite
    drops tzinfo on round-trip; we restore it so API consumers don't have to
    guess what timezone the stored datetime is in."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


router = APIRouter(prefix="/api/employees", tags=["employees"])


# What a non-admin sees of a staff record: the directory entry. Time
# entries and job costing need names, activity and the work state; pay,
# tax elections, home address and the SSN tail are HR's (GHSA-pwj7-6qq3-h4fj).
_EMPLOYEE_PRIVATE_DEFAULTS = {
    "ssn_last_four": None,
    "pay_rate": 0,
    "cost_rate": None,
    "burden_pct": None,
    "filing_status": "",
    "multiple_jobs": False,
    "dependents_amount": 0,
    "other_income_annual": 0,
    "deductions_annual": 0,
    "extra_withholding": 0,
    "address1": None,
    "address2": None,
    "city": None,
    "state": None,
    "zip": None,
    "residence_state": None,
    "wc_class_code": None,
    "state_allowances": 0,
    "state_extra_withholding": 0,
    "state_rate_override": None,
    "local_tax_rate": None,
}


def _employee_view(emp: Employee, request: Request) -> dict:
    out = EmployeeResponse.model_validate(emp).model_dump()
    if not is_admin(request):
        out.update(_EMPLOYEE_PRIVATE_DEFAULTS)
    return out


@router.get("", response_model=list[EmployeeResponse])
def list_employees(
    request: Request, active_only: bool = False, db: Session = Depends(get_db)
):
    q = db.query(Employee)
    if active_only:
        q = q.filter(Employee.is_active == True)  # noqa: E712
    rows = q.order_by(Employee.last_name, Employee.first_name).all()
    return [_employee_view(e, request) for e in rows]


@router.get("/{emp_id}", response_model=EmployeeResponse)
def get_employee(request: Request, emp_id: int, db: Session = Depends(get_db)):
    emp = db.query(Employee).filter(Employee.id == emp_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    return _employee_view(emp, request)


# US territories: real work locations with no engine of their own (no state
# income tax is withheld there), unlike a typo.
_TERRITORIES = {"PR", "GU", "VI", "AS", "MP"}


def _clean_employee_fields(fields: dict) -> dict:
    """Refuse, in words, the values payroll cannot use, and tidy the rest.

    SSN last 4 "abcd", a pay rate of -5 and a work state of "Illinois" were
    all saved (2.17.3, skytech W-L4). The work state is how payroll picks
    the withholding engine; a name instead of a code matched none and no
    state income tax was withheld. Blanks from the form mean "none"."""
    if "ssn_last_four" in fields:
        ssn = (fields["ssn_last_four"] or "").strip()
        if ssn and not re.fullmatch(r"\d{4}", ssn):
            raise HTTPException(
                status_code=400,
                detail=(
                    "SSN last 4 must be four digits: the last four of the "
                    "employee's Social Security number, like 1234."
                ),
            )
        fields["ssn_last_four"] = ssn or None
    if fields.get("pay_rate") is not None and fields["pay_rate"] < 0:
        raise HTTPException(
            status_code=400,
            detail=(
                "Pay rate can't be negative. Enter the hourly rate, or the "
                "yearly salary for a salaried employee."
            ),
        )
    for key, label in (
        ("work_state", "Work state"),
        ("residence_state", "Residence state"),
    ):
        if key not in fields:
            continue
        code = (fields[key] or "").strip().upper()
        if code and not (state_is_supported(code) or code in _TERRITORIES):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{label} must be a two-letter state code, like IL. "
                    f'"{fields[key]}" is not one.'
                ),
            )
        fields[key] = code or None
    return fields


@router.post("", response_model=EmployeeResponse, status_code=201)
def create_employee(data: EmployeeCreate, db: Session = Depends(get_db)):
    emp = Employee(**_clean_employee_fields(data.model_dump()))
    # Every new hire gets a self-service portal token and an onboarding checklist.
    _mint_portal_token(emp)
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
    for key, val in _clean_employee_fields(data.model_dump(exclude_unset=True)).items():
        setattr(emp, key, val)
    db.commit()
    db.refresh(emp)
    return emp


# --- Self-service portal access -------------------------------------------
def _portal_url(request: Request, token: str) -> str:
    """Absolute portal link. The employee opens this in THEIR browser (or
    their phone), so it must carry the host the server is reachable on —
    127.0.0.1 on a desktop install, the LAN address on Server Edition, the
    public name behind a reverse proxy (X-Forwarded-* is honoured through
    request.base_url when the proxy is trusted)."""
    return f"{str(request.base_url).rstrip('/')}/portal/{token}"


@router.get("/{emp_id}/portal-token")
def get_portal_token(request: Request, emp_id: int, db: Session = Depends(get_db)):
    """Return the employee's self-service portal token, minting one if absent."""
    emp = db.query(Employee).filter(Employee.id == emp_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    if not emp.portal_token:
        _mint_portal_token(emp)
        db.commit()
    return {
        "employee_id": emp.id,
        "portal_token": emp.portal_token,
        "portal_url": _portal_url(request, emp.portal_token),
        "expires_at": _iso_utc(emp.portal_token_expires_at),
        "last_used_at": _iso_utc(emp.portal_token_last_used),
    }


@router.get("/{emp_id}/everify")
def get_everify(emp_id: int, db: Session = Depends(get_db)):
    """Return the employee's E-Verify case record."""
    emp = db.query(Employee).filter(Employee.id == emp_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    return {
        "employee_id": emp.id,
        "case_number": emp.everify_case_number,
        "status": emp.everify_status or "not_submitted",
        "submitted_at": _iso_utc(emp.everify_submitted_at),
        "closed_at": _iso_utc(emp.everify_closed_at),
        "notes": emp.everify_notes,
    }


_EVERIFY_STATUSES = {
    "not_submitted",
    "pending",
    "photo_match_required",
    "tnc",
    "employment_authorized",
    "final_non_confirmation",
    "case_closed",
}


@router.put("/{emp_id}/everify")
def update_everify(
    emp_id: int,
    data: dict,
    db: Session = Depends(get_db),
):
    """Record / update an E-Verify case for the employee.

    Pure record-keeping — the actual case is submitted through the
    federal E-Verify portal (or a vendor like Equifax). This endpoint
    just stores what the operator entered there alongside the employee
    so DHS inspections find it in one place.
    """
    emp = db.query(Employee).filter(Employee.id == emp_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")

    status = (data.get("status") or "").strip().lower() or None
    if status and status not in _EVERIFY_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"status must be one of {sorted(_EVERIFY_STATUSES)}",
        )

    if "case_number" in data:
        emp.everify_case_number = (data.get("case_number") or "").strip() or None
    if status is not None:
        # Auto-stamp lifecycle timestamps the first time we see each one.
        if status != "not_submitted" and emp.everify_submitted_at is None:
            emp.everify_submitted_at = datetime.now(timezone.utc)
        if status in ("employment_authorized", "final_non_confirmation", "case_closed"):
            if emp.everify_closed_at is None:
                emp.everify_closed_at = datetime.now(timezone.utc)
        emp.everify_status = status
    if "notes" in data:
        emp.everify_notes = (data.get("notes") or "").strip() or None

    db.commit()
    db.refresh(emp)
    return {
        "employee_id": emp.id,
        "case_number": emp.everify_case_number,
        "status": emp.everify_status or "not_submitted",
        "submitted_at": _iso_utc(emp.everify_submitted_at),
        "closed_at": _iso_utc(emp.everify_closed_at),
        "notes": emp.everify_notes,
    }


@router.get("/{emp_id}/portal-access")
def list_portal_access(
    emp_id: int,
    limit: int = Query(default=20, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """Recent portal_accesses rows for one employee. Powers the admin
    "who hit my portal page when?" view in the Employee Details modal."""
    from app.models.portal_access import PortalAccess

    if not db.query(Employee).filter(Employee.id == emp_id).first():
        raise HTTPException(status_code=404, detail="Employee not found")
    rows = (
        db.query(PortalAccess)
        .filter(PortalAccess.employee_id == emp_id)
        .order_by(PortalAccess.id.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": r.id,
            "created_at": _iso_utc(r.created_at),
            "ip": r.ip,
            "user_agent": r.user_agent,
            "path": r.path,
            "success": r.success,
        }
        for r in rows
    ]


@router.post("/{emp_id}/portal-token")
def regenerate_portal_token(
    request: Request, emp_id: int, db: Session = Depends(get_db)
):
    """Rotate the portal token (invalidates the previous self-service link)."""
    emp = db.query(Employee).filter(Employee.id == emp_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    _mint_portal_token(emp)
    db.commit()
    return {
        "employee_id": emp.id,
        "portal_token": emp.portal_token,
        "portal_url": _portal_url(request, emp.portal_token),
        "expires_at": _iso_utc(emp.portal_token_expires_at),
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
    # ABA checksum, not just 9-digits — same validation the portal and the
    # NACHA exporter use, so a typo'd routing number is caught at entry.
    if not validate_routing_number(routing):
        raise HTTPException(status_code=400, detail="Invalid routing number")
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

    content = await read_limited(file, label="Import file")
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
