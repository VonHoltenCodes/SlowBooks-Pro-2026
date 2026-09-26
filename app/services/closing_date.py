# ============================================================================
# Closing Date Enforcement — prevent modifications before closing date
# Feature 10: Configurable closing date with optional password override
# ============================================================================

import hmac
import logging
import threading
import time
from datetime import date
from urllib.parse import unquote

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.settings import Settings
from app.services.crypto import decrypt_value
from app.services.request_context import closing_date_password

logger = logging.getLogger(__name__)

# The override password travels with the one request it is meant for, in a
# header, percent-encoded (UTF-8) so any character survives. Only a signed-in
# person's request carries it through (get_db / the request middleware);
# an API token's never does — agents must not hold the override.
PASSWORD_HEADER = "X-Closing-Date-Password"
# Sent back on a refusal when an override password is set, so the page knows
# to ask for it: "password" (none given) or "wrong-password".
OVERRIDE_HEADER = "X-Closing-Date-Override"
# Session.info key get_db stamps the supplied password under.
SESSION_INFO_KEY = "closing_date_password"

_MAX_PASSWORD_LEN = 1024

# Wrong override passwords are limited, so a signed-in person can't try one
# guess after another until the lock opens: after WRONG_LIMIT wrong
# passwords within WRONG_WINDOW seconds the override is refused for
# LOCK_SECONDS, the right password included. Kept per company file, in this
# process (the desktop app and Server Edition run one); a right password
# clears the count.
WRONG_LIMIT = 5
WRONG_WINDOW = 600
LOCK_SECONDS = 600
_wrong: dict[str, list[float]] = {}
_locked_until: dict[str, float] = {}
_guard = threading.Lock()


def _company_key(db: Session) -> str:
    try:
        return str(db.get_bind().url)
    except Exception:
        return "default"


def _lock_remaining(key: str, now: float) -> float:
    with _guard:
        until = _locked_until.get(key, 0.0)
        if until <= now:
            _locked_until.pop(key, None)
            return 0.0
        return until - now


def _note_wrong(key: str, now: float) -> None:
    with _guard:
        recent = [t for t in _wrong.get(key, []) if now - t < WRONG_WINDOW]
        recent.append(now)
        if len(recent) >= WRONG_LIMIT:
            _locked_until[key] = now + LOCK_SECONDS
            recent = []
        _wrong[key] = recent


def _clear_wrong(key: str) -> None:
    with _guard:
        _wrong.pop(key, None)
        _locked_until.pop(key, None)


def reset_override_attempts() -> None:
    """Forget every count and lock (tests; a restart does the same)."""
    with _guard:
        _wrong.clear()
        _locked_until.clear()


def password_from_header(raw: str | None) -> str | None:
    """The password a request carried in PASSWORD_HEADER, decoded."""
    if not raw or len(raw) > 3 * _MAX_PASSWORD_LEN:
        return None
    value = unquote(raw)
    return value if 0 < len(value) <= _MAX_PASSWORD_LEN else None


def get_closing_date(db: Session) -> date | None:
    """Get the configured closing date, or None if not set."""
    row = db.query(Settings).filter(Settings.key == "closing_date").first()
    if row and row.value:
        try:
            return date.fromisoformat(row.value)
        except ValueError:
            return None
    return None


def _supplied_password(db: Session) -> str | None:
    """The override password sent with the current request, if any: stamped
    on the Session by get_db (the path that holds on every runtime), or the
    request contextvar for a session opened outside get_db."""
    info = getattr(db, "info", None)
    if isinstance(info, dict) and info.get(SESSION_INFO_KEY):
        return info[SESSION_INFO_KEY]
    return closing_date_password.get()


def check_closing_date(db: Session, txn_date: date, password: str = None):
    """Raise HTTPException if txn_date is on or before the closing date.
    If a closing_date_password is set and the caller provides it (as the
    argument, or as the signed-in person's PASSWORD_HEADER on this request),
    allow the change and record that the lock was overridden."""
    closing = get_closing_date(db)
    if closing is None:
        return  # No closing date configured

    if txn_date <= closing:
        pw_row = (
            db.query(Settings).filter(Settings.key == "closing_date_password").first()
        )
        stored = ""
        if pw_row and pw_row.value:
            try:
                stored = decrypt_value(pw_row.value)
            except Exception:
                # Unreadable (the settings key changed): no override is
                # possible, and the refusal must still be a refusal, not a 500.
                stored = ""
        if password is None:
            password = _supplied_password(db)
        detail = (
            f"Transaction date {txn_date} is on or before the closing date "
            f"({closing}). Modifications to closed periods are not allowed."
        )
        key = _company_key(db)
        now = time.monotonic()
        if stored and password:
            remaining = _lock_remaining(key, now)
            if remaining:
                minutes = max(1, int(-(-remaining // 60)))
                raise HTTPException(
                    status_code=403,
                    detail=(
                        f"{detail} Too many wrong closing-date passwords were "
                        f"entered; try again in {minutes} minute"
                        f"{'' if minutes == 1 else 's'}."
                    ),
                    headers={OVERRIDE_HEADER: "locked"},
                )
        # compare bytes: compare_digest refuses a str with non-ASCII characters
        if (
            stored
            and password
            and hmac.compare_digest(password.encode("utf-8"), stored.encode("utf-8"))
        ):
            _clear_wrong(key)
            _record_override(db, closing, txn_date)
            return  # Password override accepted
        headers = None
        if stored:
            if password:
                _note_wrong(key, now)
                # Never log or echo the value, only that it did not match.
                logger.warning("closing-date override refused: wrong password")
                detail += " The closing-date password you entered is not correct."
                headers = {OVERRIDE_HEADER: "wrong-password"}
            else:
                detail += " Enter the closing-date password to make this change."
                headers = {OVERRIDE_HEADER: "password"}
        raise HTTPException(status_code=403, detail=detail, headers=headers)


def _record_override(db: Session, closing: date, txn_date: date) -> None:
    """One audit row per request that used the override (the rows it
    changed are audited as usual), written in the request's own transaction
    so a change that fails leaves no claim that it happened."""
    info = getattr(db, "info", None)
    if isinstance(info, dict):
        if info.get("_closing_override_recorded"):
            return
        info["_closing_override_recorded"] = True
    logger.info(
        "closing-date override used: a %s change inside a period closed through %s",
        txn_date,
        closing,
    )
    try:
        from app.services.audit import log_event

        log_event(
            db,
            table_name="closing_date",
            record_id=0,
            action="OVERRIDE",
            new_values={
                "closing_date": closing.isoformat(),
                "transaction_date": txn_date.isoformat(),
            },
            source="closing_date_override",
        )
    except Exception:
        logger.exception("could not record the closing-date override")
