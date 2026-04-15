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

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.auth import (
    check_password,
    password_is_set,
    set_password,
)
from app.services.rate_limit import limiter

router = APIRouter(prefix="/api/auth", tags=["auth"])


class PasswordPayload(BaseModel):
    password: str = Field(..., min_length=1, max_length=512)


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
    payload: PasswordPayload,
    request: Request,
    db: Session = Depends(get_db),
):
    """First-run password set. Returns 409 if a password is already set."""
    if password_is_set(db):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Password already set — use /login",
        )
    set_password(db, payload.password)
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

    Rate-limited to 5/minute per IP to kill brute-force. argon2id's
    ~100ms-per-verify cost is the second line of defence.
    """
    if not password_is_set(db):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Setup required — set a password first",
        )
    if not check_password(db, payload.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect password",
        )
    request.session["authenticated"] = True
    return {"status": "ok", "authenticated": True}


@router.post("/logout")
def logout(request: Request):
    """Clear the session cookie."""
    request.session.clear()
    return {"status": "ok"}
