# ============================================================================
# Slowbooks Pro 2026 — Authentication
#
# Passwords hashed with argon2id, sessions via Starlette SessionMiddleware
# (signed cookie). Two shapes, one code path:
#   - Single-operator (desktop default): one password, no username asked.
#     Backed by the settings-table hash AND a single admin user row.
#   - Server Edition multi-user: the users table is the principal model;
#     username+password once a second user exists. RBAC enforcement is
#     layered separately.
# ============================================================================

import logging
import os
import secrets
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import HTTPException, Request, status
from sqlalchemy.orm import Session

from app.services.settings_service import get_setting_raw, set_setting

logger = logging.getLogger(__name__)

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

    The diagnostics below are deliberate: this key rotating unexpectedly
    silently invalidates every existing session cookie (users get bounced
    to a login screen mid-use with no visible cause). #3 is a SILENT
    fallback by design elsewhere in this function -- these log lines exist
    so that when it fires, it shows up in the desktop install's log
    instead of vanishing into an `except OSError: pass`.
    """
    env_key = os.environ.get("SESSION_SECRET_KEY", "").strip()
    if env_key:
        return env_key

    key_path = Path(__file__).resolve().parents[2] / ".slowbooks-session.key"
    if key_path.exists():
        try:
            existing = key_path.read_text(encoding="utf-8").strip()
            if existing:
                logger.info("session secret loaded from %s", key_path)
                return existing
        except OSError as exc:
            logger.warning("could not read %s: %s", key_path, exc)

    new_key = secrets.token_urlsafe(48)
    persisted = False
    try:
        import tempfile

        fd, tmp = tempfile.mkstemp(dir=str(key_path.parent), prefix=".session-key-")
        os.write(fd, new_key.encode())
        os.close(fd)
        os.chmod(tmp, 0o600)
        os.replace(tmp, str(key_path))
        persisted = True
    except OSError as exc:
        logger.warning("could not persist session secret to %s: %s", key_path, exc)
    if key_path.exists():
        try:
            existing = key_path.read_text(encoding="utf-8").strip()
            if existing:
                if persisted:
                    logger.info("new session secret written to %s", key_path)
                return existing
        except OSError:
            pass
    logger.warning(
        "session secret is NOT persisted -- it will be different every "
        "time the server restarts, which logs everyone out without "
        "warning. This should only ever log once, on a truly first-ever "
        "launch; if it keeps appearing on every restart, %s isn't writable.",
        key_path,
    )
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
    new_hash = hash_password(plain)
    set_setting(db, AUTH_PASSWORD_KEY, new_hash)
    # Keep the (single) admin user row in lockstep — on single-operator
    # installs the settings hash and the admin row are the same password.
    from app.models.users import ROLE_ADMIN, User

    admin = (
        db.query(User)
        .filter(User.role == ROLE_ADMIN, User.is_active)
        .order_by(User.id)
        .first()
    )
    if admin is not None and db.query(User).count() == 1:
        admin.password_hash = new_hash
    db.commit()


def check_password(db: Session, plain: str) -> bool:
    """Check a submitted password against the stored hash."""
    stored = get_setting_raw(db, AUTH_PASSWORD_KEY) or ""
    if not stored:
        return False
    return verify_password(plain, stored)


# ---------------------------------------------------------------------------
# Principals (Server Edition groundwork)
#
# The users table is the source of truth for who can sign in. Legacy
# installs (settings-table hash, no user rows) are backfilled with an
# "admin" row the first time the login path runs — after which the
# settings hash is kept in sync but the user row wins.
# ---------------------------------------------------------------------------

DEFAULT_ADMIN_USERNAME = "admin"


def _users_query(db: Session):
    from app.models.users import User

    return db.query(User)


def count_active_users(db: Session) -> int:
    from app.models.users import User

    return _users_query(db).filter(User.is_active).count()


def is_multi_user(db: Session) -> bool:
    return count_active_users(db) > 1


def ensure_admin_user(db: Session):
    """Backfill the implicit operator as a real admin user row.

    No-op when any user exists. Uses the settings-table hash verbatim
    (argon2 hashes are portable strings), so the operator's password
    keeps working with zero interaction on upgrade.
    """
    from app.models.users import ROLE_ADMIN, User

    existing = _users_query(db).first()
    if existing is not None:
        return existing
    stored = get_setting_raw(db, AUTH_PASSWORD_KEY) or ""
    if not stored.strip():
        return None
    admin = User(
        username=DEFAULT_ADMIN_USERNAME,
        display_name=(get_setting_raw(db, "operator_name") or "").strip() or "Operator",
        password_hash=stored,
        role=ROLE_ADMIN,
        is_active=True,
    )
    db.add(admin)
    db.commit()
    logger.info("Backfilled operator as admin user")
    return admin


def authenticate(db: Session, password: str, username: str | None = None):
    """Resolve credentials to a User, or None.

    Single-user installs authenticate by password alone (username, if
    sent, is ignored) — identical UX to the legacy flow. With more than
    one active user, the username is required and selects the account.
    """
    from datetime import datetime, timezone

    from app.models.users import User

    ensure_admin_user(db)

    if is_multi_user(db):
        if not (username or "").strip():
            return None
        user = (
            _users_query(db)
            .filter(User.username == username.strip().lower(), User.is_active)
            .first()
        )
    else:
        user = _users_query(db).filter(User.is_active).first()

    if user is None or not verify_password(password, user.password_hash):
        return None
    # Self-attribute the login's own audit row (field finding: the session
    # carries no username yet at this point, so without this stamp every
    # login writes the most visible unattributed row on the audit page).
    db.info["acting_username"] = user.username
    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    return user


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


# ---------------------------------------------------------------------------
# "Ask for the password each time SlowBooks Pro starts" (explore 2.17.3,
# macbase1 S-j). The session cookie outlives the app — the desktop window
# keeps a persistent profile so that print windows and downloads share the
# sign-in — so quitting and relaunching reopened the company signed in. A
# company can now choose otherwise: every session remembers which start of
# the server signed it in, and with the setting on, one from an earlier
# start counts as signed out. Off by default, so nothing changes for anyone
# who does not ask.
#
# Desktop only (SLOWBOOKS_DESKTOP=1, set by the launcher for the window and
# for Server Edition's --serve-lan, one server process either way). A Docker
# deployment runs two workers, each with its own start, and would sign
# people out at random.
# ---------------------------------------------------------------------------

BOOT_ID = secrets.token_hex(16)
SESSION_BOOT_KEY = "boot"
ASK_ON_START_KEY = "ask_password_on_start"


def remember_this_start(session: dict) -> None:
    """Called where a session is signed in."""
    session[SESSION_BOOT_KEY] = BOOT_ID


def _asks_for_password_on_start() -> bool:
    # Looked up on the module at call time: the test harness and the
    # launcher both repoint app.database.SessionLocal.
    import app.database as database

    try:
        db = database.SessionLocal()
        try:
            return get_setting_raw(db, ASK_ON_START_KEY) == "true"
        finally:
            db.close()
    except Exception:
        logger.exception("could not read %s; keeping the session", ASK_ON_START_KEY)
        return False


def signed_in_before_this_start(session: dict) -> bool:
    """True when a signed-in session dates from an earlier start of the app
    and the company asks for the password each time it starts: the caller
    treats it as signed out. A session found current is marked with this
    start, so the setting is read once per session per start, not on every
    request."""
    if session.get(SESSION_BOOT_KEY) == BOOT_ID:
        return False
    if os.environ.get("SLOWBOOKS_DESKTOP") == "1" and _asks_for_password_on_start():
        return True
    remember_this_start(session)
    return False
