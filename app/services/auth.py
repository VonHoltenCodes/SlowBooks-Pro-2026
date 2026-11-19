# ============================================================================
# SlowBooks Pro 2026 — Authentication
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
import string
import threading
import weakref
from datetime import date
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

# A password shown once is read off a screen and typed, often at the same
# computer (the desktop's invitation): letters and digits with no 0/O or
# 1/l/I among them (2.24.0, NEW-167). The accountant's copy's passphrase
# draws on the same alphabet.
LOOKALIKE_CHARS = "0O1lI"
READABLE_ALPHABET = "".join(
    c for c in string.ascii_letters + string.digits if c not in LOOKALIKE_CHARS
)


def one_time_password(length: int = 16) -> str:
    """A password for one sign-in, shown once: 16 characters from the
    readable alphabet (57 symbols, 93 bits), replaced at that sign-in."""
    return "".join(secrets.choice(READABLE_ALPHABET) for _ in range(length))


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
    return _session_secret_from_file(key_path)


def _session_secret_from_file(key_path: Path) -> str:
    """The key in key_path, made there once if it isn't yet.

    Several server processes can start at once (the Docker image runs two
    uvicorn workers) and none finds the file on a first start. Each used to
    write its own key and keep it, so a cookie signed by one worker was
    refused by the other and people were signed out at random until a
    restart. Now the first process to create the file wins (a hard link of
    a finished temp file: atomic, and refused if the file exists), and every
    other process reads that key.
    """
    existing = _read_key(key_path)
    if existing:
        logger.info("session secret loaded from %s", key_path)
        return existing

    new_key = secrets.token_urlsafe(48)
    try:
        import tempfile

        fd, tmp = tempfile.mkstemp(dir=str(key_path.parent), prefix=".session-key-")
        try:
            os.write(fd, new_key.encode())
        finally:
            os.close(fd)
        os.chmod(tmp, 0o600)
        try:
            os.link(tmp, str(key_path))
            logger.info("new session secret written to %s", key_path)
        except FileExistsError:
            pass  # another process made it first: its key is the key
        except OSError:
            # no hard links here (some network or FAT folders): fall back
            # to an exclusive create, which is as atomic for the winner
            try:
                fd2 = os.open(
                    str(key_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
                )
            except FileExistsError:
                pass
            else:
                with os.fdopen(fd2, "w", encoding="utf-8") as fh:
                    fh.write(new_key)
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    except OSError as exc:
        logger.warning("could not persist session secret to %s: %s", key_path, exc)

    # The winner's file is complete when it appears via link; after an
    # O_EXCL create it may be empty for a moment, so wait briefly for it.
    import time

    for _ in range(50):
        existing = _read_key(key_path)
        if existing:
            return existing
        time.sleep(0.02)
    logger.warning(
        "session secret is NOT persisted -- it will be different every "
        "time the server restarts, which logs everyone out without "
        "warning. This should only ever log once, on a truly first-ever "
        "launch; if it keeps appearing on every restart, %s isn't writable.",
        key_path,
    )
    return new_key


def _read_key(key_path: Path) -> str:
    try:
        return key_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""
    except OSError as exc:
        logger.warning("could not read %s: %s", key_path, exc)
        return ""


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


# ---------------------------------------------------------------------------
# Revocable sessions. The signed session cookie is tamper-proof but it
# is not revocable on its own: it carried the role and lived until the idle
# timeout, so deactivating a user, changing their role or resetting their
# password did not end the sessions they already had. Each user row now keeps
# a session_version; a sign-in records it, every request re-reads the row and
# signs the session out when the two differ, and the role is taken from the
# row rather than the cookie. These two keys are what a session stores.
# ---------------------------------------------------------------------------

SESSION_USER_ID_KEY = "user_id"
SESSION_VERSION_KEY = "session_version"


def check_session_user(session: dict):
    """Re-check a signed-in session against its user row. Returns
    (status, role, version):
      ("ok", role, version)   keep the session; use this role, store version
      ("revoked", None, None) the user is gone, inactive, past its end date,
                              or the version was bumped (deactivated / role
                              changed / password changed) — the caller signs
                              the session out
      ("skip", None, None)    the session names no user row (a legacy cookie
                              from before the users table, or a desktop
                              password-only session with no row): nothing to
                              re-check, left as it was

    A read-only point lookup (one row by primary key) on the company this
    request works in — cheap enough to run on every request.
    """
    user_id = session.get(SESSION_USER_ID_KEY)
    if not user_id:
        return ("skip", None, None)
    import app.database as database

    from app.models.users import User

    db = database.SessionLocal()
    try:
        user = db.get(User, user_id)
        if user is None or not user.is_active:
            return ("revoked", None, None)
        # an invited sign-in past its end date (#345): the same day test the
        # login path refuses it by, so an ended sign-in stops answering now
        # rather than at the idle timeout
        expires_on = getattr(user, "expires_on", None)
        if expires_on is not None and date.today() > expires_on:
            return ("revoked", None, None)
        stored = session.get(SESSION_VERSION_KEY)
        # A session from before this column carries no version: adopt the
        # row's current one rather than sign everyone out on the upgrade (the
        # same courtesy signed_in_to_another_company extends to an old cookie
        # with no company). A password change since the upgrade still bumps
        # the row, so the adopted session is caught from then on.
        if stored is not None and stored != user.session_version:
            return ("revoked", None, None)
        return ("ok", user.role, user.session_version)
    finally:
        db.close()


def bump_session_version(user) -> None:
    """End this user's existing sessions: raise the row's version so
    the version each live session stored no longer matches and its next
    request is signed out. Call on deactivation, a role change, a password
    change/reset, an end date set in the past, and sign-out. The caller
    commits; a caller that wants to keep
    the acting session alive re-stores the new value in its own session."""
    if user is None:
        return
    user.session_version = (user.session_version or 0) + 1


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


# ---------------------------------------------------------------------------
# A sign-in belongs to one company (2.18.0 gate, skytech R6-1). Every company
# on an install is served under the same session secret, and a session
# recorded who signed in and with what role, not where: with last_opened
# pointed at another company outside the app, or after a Switch company...
# whose sign-out failed, the next company opened signed in, its own password
# never asked. Each company file now carries an id of its own; a sign-in
# records it, and a session recorded for another company counts as signed
# out. A session from before 2.18.0 carries no company, and belongs to the
# one it is first used on, so an upgrade signs nobody out.
# ---------------------------------------------------------------------------

SESSION_COMPANY_KEY = "company"
COMPANY_SESSION_SETTING = "company_session_id"
# Keyed by the engine itself, held weakly: the entry goes when the engine
# does. Keyed by id(engine), a new engine could reuse a freed one's address
# and inherit its company id — every request then read as "signed in to
# another company" (seen as an order-dependent test failure; a server serves
# one company per process, so only a process that repoints its engine can
# meet it).
_company_ids: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()
_company_ids_lock = threading.Lock()


def _company_id() -> str | None:
    """This company's id, made the first time it is asked for. The id is
    kept for the process, per database (the test harness and the launcher
    repoint app.database.SessionLocal; a Server Edition server serves
    several companies, each on an engine of its own: #358)."""
    import app.database as database

    factory = database.SessionLocal
    if database.served_company() is not None:
        key = database.current_engine()
    else:
        key = getattr(factory, "kw", {}).get("bind") or factory
    company = _company_ids.get(key)
    if company:
        return company
    with _company_ids_lock:
        company = _company_ids.get(key)
        if company:
            return company
        try:
            db = factory()
            try:
                company = get_setting_raw(db, COMPANY_SESSION_SETTING)
                if not company:
                    company = secrets.token_hex(16)
                    set_setting(db, COMPANY_SESSION_SETTING, company)
                    db.commit()
            finally:
                db.close()
        except Exception:
            logger.exception("could not read this company's id; keeping the session")
            return None
        _company_ids[key] = company
        return company


def forget_company_ids() -> None:
    """For tests that serve another company in the same process."""
    _company_ids.clear()


def remember_this_company(session: dict) -> None:
    """Called where a session is signed in."""
    company = _company_id()
    if company:
        session[SESSION_COMPANY_KEY] = company


def signed_in_to_another_company(session: dict) -> bool:
    """True when a signed-in session was signed in to another company: the
    caller treats it as signed out."""
    company = _company_id()
    if company is None:
        return False
    signed_in_to = session.get(SESSION_COMPANY_KEY)
    if signed_in_to is None:
        session[SESSION_COMPANY_KEY] = company
        return False
    return signed_in_to != company
