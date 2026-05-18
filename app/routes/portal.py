# ============================================================================
# Employee Self-Service Portal — token-accessed, server-rendered HTML pages
# Lets an employee view pay stubs and update their own W-4, address, bank
# accounts, and PTO requests. Access is by per-employee secret token, the
# same pattern as the public invoice-payment page (no login system).
# ============================================================================

from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.bank_accounts import BankAccountKind, DepositType, EmployeeBankAccount
from app.models.payroll import Employee, FilingStatus
from app.models.pto import PTOAccrual, PTOPolicy, PTORequest, PTOType
from app.services.encryption import encrypt

router = APIRouter(tags=["portal"])

TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
_jinja_env = Environment(autoescape=True, loader=FileSystemLoader(str(TEMPLATE_DIR)))


def _get_employee(token: str, db: Session) -> Employee:
    """Resolve a portal token to an active employee, or raise 404."""
    employee = db.query(Employee).filter(Employee.portal_token == token).first()
    if not employee or not employee.is_active:
        raise HTTPException(status_code=404, detail="Portal not found")
    return employee


def _render(name: str, **ctx) -> HTMLResponse:
    """Render a portal template to an HTMLResponse."""
    template = _jinja_env.get_template(f"portal/{name}")
    return HTMLResponse(template.render(**ctx))


def _processed_stub_count(emp: Employee) -> int:
    """Count the employee's pay stubs belonging to a processed pay run."""
    return sum(
        1
        for stub in emp.pay_stubs
        if stub.pay_run and stub.pay_run.status.value == "processed"
    )


@router.get("/portal/{token}")
def portal_dashboard(token: str, db: Session = Depends(get_db)):
    """Portal home — greeting, employee summary, and navigation."""
    emp = _get_employee(token, db)
    return _render(
        "dashboard.html",
        token=token,
        emp=emp,
        stub_count=_processed_stub_count(emp),
    )


@router.get("/portal/{token}/paystubs")
def portal_paystubs(token: str, db: Session = Depends(get_db)):
    """List the employee's pay stubs (newest first), linking to the PDF."""
    emp = _get_employee(token, db)
    stubs = [
        stub
        for stub in emp.pay_stubs
        if stub.pay_run and stub.pay_run.status.value == "processed"
    ]
    stubs.sort(key=lambda s: s.pay_run.pay_date, reverse=True)
    return _render("paystubs.html", token=token, emp=emp, stubs=stubs)


@router.get("/portal/{token}/profile")
def portal_profile(token: str, saved: int = 0, db: Session = Depends(get_db)):
    """Form pre-filled with the employee's W-4 election and mailing address."""
    emp = _get_employee(token, db)
    return _render(
        "profile.html",
        token=token,
        emp=emp,
        filing_statuses=list(FilingStatus),
        saved=saved,
    )


@router.post("/portal/{token}/profile")
def portal_profile_save(
    token: str,
    filing_status: str = Form(...),
    multiple_jobs: bool = Form(False),
    dependents_amount: float = Form(0),
    other_income_annual: float = Form(0),
    deductions_annual: float = Form(0),
    extra_withholding: float = Form(0),
    address1: str = Form(""),
    address2: str = Form(""),
    city: str = Form(""),
    state: str = Form(""),
    zip: str = Form(""),
    db: Session = Depends(get_db),
):
    """Persist the employee's W-4 election and mailing address."""
    emp = _get_employee(token, db)
    try:
        emp.filing_status = FilingStatus(filing_status)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid filing status")
    emp.multiple_jobs = bool(multiple_jobs)
    emp.dependents_amount = dependents_amount
    emp.other_income_annual = other_income_annual
    emp.deductions_annual = deductions_annual
    emp.extra_withholding = extra_withholding
    emp.address1 = address1 or None
    emp.address2 = address2 or None
    emp.city = city or None
    emp.state = state or None
    emp.zip = zip or None
    db.commit()
    return RedirectResponse(url=f"/portal/{token}/profile?saved=1", status_code=303)


@router.get("/portal/{token}/bank")
def portal_bank(
    token: str, saved: int = 0, error: str = "", db: Session = Depends(get_db)
):
    """List the employee's bank accounts plus an add-account form."""
    emp = _get_employee(token, db)
    accounts = (
        db.query(EmployeeBankAccount)
        .filter(EmployeeBankAccount.employee_id == emp.id)
        .order_by(EmployeeBankAccount.id)
        .all()
    )
    return _render(
        "bank.html",
        token=token,
        emp=emp,
        accounts=accounts,
        account_kinds=list(BankAccountKind),
        deposit_types=list(DepositType),
        saved=saved,
        error=error,
    )


@router.post("/portal/{token}/bank")
def portal_bank_add(
    token: str,
    nickname: str = Form(""),
    account_kind: str = Form(...),
    routing_number: str = Form(...),
    account_number: str = Form(...),
    deposit_type: str = Form(...),
    db: Session = Depends(get_db),
):
    """Validate, encrypt, and store a new direct-deposit bank account."""
    emp = _get_employee(token, db)

    routing = routing_number.strip()
    account = account_number.strip()
    if not (routing.isdigit() and len(routing) == 9):
        return RedirectResponse(
            url=f"/portal/{token}/bank?error=Routing+number+must+be+9+digits",
            status_code=303,
        )
    if not account.isdigit():
        return RedirectResponse(
            url=f"/portal/{token}/bank?error=Account+number+must+be+numeric",
            status_code=303,
        )
    try:
        kind = BankAccountKind(account_kind)
        dtype = DepositType(deposit_type)
    except ValueError:
        return RedirectResponse(
            url=f"/portal/{token}/bank?error=Invalid+selection",
            status_code=303,
        )

    db.add(
        EmployeeBankAccount(
            employee_id=emp.id,
            nickname=nickname or None,
            account_kind=kind,
            routing_number_enc=encrypt(routing),
            account_number_enc=encrypt(account),
            account_last_four=account[-4:],
            deposit_type=dtype,
            is_active=True,
        )
    )
    db.commit()
    return RedirectResponse(url=f"/portal/{token}/bank?saved=1", status_code=303)


@router.get("/portal/{token}/pto")
def portal_pto(token: str, saved: int = 0, db: Session = Depends(get_db)):
    """Show PTO balances, existing requests, and a new-request form."""
    emp = _get_employee(token, db)
    accruals = (
        db.query(PTOAccrual, PTOPolicy)
        .join(PTOPolicy, PTOAccrual.policy_id == PTOPolicy.id)
        .filter(PTOAccrual.employee_id == emp.id)
        .order_by(PTOPolicy.name)
        .all()
    )
    requests = (
        db.query(PTORequest)
        .filter(PTORequest.employee_id == emp.id)
        .order_by(PTORequest.start_date.desc())
        .all()
    )
    return _render(
        "pto.html",
        token=token,
        emp=emp,
        accruals=accruals,
        requests=requests,
        pto_types=list(PTOType),
        saved=saved,
    )


@router.post("/portal/{token}/pto")
def portal_pto_request(
    token: str,
    start_date: str = Form(...),
    end_date: str = Form(...),
    hours: float = Form(0),
    pto_type: str = Form(...),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    """Create a new PTO request (status defaults to PENDING)."""
    emp = _get_employee(token, db)
    try:
        ptype = PTOType(pto_type)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid PTO type")
    try:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Dates must be YYYY-MM-DD")

    db.add(
        PTORequest(
            employee_id=emp.id,
            start_date=start,
            end_date=end,
            hours=hours,
            pto_type=ptype,
            notes=notes or None,
        )
    )
    db.commit()
    return RedirectResponse(url=f"/portal/{token}/pto?saved=1", status_code=303)
