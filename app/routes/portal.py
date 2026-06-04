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
from app.models.portal_access import PortalAccess
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


def _claim(
    token: str, redirect_to: str, db: Session, request: Request | None = None
) -> RedirectResponse:
    """Validate the URL-supplied token, stamp the cookie, redirect away.

    Records a portal_accesses row when `request` is provided — backward-
    compat token URLs route here and the operator wants to see them in
    the audit trail too.
    """
    try:
        emp = _get_employee(token, db)
    except HTTPException:
        if request is not None:
            _record_portal_access(db, request, employee_id=None, success=False)
        raise
    if request is not None:
        _record_portal_access(db, request, employee_id=emp.id, success=True)
    response = _portal_redirect(redirect_to)
    # Use the DB-resident token value rather than the URL param. Same
    # string by construction (we just matched on it), but sourcing from
    # the validated Employee row gives static analyzers a clear sanitizer
    # boundary for the cookie write (CodeQL: py/cookie-injection).
    _set_portal_cookie(response, emp.portal_token)
    return response


def _client_ip(request: Request) -> str:
    """Client IP, capped at 45 chars for IPv6. Honors X-Forwarded-For only
    behind a declared trusted proxy (TRUST_PROXY_HEADERS) — otherwise XFF is
    client-spoofable and would poison the portal access audit trail."""
    from app.config import TRUST_PROXY_HEADERS

    fwd = request.headers.get("x-forwarded-for", "") if TRUST_PROXY_HEADERS else ""
    if fwd:
        return fwd.split(",")[0].strip()[:45]
    client = request.client
    return (client.host if client else "")[:45]


def _redact_portal_path(path: str) -> str:
    """Strip the portal token out of a path before it's persisted.

    Legacy /portal/<token>/... URLs carry a live bearer credential in the
    path segment. Logging it verbatim would write a working token into
    portal_accesses (visible in the admin audit UI and every pg_dump) —
    anyone with read access could then impersonate the employee. Cookieless
    route names (/portal/paystubs, /portal/profile) are short words, so a
    length heuristic cleanly distinguishes them from a ~32-char token.
    """
    parts = path.split("/")
    # ['', 'portal', '<maybe token>', ...] — redact a long 3rd segment.
    if len(parts) >= 3 and len(parts[2]) > 20:
        parts[2] = "REDACTED"
    return "/".join(parts)


def _record_portal_access(
    db: Session, request: Request, employee_id: int | None, success: bool
) -> None:
    """Insert one portal_accesses row. Swallows write failures so the audit
    write can never break the request itself."""
    try:
        db.add(
            PortalAccess(
                employee_id=employee_id,
                ip=_client_ip(request),
                user_agent=(request.headers.get("user-agent") or "")[:255],
                path=_redact_portal_path(request.url.path)[:200],
                success=success,
            )
        )
        db.commit()
    except Exception:
        db.rollback()


def _employee_from_cookie(request: Request, db: Session) -> Employee:
    """Resolve the employee from the portal cookie, or 401 if absent / invalid.

    Records a portal_accesses row on every call — success and failure both
    show up so forensic queries can spot patterns (e.g. a sudden burst of
    401s from one IP)."""
    token = request.cookies.get(PORTAL_COOKIE_NAME)
    if not token:
        _record_portal_access(db, request, employee_id=None, success=False)
        raise HTTPException(
            status_code=401,
            detail="Portal session required — open the link from your email again",
        )
    try:
        emp = _get_employee(token, db)
    except HTTPException:
        _record_portal_access(db, request, employee_id=None, success=False)
        raise
    _record_portal_access(db, request, employee_id=emp.id, success=True)
    return emp


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


@router.get("/portal/favicon.ico")
def portal_favicon(db: Session = Depends(get_db)):
    """Serve the employer's company logo as the portal favicon.

    Falls back to a 204 if no logo is configured — better than a 404 in
    every browser dev-tools console. The actual <link rel="icon"> in the
    portal templates points here, so each customer's portal carries their
    own bookmark icon.
    """
    from fastapi.responses import FileResponse, Response

    settings = get_all_settings(db)
    logo_path = (settings.get("company_logo_path") or "").lstrip("/")
    if not logo_path:
        return Response(status_code=204)

    # Same resolve-within guard the attachments code uses — never serve
    # anything outside the static dir.
    static_root = (Path(__file__).parent.parent / "static").resolve()
    full_path = (static_root / logo_path.removeprefix("static/")).resolve()
    try:
        full_path.relative_to(static_root)
    except ValueError:
        return Response(status_code=204)
    if not full_path.exists():
        return Response(status_code=204)
    return FileResponse(full_path, media_type="image/png")


# ---------------------------------------------------------------------------
# Backward-compat claim shims — the URL the employee receives by email is
# still /portal/{token}/... but each of these now sets the cookie and
# 303-redirects to the cookieless URL. After one hop the token is no longer
# in the URL bar, the browser history, or any Referer.
# ---------------------------------------------------------------------------


@router.get("/portal/{token}")
@limiter.limit("30/minute")
def portal_claim_dashboard(request: Request, token: str, db: Session = Depends(get_db)):
    return _claim(token, "/portal/", db, request)


@router.get("/portal/{token}/paystubs")
@limiter.limit("30/minute")
def portal_claim_paystubs(request: Request, token: str, db: Session = Depends(get_db)):
    return _claim(token, "/portal/paystubs", db, request)


@router.get("/portal/{token}/profile")
@limiter.limit("30/minute")
def portal_claim_profile(request: Request, token: str, db: Session = Depends(get_db)):
    return _claim(token, "/portal/profile", db, request)


@router.get("/portal/{token}/bank")
@limiter.limit("30/minute")
def portal_claim_bank(request: Request, token: str, db: Session = Depends(get_db)):
    return _claim(token, "/portal/bank", db, request)


@router.get("/portal/{token}/pto")
@limiter.limit("30/minute")
def portal_claim_pto(request: Request, token: str, db: Session = Depends(get_db)):
    return _claim(token, "/portal/pto", db, request)


# POST routes with token in the URL — process inline, stamp the cookie, then
# redirect to the cookieless URL. Browsers can't redirect a POST across paths
# cleanly, so we do the work first and 303 to the GET equivalent.
def _set_cookie_on(response, emp: Employee):
    # Cookie value is read from the validated Employee row, not the URL
    # param. Same string by construction, but DB-sourced gives static
    # analyzers a clear sanitizer for the cookie write
    # (CodeQL: py/cookie-injection).
    _set_portal_cookie(response, emp.portal_token)
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
    return _set_cookie_on(_portal_redirect("/portal/profile?saved=1"), emp)


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
            emp,
        )
    if not account.isdigit():
        return _set_cookie_on(
            _portal_redirect("/portal/bank?error=Account+number+must+be+numeric"),
            emp,
        )
    try:
        kind = BankAccountKind(account_kind)
        dtype = DepositType(deposit_type)
    except ValueError:
        return _set_cookie_on(
            _portal_redirect("/portal/bank?error=Invalid+selection"), emp
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
    return _set_cookie_on(_portal_redirect("/portal/bank?saved=1"), emp)


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
    return _set_cookie_on(_portal_redirect("/portal/pto?saved=1"), emp)
