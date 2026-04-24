# ============================================================================
# Slowbooks Pro 2026 — Single-user authentication (Phase 9.7)
#
# Threat model: LAN deployment, one operator. No user model, no RBAC —
# just "did you type the one password". Password hashed with argon2id,
# session issued via Starlette SessionMiddleware (signed cookie).
# ============================================================================

import os
import secrets
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import HTTPException, Request, status
from sqlalchemy.orm import Session

from app.services.settings_service import get_setting_raw, set_setting

# Settings-table key where the argon2 hash lives
AUTH_PASSWORD_KEY = "auth_password_hash"

# Session cookie name + lifetime
SESSION_COOKIE_NAME = "slowbooks_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 30  # 30 days

# Minimum password length for setup
MIN_PASSWORD_LEN = 8

# argon2-cffi defaults: time_cost=3, memory_cost=65536 (64MB), parallelism=4
# → ~100 ms per hash on a modern CPU, expensive enough to kill brute force.
_hasher = PasswordHasher()


def hash_password(plain: str) -> str:
    """Hash a plaintext password with argon2id."""
    return _hasher.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a plaintext password against an argon2 hash."""
    try:
        _hasher.verify(hashed, plain)
        return True
    except (VerifyMismatchError, InvalidHashError, ValueError):
        return False


def get_session_secret() -> str:
    """
    Resolve the session-signing secret.

    Priority order:
      1. SESSION_SECRET_KEY env var (ops-preferred)
      2. .slowbooks-session.key file next to the repo (auto-created at 0600)
      3. Fresh random generation (not persisted if the FS is read-only)
    """
    env_key = os.environ.get("SESSION_SECRET_KEY", "").strip()
    if env_key:
        return env_key

    key_path = Path(__file__).resolve().parents[2] / ".slowbooks-session.key"
    if key_path.exists():
        try:
            existing = key_path.read_text().strip()
            if existing:
                return existing
        except OSError:
            pass

    new_key = secrets.token_urlsafe(48)
    try:
        import tempfile

        fd, tmp = tempfile.mkstemp(dir=str(key_path.parent), prefix=".session-key-")
        os.write(fd, new_key.encode())
        os.close(fd)
        os.chmod(tmp, 0o600)
        os.replace(tmp, str(key_path))
    except OSError:
        pass
    if key_path.exists():
        try:
            return key_path.read_text().strip() or new_key
        except OSError:
            pass
    return new_key


def password_is_set(db: Session) -> bool:
    """Has the operator completed first-run setup?"""
    stored = get_setting_raw(db, AUTH_PASSWORD_KEY)
    return bool((stored or "").strip())


def set_password(db: Session, plain: str) -> None:
    """Store a new argon2id hash for the operator password."""
    if not plain or len(plain) < MIN_PASSWORD_LEN:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Password must be at least {MIN_PASSWORD_LEN} characters",
        )
    set_setting(db, AUTH_PASSWORD_KEY, hash_password(plain))
    db.commit()


def check_password(db: Session, plain: str) -> bool:
    """Check a submitted password against the stored hash."""
    stored = get_setting_raw(db, AUTH_PASSWORD_KEY) or ""
    if not stored:
        return False
    return verify_password(plain, stored)


def require_auth(request: Request) -> None:
    """
    FastAPI dependency that rejects unauthenticated requests with 401.

    Applied at router registration time via:
        app.include_router(foo.router, dependencies=[Depends(require_auth)])
    """
    if request.session.get("authenticated") is not True:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
