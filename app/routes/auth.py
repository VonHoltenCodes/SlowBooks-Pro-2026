# ============================================================================
# Slowbooks Pro 2026 — Auth routes
#
#   GET  /api/auth/status  → {setup_needed, authenticated, multi_user, user?}
#   POST /api/auth/setup   → first-time password set (409 if already set)
#   POST /api/auth/login   → password (+ username once 2+ users exist)
#   POST /api/auth/logout  → clear session
#
# These routes are deliberately NOT protected by require_auth — they're
# how you become authenticated in the first place.
# ============================================================================

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import Field
from app.schemas.common import StrictModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.auth import LoginAttempt
from app.services.auth import (
    authenticate,
    ensure_admin_user,
    is_multi_user,
    password_is_set,
    set_password,
)
from app.services.rate_limit import limiter
from app.services.request_utils import client_ip as _client_ip
from app.services.settings_service import (
    SettingValueError,
    clean_setting_value,
    set_setting,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _record_login_attempt(db: Session, request: Request, success: bool) -> None:
    """Insert a row into login_attempts. Failures aren't fatal — never let
    audit-log writes break the auth flow itself."""
    try:
        db.add(
            LoginAttempt(
                ip=_client_ip(request),
                user_agent=(request.headers.get("user-agent") or "")[:255],
                success=success,
            )
        )
        db.commit()
    except Exception:
        db.rollback()


class PasswordPayload(StrictModel):
    password: str = Field(..., min_length=1, max_length=512)
    # Server Edition: required only when more than one user exists.
    username: Optional[str] = Field(None, max_length=100)


class SetupPayload(StrictModel):
    """First-run setup. Password is required; everything else is optional and
    falls back to the DEFAULT_SETTINGS values if blank."""

    password: str = Field(..., min_length=1, max_length=512)

    # Company
    company_name: Optional[str] = Field(None, max_length=200)
    company_address1: Optional[str] = Field(None, max_length=200)
    company_address2: Optional[str] = Field(None, max_length=200)
    company_city: Optional[str] = Field(None, max_length=100)
    company_state: Optional[str] = Field(None, max_length=50)
    company_zip: Optional[str] = Field(None, max_length=20)
    company_phone: Optional[str] = Field(None, max_length=50)
    company_email: Optional[str] = Field(None, max_length=200)
    company_website: Optional[str] = Field(None, max_length=200)
    company_tax_id: Optional[str] = Field(None, max_length=50)

    # Operator
    operator_name: Optional[str] = Field(None, max_length=200)
    operator_email: Optional[str] = Field(None, max_length=200)

    # Defaults applied to new invoices
    default_terms: Optional[str] = Field(None, max_length=100)
    default_tax_rate: Optional[str] = Field(None, max_length=20)


# Settings keys we accept on /setup (anything else on the payload is ignored)
_SETUP_SETTINGS_KEYS = (
    "company_name",
    "company_address1",
    "company_address2",
    "company_city",
    "company_state",
    "company_zip",
    "company_phone",
    "company_email",
    "company_website",
    "company_tax_id",
    "operator_name",
    "operator_email",
    "default_terms",
    "default_tax_rate",
)


def _company_name(db: Session) -> str:
    """The company's name as the books carry it, else as the company list
    (the picker) names this file — a company created before its name was
    written into the file has only the latter. The shipped placeholder
    ("My Company") is nobody's name: setup must not offer it as one."""
    from app.models.settings import DEFAULT_SETTINGS
    from app.services.settings_service import get_setting_raw

    placeholder = DEFAULT_SETTINGS["company_name"]
    name = (get_setting_raw(db, "company_name") or "").strip()
    if not name:
        try:
            from app.services.company_service import current_manifest_name

            name = current_manifest_name() or ""
        except Exception:
            name = ""
    return "" if name == placeholder else name


@router.get("/status")
def auth_status(request: Request, db: Session = Depends(get_db)):
    """Tell the SPA whether first-run setup is needed and whether the
    current session is authenticated."""
    authenticated = request.session.get("authenticated") is True
    setup_needed = not password_is_set(db)
    out = {
        "setup_needed": setup_needed,
        "authenticated": authenticated,
        # Login UI shows a username field only when this is true.
        "multi_user": is_multi_user(db),
    }
    if out["multi_user"] and not authenticated:
        # The names, so the sign-in screen can offer a list instead of a
        # blank field — the way a desktop bookkeeping package does. Names
        # only, never roles; and only on a multi-user install, where the
        # people on the LAN already know each other. Server Edition is
        # documented for trusted networks; this is part of that trade.
        from app.models.users import User

        out["usernames"] = [
            u.username
            for u in db.query(User)
            .filter(User.is_active.is_(True))
            .order_by(User.username)
            .all()
        ]
    # Whose books these are. The sign-in screen names them (with several
    # companies on one machine, "Unlock Slowbooks" did not say whose
    # password it wanted — explore 2.17.3, macbase1 F2), and first-run setup
    # prefills the name, so setup neither re-asks for the name typed in the
    # New Company dialog (F3) nor silently renames a file that already holds
    # a company's books (2.9.0 gate).
    out["company_name"] = _company_name(db)
    if setup_needed:
        from app.models.transactions import Transaction

        out["has_data"] = db.query(Transaction.id).first() is not None
    if authenticated and request.session.get("username"):
        out["user"] = {
            "username": request.session.get("username"),
            "display_name": request.session.get("display_name") or "",
            "role": request.session.get("role") or "admin",
        }
    return out


@router.post("/setup")
def setup(
    payload: SetupPayload,
    request: Request,
    db: Session = Depends(get_db),
):
    """First-run setup: store company/operator info and the operator password
    in one transaction, then issue a session. Returns 409 if a password is
    already set."""
    if password_is_set(db):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Password already set — use /login",
        )

    if payload.company_name:
        from app.services.company_service import (
            _current_company_file,
            company_name_taken_by,
        )

        other = company_name_taken_by(
            payload.company_name, exclude_file=_current_company_file()
        )
        if other:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Another company file ({other}) is already named "
                    f"'{payload.company_name.strip()}'. Choose a name that "
                    "tells the two apart."
                ),
            )

    # Persist any non-blank settings the user provided. set_password() will
    # commit at the end, so all writes land in a single transaction. The
    # values Settings checks (a default tax rate from 0 to 100) are checked
    # here too, before anything is written.
    payload_dict = payload.model_dump()
    to_store = {}
    for key in _SETUP_SETTINGS_KEYS:
        value = payload_dict.get(key)
        if value is not None and value != "":
            try:
                to_store[key] = clean_setting_value(key, value)
            except SettingValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from None
    for key, value in to_store.items():
        set_setting(db, key, value)

    set_password(db, payload.password)
    # Materialize the operator as the admin user row right away (Server
    # Edition principal model) — same password, zero extra questions.
    admin = ensure_admin_user(db)
    if payload.company_name:
        from app.services.company_service import sync_manifest_name

        sync_manifest_name(payload.company_name)
    # Rotate session before issuing — clears anything an attacker might have
    # planted via a fixation attempt. Starlette's signed-cookie session is
    # already fixation-resistant (signature changes with payload) but this
    # is defence in depth and intent-revealing.
    request.session.clear()
    request.session["authenticated"] = True
    _stash_user(request, admin)
    return {"status": "ok", "authenticated": True}


def _stash_user(request: Request, user) -> None:
    """Record the acting principal in the session (None = legacy no-row)."""
    if user is None:
        return
    request.session["user_id"] = user.id
    request.session["username"] = user.username
    request.session["display_name"] = user.display_name
    request.session["role"] = user.role


@router.post("/login")
@limiter.limit("5/minute")
def login(
    request: Request,
    payload: PasswordPayload,
    db: Session = Depends(get_db),
):
    """Verify the operator password and issue a session.

    Rate-limited to 5/minute per IP to kill fast brute-force. argon2id's
    ~100ms-per-verify cost is the second line. Every attempt — success or
    failure — is recorded in `login_attempts` so a slow patient attacker
    pacing requests under the rate limit still shows up in the audit log.
    """
    if not password_is_set(db):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Setup required — set a password first",
        )
    if is_multi_user(db) and not (payload.username or "").strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username required",
        )
    user = authenticate(db, payload.password, username=payload.username)
    if user is None:
        _record_login_attempt(db, request, success=False)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "Incorrect username or password"
                if is_multi_user(db)
                else "Incorrect password"
            ),
        )
    _record_login_attempt(db, request, success=True)
    # Same rotation rationale as /setup.
    request.session.clear()
    request.session["authenticated"] = True
    _stash_user(request, user)
    return {"status": "ok", "authenticated": True}


@router.post("/logout")
def logout(request: Request):
    """Clear the session cookie."""
    request.session.clear()
    return {"status": "ok"}
