# ============================================================================
# API token service — issue, resolve, and revoke bearer tokens.
#
# resolve() is called from the session middleware for requests carrying
# `Authorization: Bearer sbp_...` and no authenticated session. It opens
# its own short-lived DB session (middleware runs before dependencies).
# ============================================================================

import hashlib
import secrets
from datetime import date, datetime, timedelta, timezone

from app.models.api_tokens import TOKEN_PREFIX, ApiToken
from app.models.users import ROLE_ADMIN
from app.models.users import VALID_ROLES  # noqa: F401 — re-exported for routes

# last_used_at is best-effort telemetry; throttle writes so a chatty agent
# doesn't turn every GET into a DB write.
_LAST_USED_RESOLUTION = timedelta(minutes=5)


# An administrator's token from before 2.29.0 (#659): refused at the door.
ADMIN_TOKEN_REFUSAL = (
    'The API token "{label}" was made as an administrator, and API tokens '
    "can't be administrators any more. Revoke it under Settings, API Tokens, "
    "and make a bookkeeper, accountant or read-only token for the program "
    "that uses it (tick Can make backups if it makes backups). Settings, "
    "payroll and HR need an administrator signed in."
)


# The label the AI assistant connection gives the tokens it makes in each
# company ("AI assistant (relay)", then " 2", " 3" when one is taken). Kept
# here so the gate and the tokens page can know such a token without
# importing the connection; app.services.switchboard_link.TOKEN_LABEL is the
# same text.
LINK_TOKEN_LABEL = "AI assistant (relay)"


def is_link_label(label: str | None) -> bool:
    """A label the AI assistant connection's own tokens carry."""
    return isinstance(label, str) and label.startswith(LINK_TOKEN_LABEL)


def looks_like_link_label(label: str | None) -> bool:
    """A label a token made or renamed by hand may not take: the
    connection's own, in any letter case (the connection finds its tokens
    with a database LIKE, which ignores case on SQLite)."""
    return isinstance(label, str) and label.strip().lower().startswith(
        LINK_TOKEN_LABEL.lower()
    )


def _maker_still_admin(db, created_by: str | None) -> bool:
    """The person who made a connection token is still an administrator of
    this company: their user row is there, active, not past its end date,
    and an administrator's. The way a company ends someone's access is to
    deactivate or demote them, and the connection's token goes with them.
    Checked on every request, so a restored backup or an edited row counts
    as much as a change made under Settings, Users.

    A company with no user rows at all is a single-operator company from
    before sign-ins had names; its operator ("operator") is its
    administrator by definition, as the gate treats such a sign-in."""
    from app.models.users import User

    username = (created_by or "").strip().lower()
    if not username:
        return False
    user = db.query(User).filter(User.username == username).first()
    if user is None:
        return username == "operator" and db.query(User.id).first() is None
    if not user.is_active or user.role != ROLE_ADMIN:
        return False
    expires_on = getattr(user, "expires_on", None)
    return expires_on is None or date.today() <= expires_on


def generate_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def resolve(bearer: str) -> dict | None:
    """Bearer secret → {"label", "role", "allow_backups", "allow_payroll",
    "allow_imports"} for an active token; {"revoked":
    True, "label"} for one an administrator turned off (so the 401 can say
    so, #434), or for an AI assistant connection token whose maker is no
    longer an administrator here; None for a secret that matches nothing."""
    if not bearer or not bearer.startswith(TOKEN_PREFIX):
        return None
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        row = (
            db.query(ApiToken).filter(ApiToken.token_hash == hash_token(bearer)).first()
        )
        if row is None:
            return None
        if not row.is_active:
            return {"revoked": True, "label": row.label}
        if is_link_label(row.label) and not _maker_still_admin(db, row.created_by):
            # the AI assistant connection's token works only while the
            # administrator who added this company to it still is one here
            return {"revoked": True, "label": row.label}
        now = datetime.now(timezone.utc)
        last = row.last_used_at
        if last is not None and last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        # An admin-role token is refused at the door (#659): "last used"
        # would read as if it got in, so it is not stamped.
        if row.role != ROLE_ADMIN and (
            last is None or (now - last) > _LAST_USED_RESOLUTION
        ):
            row.last_used_at = now
            db.commit()
        return {
            "label": row.label,
            "role": row.role,
            "allow_backups": bool(row.allow_backups),
            "allow_payroll": bool(row.allow_payroll),
            "allow_imports": bool(row.allow_imports),
        }
    finally:
        db.close()
