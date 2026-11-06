# ============================================================================
# Slowbooks Pro 2026 — Auth routes
#
#   GET  /api/auth/status  → {setup_needed, authenticated, multi_user, desktop, user?}
#   POST /api/auth/setup   → first-time password set (409 if already set)
#   POST /api/auth/login   → password (+ username once 2+ users exist)
#   POST /api/auth/logout  → clear session
#   POST /api/auth/company → work in another company this server serves (#358)
#
# These routes are deliberately NOT protected by require_auth — they're
# how you become authenticated in the first place.
# ============================================================================

import hashlib
import ipaddress
import logging
from datetime import date
import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import Field
from app.schemas.common import StrictModel
from sqlalchemy.orm import Session

from app.database import get_db, use_company
from app.models.auth import LoginAttempt
from app.services import served_companies
from app.services.auth import (
    SESSION_USER_ID_KEY,
    SESSION_VERSION_KEY,
    authenticate,
    bump_session_version,
    check_session_user,
    ensure_admin_user,
    is_multi_user,
    password_is_set,
    remember_this_start,
    set_password,
    remember_this_company,
    signed_in_before_this_start,
    signed_in_to_another_company,
)
from app.services.rate_limit import limiter
from app.services.request_utils import client_ip as _client_ip
from app.services.settings_service import (
    SettingValueError,
    clean_setting_value,
    set_setting,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])
logger = logging.getLogger(__name__)


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
    # Encrypt this company file now (#290): a desktop makes a recovery key,
    # shown once in the answer, and wraps the key for this password
    encrypt: bool = False

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
    # Legal entity type and an LLC's tax election (#488): chosen here, before
    # the first posting, the equity accounts follow it (entity_equity.py).
    entity_type: Optional[str] = Field(None, max_length=30)
    llc_tax_election: Optional[str] = Field(None, max_length=30)

    # Operator
    operator_name: Optional[str] = Field(None, max_length=200)
    operator_email: Optional[str] = Field(None, max_length=200)

    # Defaults applied to new invoices
    default_terms: Optional[str] = Field(None, max_length=100)
    default_tax_rate: Optional[str] = Field(None, max_length=20)

    # Whether the chart uses account numbers (#567): "true" | "false";
    # left out, the default (on) stands
    use_account_numbers: Optional[str] = Field(None, max_length=5)


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
    "entity_type",
    "llc_tax_election",
    "operator_name",
    "operator_email",
    "default_terms",
    "default_tax_rate",
    "use_account_numbers",
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


def _desktop_window(request: Request) -> bool:
    """True when the request comes from the desktop app's own window: the
    launcher's flag, not the windowless Server Edition (--serve-lan), and a
    request from this machine, as app.main decides for its CSP.

    The sign-in screen offers "Choose a different company →" by it. It used
    to wait for the launcher's bridge, which macOS injects after the page
    has loaded: the screen the app starts on never had the link (2.18.0
    gate, macbase1 NEW-8)."""
    if os.environ.get("SLOWBOOKS_DESKTOP") != "1":
        return False
    if os.environ.get("SLOWBOOKS_SERVER_MODE") == "1":
        return False
    host = request.client.host if request.client else ""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@router.get("/status")
def auth_status(request: Request):
    """Tell the SPA whether first-run setup is needed and whether the
    current session is authenticated. When the session's company cannot be
    opened now (#358: its file missing, locked or from another version),
    the answer says so and still lists the companies this server serves,
    so the sign-in screen keeps its choice."""
    import app.database as db_module

    try:
        db = db_module.SessionLocal()
    except served_companies.CompanyUnavailable as exc:
        return _status_while_company_unavailable(
            request, served_companies.sentence_of(exc)
        )
    try:
        return _auth_status(request, db)
    finally:
        db.close()


def _status_while_company_unavailable(request: Request, sentence: str) -> dict:
    return {
        "authenticated": False,
        "setup_needed": False,
        "multi_user": False,
        "usernames": [],
        "company_name": "",
        "accountants_copy": None,
        "desktop": _desktop_window(request),
        "company_unavailable": sentence,
        "companies": [
            {**c, "opens": False} for c in served_companies.names_for_sign_in()
        ],
    }


def _auth_status(request: Request, db: Session) -> dict:
    authenticated = request.session.get("authenticated") is True
    if authenticated and (
        signed_in_before_this_start(request.session)
        or signed_in_to_another_company(request.session)
    ):
        # "Ask for the password each time SlowBooks Pro starts": a session
        # from before this start is signed out here too, or the page would
        # be told it is signed in while every request answers 401.
        request.session.clear()
        authenticated = False
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
    # an accountant's copy says so on every page (#346)
    from app.services.accountants_copy import mode_of

    copy = mode_of(db)
    out["accountants_copy"] = (
        {k: v for k, v in copy.items() if k != "token"} if copy else None
    )
    out["desktop"] = _desktop_window(request)
    if served_companies.enabled():
        # Several companies on this server (#358): the sign-in screen asks
        # which, and the header offers the others. "opens" is whether this
        # sign-in's password opened that company too, so a switch is a click.
        opens = request.session.get(served_companies.SESSION_OPENS_KEY) or []
        out["companies"] = [
            {**c, "opens": authenticated and c["file"] in opens}
            for c in served_companies.names_for_sign_in()
        ]
    if setup_needed:
        from app.models.transactions import Transaction

        out["has_data"] = db.query(Transaction.id).first() is not None
    elif not authenticated:
        # "Forgot your password?" (#719): what to do for this company
        out["forgot"] = _forgot_password(request, db)
    if authenticated and request.session.get("username"):
        out["user"] = {
            "username": request.session.get("username"),
            "display_name": request.session.get("display_name") or "",
            "role": request.session.get("role") or "admin",
            "must_change_password": bool(request.session.get("must_change_password")),
        }
    return out


# ---------------------------------------------------------------------------
# Forgot your password? (#719)
#
# Server Edition, a browser, and any company with another administrator:
# ask an administrator (Settings, Users, Reset password). A desktop file
# encrypted under a recovery key: open it with the key, then set a new
# administrator password. A desktop file that is not encrypted: set one at
# the computer, where anyone who can sign in can already read the file
# without the app. Only the desktop app's own window can make either reset:
# the launcher sends it with this run's window secret (desktop_window.py).
# ---------------------------------------------------------------------------

FORGOT_ASK_ADMIN = (
    "Ask an administrator to reset it under Settings, Users. They give you a "
    "one-time password to sign in with, and you choose a new one then."
)
FORGOT_ASK_ANOTHER = "Ask another administrator to reset it under Settings, Users."
FORGOT_RECOVERY = (
    "This company file is encrypted. Close the company and open it with its "
    "recovery key: choose a different company, open this one, and choose "
    "Use the recovery key instead. You can then set a new administrator "
    "password."
)
FORGOT_RECOVERY_OPENED = (
    "You opened this company with its recovery key, so you can set a new "
    "password for one of its administrators now."
)
FORGOT_HERE = (
    "This company file is on this computer and is not encrypted, so you can "
    "set a new administrator password here."
)
FORGOT_HERE_OR_ASK = (
    "You can also set a new administrator password here, because this "
    "company file is on this computer and is not encrypted."
)
WINDOW_ONLY_MESSAGE = (
    "A new administrator password can be set only in the SlowBooks Pro "
    "window, on the computer that holds the company file."
)
# What a browser on the same computer is told where the window would be
# offered the reset (W-125): the status cannot tell the app's own window from
# a browser at its address, so the page offers the reset only when it has the
# launcher's bridge, and says this when it has not.
FORGOT_ELSEWHERE = (
    WINDOW_ONLY_MESSAGE
    + " Open the company in the app there and choose Forgot your password?"
)
ENCRYPTED_NEEDS_KEY_MESSAGE = (
    "This company file is encrypted. Close the company and open it with its "
    "recovery key to set a new administrator password."
)
CHOOSE_ADMIN_MESSAGE = "Choose which administrator gets the new password."
NOT_AN_ADMIN_MESSAGE = "That is not an administrator of this company."

RESET_BY_RECOVERY_KEY = "recovery key"
RESET_AT_COMPUTER = "at the computer"


def _active_admins(db: Session) -> list:
    from app.models.users import ROLE_ADMIN, User

    return (
        db.query(User)
        .filter(User.role == ROLE_ADMIN, User.is_active.is_(True))
        .order_by(User.username)
        .all()
    )


def _window_may_reset(request: Request) -> bool:
    """The page is the desktop app's own window, started by the launcher
    for it: the only place the reset is offered. The reset route itself
    asks for more (the launcher's secret, _from_the_window)."""
    from app.services import company_file, desktop_window

    return (
        _desktop_window(request)
        and desktop_window.has_secret()
        and not served_companies.enabled()
        and not company_file.serving_another_company()
    )


def _from_the_window(request: Request) -> bool:
    """A request the launcher made for its window: the desktop's loopback
    window as the sign-in screen tells it, this run's window secret, and no
    API token. Everything else is refused."""
    from app.services import desktop_window

    if request.headers.get("authorization"):
        return False
    return _window_may_reset(request) and desktop_window.secret_matches(request.headers)


def _forgot_password(request: Request, db: Session) -> dict:
    """The sign-in page's "Forgot your password?": the sentence for this
    company, and whether the app's own window can set a new password here.

    Anyone at the app's address on this computer reads this, a browser
    included, so it names no administrator (W-125): the window asks for the
    names through the launcher, with its secret (GET reset-at-computer).
    Where a reset is possible the answer carries two sentences: `message`
    for the window, and `elsewhere` for a page that has no bridge."""
    from app.services import company_file, company_keyring, desktop_window

    ask = {"kind": "ask_admin", "message": FORGOT_ASK_ADMIN}
    if not _window_may_reset(request):
        return ask
    path = company_file.active_path()
    if path is None:
        return ask
    several = len(_active_admins(db)) > 1
    if company_keyring.exists(path):
        if desktop_window.opened_with_recovery_key():
            return {
                "kind": "recovery_reset",
                "message": FORGOT_RECOVERY_OPENED,
                "elsewhere": FORGOT_ELSEWHERE,
            }
        return {
            "kind": "recovery",
            "message": (FORGOT_ASK_ANOTHER + " " if several else "") + FORGOT_RECOVERY,
        }
    if company_file.is_encrypted_file(path):
        # keyed by an operator secret, with no recovery key: an administrator
        return ask
    return {
        "kind": "reset_here",
        "message": (
            FORGOT_ASK_ANOTHER + " " + FORGOT_HERE_OR_ASK if several else FORGOT_HERE
        ),
        "elsewhere": (FORGOT_ASK_ANOTHER + " " if several else "") + FORGOT_ELSEWHERE,
    }


@router.get("/reset-at-computer")
@limiter.limit("30/minute")
def reset_at_computer_options(request: Request, db: Session = Depends(get_db)):
    """Whose password the window may set (W-125): the administrators' names,
    for the reset form's choice. The launcher asks with its window secret, as
    it sends the reset itself; a browser, a token and a server install are
    refused, so the names are not read from the sign-in page's address."""
    if not _from_the_window(request):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=WINDOW_ONLY_MESSAGE
        )
    return {
        "admins": [u.username for u in _active_admins(db)],
        "multi_user": is_multi_user(db),
    }


class ResetAtComputerPayload(StrictModel):
    # which administrator; may be left out when there is one
    username: Optional[str] = Field(None, max_length=100)
    new_password: str = Field(..., min_length=1, max_length=512)
    # the recovery key the picker opened the company with, sent on by the
    # launcher; required when the file is encrypted under one
    recovery_key: Optional[str] = Field(None, max_length=200)


@router.post("/reset-at-computer")
@limiter.limit("5/minute")
def reset_at_computer(
    request: Request, payload: ResetAtComputerPayload, db: Session = Depends(get_db)
):
    """Set a new administrator password when it is forgotten (#719): from
    the desktop app's own window alone (the launcher sends this with its
    window secret; a browser, an API token, Server Edition and a windowless
    run are refused), with the recovery key when the file is encrypted
    under one, else proven by being at the computer that holds the file.
    The administrator's other sessions end, the reset is in the audit log
    by how it was made, and the page signs in with the new password."""
    import hmac

    from app.models.users import User
    from app.services import company_file, company_keyring
    from app.services.audit import log_event
    from app.services.auth import AUTH_PASSWORD_KEY, MIN_PASSWORD_LEN, hash_password

    if not _from_the_window(request):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=WINDOW_ONLY_MESSAGE
        )
    if not password_is_set(db):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Setup required — set a password first",
        )
    path = company_file.active_path()
    if path is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=WINDOW_ONLY_MESSAGE
        )
    if len(payload.new_password) < MIN_PASSWORD_LEN:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Password must be at least {MIN_PASSWORD_LEN} characters",
        )

    # How it is proven: the recovery key for a file encrypted under one (it
    # must derive the key this server has the file open with), being at the
    # computer for a file that is not encrypted at all.
    key = None
    if company_keyring.exists(path):
        running = company_file.resolve_key()
        typed = payload.recovery_key or ""
        if not typed.strip():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=ENCRYPTED_NEEDS_KEY_MESSAGE,
            )
        if not company_keyring.is_recovery_key(typed) or running is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=company_keyring.WRONG_RECOVERY_MESSAGE,
            )
        key = company_keyring.key_from_recovery(typed)
        if not hmac.compare_digest(key, running):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=company_keyring.WRONG_RECOVERY_MESSAGE,
            )
        how = RESET_BY_RECOVERY_KEY
    elif company_file.is_encrypted_file(path):
        # an operator secret keys it, and no recovery key exists: an
        # administrator resets it under Settings, Users
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=FORGOT_ASK_ADMIN
        )
    else:
        how = RESET_AT_COMPUTER

    ensure_admin_user(db)  # a legacy book: its operator as the admin row
    admins = _active_admins(db)
    wanted = (payload.username or "").strip().lower()
    if wanted:
        user = next((u for u in admins if u.username == wanted), None)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=NOT_AN_ADMIN_MESSAGE
            )
    elif len(admins) == 1:
        user = admins[0]
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=CHOOSE_ADMIN_MESSAGE
        )

    db.info["acting_username"] = user.username
    new_hash = hash_password(payload.new_password)
    user.password_hash = new_hash
    user.must_change_password = False
    if db.query(User).count() == 1:
        # single-operator: the settings hash is the same password
        set_setting(db, AUTH_PASSWORD_KEY, new_hash)
    # every session this administrator has ends, as at a reset under Users
    bump_session_version(user)
    log_event(
        db,
        table_name="users",
        record_id=user.id,
        action="RESET",
        new_values={
            "username": user.username,
            "password_reset": (
                "by the recovery key"
                if how == RESET_BY_RECOVERY_KEY
                else "at the computer that holds the company file"
            ),
        },
        changed_fields=["password_hash"],
        source=(
            "password reset by recovery key"
            if how == RESET_BY_RECOVERY_KEY
            else "password reset at the computer"
        ),
    )
    db.commit()
    if key is not None:
        # the key wrapped for the new password, so the picker opens the file
        # with it; the recovery key is untouched and still opens it too
        ring = company_keyring.load(path)
        if ring is not None:
            company_keyring.set_admin(ring, user.username, payload.new_password, key)
            company_keyring.save(path, ring)
    logger.info("administrator password for %s reset (%s)", user.username, how)
    return {
        "status": "ok",
        "username": user.username,
        "multi_user": is_multi_user(db),
        "how": how,
    }


class NewPasswordPayload(StrictModel):
    new_password: str = Field(..., min_length=1, max_length=512)
    # the password in use; not asked of a sign-in that must replace its
    # one-time password, which proved it moments ago
    current_password: Optional[str] = Field(None, max_length=512)


@router.post("/password")
@limiter.limit("5/minute")
def set_own_password(
    request: Request, payload: NewPasswordPayload, db: Session = Depends(get_db)
):
    """The signed-in person sets their own password (#345): an invited
    accountant replacing the one-time password at the first sign-in, or
    anyone changing theirs, with the current one to prove it."""
    from app.models.users import User
    from app.services.auth import MIN_PASSWORD_LEN, hash_password, verify_password

    user_id = request.session.get("user_id")
    user = db.get(User, user_id) if user_id else None
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Sign in with a username to set a password of your own.",
        )
    if len(payload.new_password) < MIN_PASSWORD_LEN:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Password must be at least {MIN_PASSWORD_LEN} characters",
        )
    must_change = bool(request.session.get("must_change_password"))
    if not must_change:
        if not payload.current_password or not verify_password(
            payload.current_password, user.password_hash
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="The current password is not right.",
            )
    user.password_hash = hash_password(payload.new_password)
    user.must_change_password = False
    # revocable sessions: a changed password ends this user's OTHER
    # sessions (a copied cookie elsewhere) — but not the one making the
    # change, which re-stores the new version below so it stays signed in.
    from app.services.auth import bump_session_version

    bump_session_version(user)
    db.commit()
    request.session["must_change_password"] = False
    request.session["session_version"] = user.session_version
    _keep_keyring_current(user, payload.new_password, force=True)
    return {"status": "ok"}


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
    chosen = served_companies.chosen_file(request.session)
    if chosen is not None and request.session.get(SET_UP_KEY) != chosen:
        # Another company on this server with no password yet (#358): made
        # under Companies, and set up by the administrator who opens it from
        # there, not by whoever on the network picks it first.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=NOT_SET_UP_MESSAGE,
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
            if key == "use_account_numbers" and value not in ("true", "false"):
                raise HTTPException(
                    status_code=422,
                    detail="use_account_numbers must be one of: false, true",
                )
            try:
                to_store[key] = clean_setting_value(key, value)
            except SettingValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from None
    # The entity enums are the Settings page's (one list, routes/settings);
    # an election only means something for an LLC.
    from app.routes.settings import ENUM_SETTINGS

    for key in ("entity_type", "llc_tax_election"):
        if key in to_store and to_store[key] not in ENUM_SETTINGS[key]:
            raise HTTPException(
                status_code=422,
                detail=f"{key} must be one of: "
                + ", ".join(sorted(x for x in ENUM_SETTINGS[key] if x)),
            )
    if to_store.get("entity_type") != "llc":
        to_store.pop("llc_tax_election", None)
    for key, value in to_store.items():
        set_setting(db, key, value)
    if "entity_type" in to_store:
        from app.services.entity_equity import apply_entity_equity
        from app.services.settings_service import entity_info, get_all_settings

        db.flush()
        apply_entity_equity(db, entity_info(get_all_settings(db)))

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
    remember_this_start(request.session)
    remember_this_company(request.session)
    _stash_user(request, admin)
    _note_sign_in(request.session, chosen, admin)
    if served_companies.enabled() and admin is not None:
        # a company set up from another one's sign-in (#358): the way back
        # is a click when the password is the same there
        _remember_opened(
            request.session, admin, payload.password, served_companies.current_file()
        )
    out = {"status": "ok", "authenticated": True}
    if payload.encrypt:
        out.update(_encrypt_at_setup(db, admin, payload.password, payload.company_name))
    return out


def _encrypt_at_setup(db: Session, admin, password: str, company_name) -> dict:
    """The wizard's "encrypt this company file" (#290): on a desktop, a
    recovery key is made and shown once; with an operator secret set, the
    file is encrypted under it; where neither is possible, the books are
    set up plain and the answer says so, rather than refusing the setup
    that was just saved."""
    from app.routes.company_file import turn_on_with_recovery_key
    from app.services import company_file

    state = company_file.status()
    if not state["supported"] or state["encrypted"]:
        return {"encrypted": state["encrypted"], "encryption_note": None}
    path = company_file.active_path()
    try:
        if state["key_source"] in ("secret", "launcher"):
            key = company_file.resolve_key()
            import app.database as db_module

            db.close()
            db_module.current_engine().dispose()
            try:
                company_file.convert(path, key, encrypt=True)
            finally:
                db_module.current_engine().dispose()
            return {"encrypted": True, "encryption_note": None}
        if (
            os.environ.get("SLOWBOOKS_DESKTOP") != "1"
            or company_file.serving_another_company()
        ):
            # the desktop's own key is the company the window was started
            # for; another company a server serves is keyed by the host's
            # secret alone (2.25.0 review: the wizard read the raw flag,
            # which --serve-lan sets too, and would have put the new
            # company's key where the started company's belongs)
            return {
                "encrypted": False,
                "encryption_note": (
                    "The books are set up plain: set COMPANY_FILE_ENCRYPTION_SECRET "
                    "where SlowBooks Pro starts, then turn encryption on under "
                    "Settings."
                ),
            }
        from app.services import company_keyring

        recovery = turn_on_with_recovery_key(
            db,
            path,
            username=admin.username,
            password=password,
            company_name=company_name or "",
        )
        return {
            "encrypted": True,
            "recovery_key": recovery,
            "recovery_file": company_keyring.recovery_file_text(
                company_name or "", recovery
            ),
        }
    except HTTPException as exc:
        return {"encrypted": False, "encryption_note": str(exc.detail)}


def _keep_keyring_current(user, password: str, *, force: bool = False) -> None:
    """An administrator's sign-in, or new password, keeps their wrapping of
    the company file's key current (#290): the running process has the key;
    a one-time password is never wrapped (it is replaced in a moment)."""
    from app.models.users import ROLE_ADMIN
    from app.services import company_file, company_keyring

    if (
        user is None
        or user.role != ROLE_ADMIN
        or getattr(user, "must_change_password", False)
    ):
        return
    path = company_file.active_path()
    if path is None or not company_keyring.exists(path):
        return
    try:
        company_keyring.ensure_wrapped(path, user.username, password, force=force)
    except Exception:  # pragma: no cover - never let the keyring break a sign-in
        import logging

        logging.getLogger(__name__).exception("could not update the keyring")


def _note_sign_in(session, company_file, user) -> None:
    """Tell the scheduler a person signed in, when the sign-in is to the
    company this server was started for (company_file None, or its own
    file): the app's own run of the schedules waits for one (2.31.1 gate
    W-70). A one-time password not yet replaced is not a sign-in yet."""
    from app.services import scheduler

    if company_file is not None and company_file != served_companies.started_file():
        return
    if session.get("must_change_password"):
        return
    scheduler.note_sign_in(
        getattr(user, "username", None) or session.get("username") or "operator",
        getattr(user, "role", None) or session.get("role"),
    )


def _stash_user(request: Request, user) -> None:
    """Record the acting principal in the session (None = legacy no-row)."""
    if user is None:
        return
    request.session["user_id"] = user.id
    request.session["username"] = user.username
    request.session["display_name"] = user.display_name
    request.session["role"] = user.role
    # a one-time password must be replaced before anything else (#345)
    request.session["must_change_password"] = bool(
        getattr(user, "must_change_password", False)
    )
    # revocable sessions: the version this sign-in is good for; a
    # later bump (deactivate, role or password change) ends it
    request.session["session_version"] = getattr(user, "session_version", 0) or 0


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
    if user.expires_on is not None and date.today() > user.expires_on:
        # an invited sign-in past its end date (#345): refused with the day
        _record_login_attempt(db, request, success=False)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"This sign-in ended on {user.expires_on:%B %d, %Y}. An "
                "administrator can extend it under Settings → Users."
            ),
        )
    _record_login_attempt(db, request, success=True)
    # Same rotation rationale as /setup.
    company = served_companies.chosen_file(request.session)
    request.session.clear()
    request.session["authenticated"] = True
    remember_this_start(request.session)
    remember_this_company(request.session)
    _stash_user(request, user)
    _note_sign_in(request.session, company, user)
    _keep_keyring_current(user, payload.password)
    if served_companies.enabled():
        _remember_opened(
            request.session, user, payload.password, served_companies.current_file()
        )
    return {"status": "ok", "authenticated": True}


def _user_who_may_open(db: Session, username: str):
    """The active user by this name in this company, if their sign-in has
    not ended; else None."""
    from app.models.users import User

    user = (
        db.query(User)
        .filter(User.username == (username or "").strip().lower(), User.is_active)
        .first()
    )
    if user is None:
        return None
    if user.expires_on is not None and date.today() > user.expires_on:
        return None
    return user


def _fingerprint(password_hash: str) -> str:
    """What a session remembers of another company's sign-in: enough to tell
    at a switch that the password was changed or reset there since, and
    nothing of the hash itself (the session cookie is signed, not hidden)."""
    return hashlib.sha256((password_hash or "").encode("utf-8")).hexdigest()[:16]


def _remember_opened(session, user, password: str, here: str) -> None:
    """The companies this sign-in's password opens, this one included, with
    each one's fingerprint (#358), so a switch to any of them, and back,
    can tell the password is still the one that was given."""
    opened = {here: _fingerprint(user.password_hash)}
    opened.update(_other_companies_opened_by(user.username, password, here))
    session[served_companies.SESSION_FILE_KEY] = here
    session[served_companies.SESSION_OPENS_KEY] = list(opened)
    session[served_companies.SESSION_OPENS_FP_KEY] = opened


def _other_companies_opened_by(username: str, password: str, here: str) -> dict:
    """The other companies this server serves where the same username, with
    the same password, is an active sign-in (#358): {file: fingerprint}.
    Checked at the sign-in, and again at each switch, which asks for the
    password instead when the fingerprint no longer matches."""
    from app.services.auth import verify_password

    found = {}
    for company in served_companies.listed():
        if company["file"] == here:
            continue
        try:
            with use_company(company["file"]):
                import app.database as db_module

                other = db_module.SessionLocal()
                try:
                    user = _user_who_may_open(other, username)
                    if user is not None and verify_password(
                        password, user.password_hash
                    ):
                        found[company["file"]] = _fingerprint(user.password_hash)
                finally:
                    other.close()
        except Exception:
            # a company that cannot be opened now is simply not offered
            logger.warning("could not check sign-in for %s", company["file"])
    return found


@router.post("/logout")
def logout(request: Request):
    """Clear the session cookie. On a server serving several companies the
    sign-in screen stays in the company signed out of (#358). Sign out bumps
    the user's session version too, so a copy of the cookie taken before it
    does not keep working; a sign-out still succeeds when the books cannot
    be reached, or the session names no user row (a desktop or legacy
    session)."""
    company = request.session.get(served_companies.SESSION_FILE_KEY)
    _end_sessions_of(request.session.get(SESSION_USER_ID_KEY))
    request.session.clear()
    if company and served_companies.enabled():
        request.session[served_companies.SESSION_FILE_KEY] = company
    return {"status": "ok"}


def _end_sessions_of(user_id) -> None:
    """Bump the session version of this user row on the company the request
    works in, ending every session it has. Never fatal: a sign-out must
    succeed whether or not the row, or the database, can be reached."""
    if not user_id:
        return
    try:
        import app.database as db_module

        from app.models.users import User

        db = db_module.SessionLocal()
        try:
            user = db.get(User, user_id)
            if user is not None:
                bump_session_version(user)
                db.commit()
        finally:
            db.close()
    except Exception:
        logger.warning("could not end the other sessions of user %s", user_id)


# A company on the served list that nobody has set up yet is set up by an
# administrator who opened it from a signed-in session (#358).
SET_UP_KEY = "company_file_to_set_up"
NOT_SET_UP_MESSAGE = (
    "This company has not been set up yet. An administrator sets it up: "
    "sign in to another company on this server and open it from Companies."
)


class CompanyChoice(StrictModel):
    file: str = Field(..., min_length=1, max_length=80)


@router.post("/company")
@limiter.limit("30/minute")
def choose_company(request: Request, payload: CompanyChoice):
    """Work in another company this server serves (#358).

    Signed in, and the sign-in's password opened that company too: the
    session moves there, signed in as the same username with the role that
    company gives it, and the company's own log records the sign-in.
    Otherwise the session is signed out and the sign-in screen asks for
    that company's password, the username filled in."""
    if not served_companies.enabled():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="This server serves one company.",
        )
    target = payload.file
    if not served_companies.is_served(target):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=served_companies.NOT_SERVED
        )
    session = request.session
    # Revocable sessions: /api/auth/ is outside the gate that re-checks a
    # session against its user row, so it is re-checked here, against the
    # company the session is in, before the cookie's role opens anything. A
    # session ended since (deactivated, demoted, password reset, sign-in
    # ended) is signed out, as at Sign out, and goes on as not signed in:
    # the target asks for its own password. The role is the row's. A session
    # whose own company cannot be opened now (its file missing or locked)
    # cannot be verified, and counts as signed out the same way: the switch
    # is how the sign-in screen offers the companies that do open.
    try:
        state, db_role, db_version = check_session_user(session)
    except served_companies.CompanyUnavailable:
        state, db_role, db_version = "revoked", None, None
    if state == "revoked":
        here = session.get(served_companies.SESSION_FILE_KEY)
        session.clear()
        if here:
            session[served_companies.SESSION_FILE_KEY] = here
    elif state == "ok":
        session["role"] = db_role
        session[SESSION_VERSION_KEY] = db_version
    username = session.get("username") or ""
    opens = session.get(served_companies.SESSION_OPENS_KEY) or []
    fingerprints = session.get(served_companies.SESSION_OPENS_FP_KEY) or {}
    signed_in = session.get("authenticated") is True
    admin = signed_in and (session.get("role") or "admin") == "admin"
    if admin and not session.get("must_change_password"):
        import app.database as db_module

        with use_company(target):
            db = db_module.SessionLocal()
            try:
                new_company = not password_is_set(db)
            finally:
                db.close()
        if new_company:
            session.clear()
            session[served_companies.SESSION_FILE_KEY] = target
            session[SET_UP_KEY] = target
            return {"status": "ok", "authenticated": False, "setup": True}
    if (
        signed_in
        and target in opens
        and not session.get("must_change_password")
        and username
    ):
        with use_company(target):
            import app.database as db_module

            db = db_module.SessionLocal()
            try:
                user = _user_who_may_open(db, username)
                # the person as they are there now: still active, no
                # one-time password to replace, and the same password as
                # at the sign-in (a reset there since asks for it)
                if (
                    user is not None
                    and not getattr(user, "must_change_password", False)
                    and _fingerprint(user.password_hash) == fingerprints.get(target)
                ):
                    from datetime import datetime, timezone

                    db.info["acting_username"] = user.username
                    user.last_login_at = datetime.now(timezone.utc)
                    db.commit()
                    _record_login_attempt(db, request, success=True)
                    session.clear()
                    session["authenticated"] = True
                    remember_this_start(session)
                    session[served_companies.SESSION_FILE_KEY] = target
                    remember_this_company(session)
                    _stash_user(request, user)
                    _note_sign_in(session, target, user)
                    session[served_companies.SESSION_OPENS_KEY] = opens
                    session[served_companies.SESSION_OPENS_FP_KEY] = fingerprints
                    return {"status": "ok", "authenticated": True}
            finally:
                db.close()
    session.clear()
    session[served_companies.SESSION_FILE_KEY] = target
    return {"status": "ok", "authenticated": False, "username": username}
