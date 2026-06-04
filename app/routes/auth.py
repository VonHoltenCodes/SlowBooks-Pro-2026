# ============================================================================
# Slowbooks Pro 2026 — Auth routes (Phase 9.7)
#
# Single-operator password flow. No user model — just:
#   GET  /api/auth/status  → {setup_needed, authenticated}
#   POST /api/auth/setup   → first-time password set (409 if already set)
#   POST /api/auth/login   → verify password, issue session cookie
#   POST /api/auth/logout  → clear session
#
# These routes are deliberately NOT protected by require_auth — they're
# how you become authenticated in the first place.
# ============================================================================

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.auth import LoginAttempt
from app.services.auth import (
    check_password,
    password_is_set,
    set_password,
)
from app.services.rate_limit import limiter
from app.services.settings_service import set_setting

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _client_ip(request: Request) -> str:
    """Best-effort client IP. Honors X-Forwarded-For ONLY when the deployment
    declares it runs behind a trusted proxy (TRUST_PROXY_HEADERS) — otherwise
    XFF is client-spoofable and would let an attacker forge the audited IP.
    Direct deploys fall back to the socket peer."""
    from app.config import TRUST_PROXY_HEADERS

    fwd = request.headers.get("x-forwarded-for", "") if TRUST_PROXY_HEADERS else ""
    if fwd:
        # Take the first hop — that's the client (proxies append to the right).
        return fwd.split(",")[0].strip()[:45]
    client = request.client
    return (client.host if client else "")[:45]


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


class PasswordPayload(BaseModel):
    password: str = Field(..., min_length=1, max_length=512)


class SetupPayload(BaseModel):
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


@router.get("/status")
def auth_status(request: Request, db: Session = Depends(get_db)):
    """Tell the SPA whether first-run setup is needed and whether the
    current session is authenticated."""
    return {
        "setup_needed": not password_is_set(db),
        "authenticated": request.session.get("authenticated") is True,
    }


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

    # Persist any non-blank settings the user provided. set_password() will
    # commit at the end, so all writes land in a single transaction.
    payload_dict = payload.model_dump()
    for key in _SETUP_SETTINGS_KEYS:
        value = payload_dict.get(key)
        if value is not None and value != "":
            set_setting(db, key, value)

    set_password(db, payload.password)
    # Rotate session before issuing — clears anything an attacker might have
    # planted via a fixation attempt. Starlette's signed-cookie session is
    # already fixation-resistant (signature changes with payload) but this
    # is defence in depth and intent-revealing.
    request.session.clear()
    request.session["authenticated"] = True
    return {"status": "ok", "authenticated": True}


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
    if not check_password(db, payload.password):
        _record_login_attempt(db, request, success=False)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect password",
        )
    _record_login_attempt(db, request, success=True)
    # Same rotation rationale as /setup.
    request.session.clear()
    request.session["authenticated"] = True
    return {"status": "ok", "authenticated": True}


@router.post("/logout")
def logout(request: Request):
    """Clear the session cookie."""
    request.session.clear()
    return {"status": "ok"}
