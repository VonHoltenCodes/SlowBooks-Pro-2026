# ============================================================================
# Employee Self-Service Portal — cookie-authenticated, server-rendered pages.
#
# Two URL families:
#
#   /portal/{token}/*    — backward-compat entry points (emailed links,
#                          old bookmarks). Each one validates the token,
#                          sets a HttpOnly session cookie carrying it, and
#                          303-redirects to the cookieless equivalent so
#                          the URL bar no longer holds the token.
#
#   /portal/*            — the real handlers. They read the token out of
#                          the `slowbooks_portal` cookie. After the first
#                          hop the employee never sees the token in a URL
#                          again — no Referer leak, no shared-bookmark
#                          leak, no browser-history breadcrumb.
# ============================================================================

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader
from sqlalchemy.orm import Session

from app.config import FORCE_HTTPS
from app.database import get_db
from app.models.bank_accounts import BankAccountKind, DepositType, EmployeeBankAccount
from app.models.payroll import Employee, FilingStatus
from app.models.pto import PTOAccrual, PTOPolicy, PTORequest, PTOType
from app.services.encryption import encrypt
from app.services.rate_limit import limiter
from app.services.settings_service import get_all_settings

router = APIRouter(tags=["portal"])

TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
_jinja_env = Environment(autoescape=True, loader=FileSystemLoader(str(TEMPLATE_DIR)))

# Idle window: a token unused for this long is treated as revoked. Sliding
# window — every authenticated request rolls last_used forward.
PORTAL_TOKEN_IDLE_DAYS = 90

# Cookie that carries the portal token after the first claim.
PORTAL_COOKIE_NAME = "slowbooks_portal"
PORTAL_COOKIE_MAX_AGE = (
    60 * 60 * 24 * 30
)  # 30 days; idle expiry is enforced server-side

_PORTAL_HEADERS = {
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store, max-age=0",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _to_utc(dt: datetime) -> datetime:
    """Treat naive datetimes as UTC. SQLite returns naive timestamps even when
    we wrote them with tzinfo; PostgreSQL returns tz-aware."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _get_employee(token: str, db: Session) -> Employee:
    """Resolve a portal token to an active, non-expired employee, or HTTPException.

    Updates `portal_token_last_used` on every successful lookup. 90-day idle
    + hard expiry-at enforced.
    """
    employee = db.query(Employee).filter(Employee.portal_token == token).first()
    if not employee or not employee.is_active:
        raise HTTPException(status_code=404, detail="Portal not found")

    now = _now()
    if (
        employee.portal_token_expires_at
        and _to_utc(employee.portal_token_expires_at) < now
    ):
        raise HTTPException(status_code=410, detail="Portal token has expired")
    if employee.portal_token_last_used is not None:
        idle_cutoff = now - timedelta(days=PORTAL_TOKEN_IDLE_DAYS)
        if _to_utc(employee.portal_token_last_used) < idle_cutoff:
            raise HTTPException(status_code=410, detail="Portal token has expired")

    employee.portal_token_last_used = now
    db.commit()
    return employee


def _branding(db: Session) -> dict:
    settings = get_all_settings(db)
    return {
        "company_name": settings.get("company_name") or "Employer",
        "company_logo_url": settings.get("company_logo_path") or "",
    }


def _render(name: str, db: Session, **ctx) -> HTMLResponse:
    template = _jinja_env.get_template(f"portal/{name}")
    return HTMLResponse(
        template.render(**_branding(db), **ctx), headers=_PORTAL_HEADERS
    )


def _portal_redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url=url, status_code=303, headers=_PORTAL_HEADERS)


def _set_portal_cookie(response, token: str) -> None:
    """Stamp the response with the HttpOnly portal-session cookie."""
    response.set_cookie(
        key=PORTAL_COOKIE_NAME,
        value=token,
        max_age=PORTAL_COOKIE_MAX_AGE,
        httponly=True,
        secure=FORCE_HTTPS,
        samesite="strict",
        path="/portal",
    )


def _claim(token: str, redirect_to: str, db: Session) -> RedirectResponse:
    """Validate the URL-supplied token, stamp the cookie, redirect away."""
    _get_employee(token, db)  # raises 404 / 410
    response = _portal_redirect(redirect_to)
    _set_portal_cookie(response, token)
    return response


def _employee_from_cookie(request: Request, db: Session) -> Employee:
    """Resolve the employee from the portal cookie, or 401 if absent / invalid."""
    token = request.cookies.get(PORTAL_COOKIE_NAME)
    if not token:
        raise HTTPException(
            status_code=401,
            detail="Portal session required — open the link from your email again",
        )
    return _get_employee(token, db)


def _processed_stub_count(emp: Employee) -> int:
    return sum(
        1
        for stub in emp.pay_stubs
        if stub.pay_run and stub.pay_run.status.value == "processed"
    )


# ---------------------------------------------------------------------------
# Cookieless handlers — the real implementations.
# Registered BEFORE the catch-all `/portal/{token}` so literal paths
# (`/portal/`, `/portal/paystubs`, etc.) win the routing match.
# ---------------------------------------------------------------------------


@router.get("/portal/")
@limiter.limit("30/minute")
def portal_dashboard(request: Request, db: Session = Depends(get_db)):
    emp = _employee_from_cookie(request, db)
    return _render(
        "dashboard.html",
        db,
        emp=emp,
        stub_count=_processed_stub_count(emp),
    )


@router.get("/portal/paystubs")
@limiter.limit("30/minute")
def portal_paystubs(request: Request, db: Session = Depends(get_db)):
    emp = _employee_from_cookie(request, db)
    stubs = [
        stub
        for stub in emp.pay_stubs
        if stub.pay_run and stub.pay_run.status.value == "processed"
    ]
    stubs.sort(key=lambda s: s.pay_run.pay_date, reverse=True)
    return _render("paystubs.html", db, emp=emp, stubs=stubs)


@router.get("/portal/profile")
@limiter.limit("30/minute")
def portal_profile(request: Request, saved: int = 0, db: Session = Depends(get_db)):
    emp = _employee_from_cookie(request, db)
    return _render(
        "profile.html",
        db,
        emp=emp,
        filing_statuses=list(FilingStatus),
        saved=saved,
    )


@router.post("/portal/profile")
@limiter.limit("10/minute")
def portal_profile_save(
    request: Request,
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
    emp = _employee_from_cookie(request, db)
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
    return _portal_redirect("/portal/profile?saved=1")


@router.get("/portal/bank")
@limiter.limit("30/minute")
def portal_bank(
    request: Request,
    saved: int = 0,
    error: str = "",
    db: Session = Depends(get_db),
):
    emp = _employee_from_cookie(request, db)
    accounts = (
        db.query(EmployeeBankAccount)
        .filter(EmployeeBankAccount.employee_id == emp.id)
        .order_by(EmployeeBankAccount.id)
        .all()
    )
    return _render(
        "bank.html",
        db,
        emp=emp,
        accounts=accounts,
        account_kinds=list(BankAccountKind),
        deposit_types=list(DepositType),
        saved=saved,
        error=error,
    )


@router.post("/portal/bank")
@limiter.limit("10/minute")
def portal_bank_add(
    request: Request,
    nickname: str = Form(""),
    account_kind: str = Form(...),
    routing_number: str = Form(...),
    account_number: str = Form(...),
    deposit_type: str = Form(...),
    db: Session = Depends(get_db),
):
    emp = _employee_from_cookie(request, db)
    routing = routing_number.strip()
    account = account_number.strip()
    if not (routing.isdigit() and len(routing) == 9):
        return _portal_redirect("/portal/bank?error=Routing+number+must+be+9+digits")
    if not account.isdigit():
        return _portal_redirect("/portal/bank?error=Account+number+must+be+numeric")
    try:
        kind = BankAccountKind(account_kind)
        dtype = DepositType(deposit_type)
    except ValueError:
        return _portal_redirect("/portal/bank?error=Invalid+selection")

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
    return _portal_redirect("/portal/bank?saved=1")


@router.get("/portal/pto")
@limiter.limit("30/minute")
def portal_pto(request: Request, saved: int = 0, db: Session = Depends(get_db)):
    emp = _employee_from_cookie(request, db)
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
        db,
        emp=emp,
        accruals=accruals,
        requests=requests,
        pto_types=list(PTOType),
        saved=saved,
    )


@router.post("/portal/pto")
@limiter.limit("10/minute")
def portal_pto_request(
    request: Request,
    start_date: str = Form(...),
    end_date: str = Form(...),
    hours: float = Form(0),
    pto_type: str = Form(...),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    emp = _employee_from_cookie(request, db)
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
    return _portal_redirect("/portal/pto?saved=1")


@router.post("/portal/logout")
def portal_logout():
    """Clear the portal cookie and send the employee somewhere neutral."""
    response = _portal_redirect("/portal/")
    response.delete_cookie(PORTAL_COOKIE_NAME, path="/portal")
    return response


# ---------------------------------------------------------------------------
# Backward-compat claim shims — the URL the employee receives by email is
# still /portal/{token}/... but each of these now sets the cookie and
# 303-redirects to the cookieless URL. After one hop the token is no longer
# in the URL bar, the browser history, or any Referer.
# ---------------------------------------------------------------------------


@router.get("/portal/{token}")
@limiter.limit("30/minute")
def portal_claim_dashboard(request: Request, token: str, db: Session = Depends(get_db)):
    return _claim(token, "/portal/", db)


@router.get("/portal/{token}/paystubs")
@limiter.limit("30/minute")
def portal_claim_paystubs(request: Request, token: str, db: Session = Depends(get_db)):
    return _claim(token, "/portal/paystubs", db)


@router.get("/portal/{token}/profile")
@limiter.limit("30/minute")
def portal_claim_profile(request: Request, token: str, db: Session = Depends(get_db)):
    return _claim(token, "/portal/profile", db)


@router.get("/portal/{token}/bank")
@limiter.limit("30/minute")
def portal_claim_bank(request: Request, token: str, db: Session = Depends(get_db)):
    return _claim(token, "/portal/bank", db)


@router.get("/portal/{token}/pto")
@limiter.limit("30/minute")
def portal_claim_pto(request: Request, token: str, db: Session = Depends(get_db)):
    return _claim(token, "/portal/pto", db)


# POST routes with token in the URL — process inline, stamp the cookie, then
# redirect to the cookieless URL. Browsers can't redirect a POST across paths
# cleanly, so we do the work first and 303 to the GET equivalent.
def _set_cookie_on(response, token: str):
    _set_portal_cookie(response, token)
    return response


@router.post("/portal/{token}/profile")
@limiter.limit("10/minute")
def portal_claim_profile_save(
    request: Request,
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
    return _set_cookie_on(_portal_redirect("/portal/profile?saved=1"), token)


@router.post("/portal/{token}/bank")
@limiter.limit("10/minute")
def portal_claim_bank_add(
    request: Request,
    token: str,
    nickname: str = Form(""),
    account_kind: str = Form(...),
    routing_number: str = Form(...),
    account_number: str = Form(...),
    deposit_type: str = Form(...),
    db: Session = Depends(get_db),
):
    emp = _get_employee(token, db)
    routing = routing_number.strip()
    account = account_number.strip()
    if not (routing.isdigit() and len(routing) == 9):
        return _set_cookie_on(
            _portal_redirect("/portal/bank?error=Routing+number+must+be+9+digits"),
            token,
        )
    if not account.isdigit():
        return _set_cookie_on(
            _portal_redirect("/portal/bank?error=Account+number+must+be+numeric"),
            token,
        )
    try:
        kind = BankAccountKind(account_kind)
        dtype = DepositType(deposit_type)
    except ValueError:
        return _set_cookie_on(
            _portal_redirect("/portal/bank?error=Invalid+selection"), token
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
    return _set_cookie_on(_portal_redirect("/portal/bank?saved=1"), token)


@router.post("/portal/{token}/pto")
@limiter.limit("10/minute")
def portal_claim_pto_request(
    request: Request,
    token: str,
    start_date: str = Form(...),
    end_date: str = Form(...),
    hours: float = Form(0),
    pto_type: str = Form(...),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
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
    return _set_cookie_on(_portal_redirect("/portal/pto?saved=1"), token)
