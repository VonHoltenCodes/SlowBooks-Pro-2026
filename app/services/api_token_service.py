# ============================================================================
# API token service — issue, resolve, and revoke bearer tokens.
#
# resolve() is called from the session middleware for requests carrying
# `Authorization: Bearer sbp_...` and no authenticated session. It opens
# its own short-lived DB session (middleware runs before dependencies).
# ============================================================================

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from app.models.api_tokens import TOKEN_PREFIX, ApiToken
from app.models.users import VALID_ROLES  # noqa: F401 — re-exported for routes

# last_used_at is best-effort telemetry; throttle writes so a chatty agent
# doesn't turn every GET into a DB write.
_LAST_USED_RESOLUTION = timedelta(minutes=5)


def generate_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def resolve(bearer: str) -> dict | None:
    """Bearer secret → {"label", "role", "allow_backups"} for an active token; {"revoked":
    True, "label"} for one an administrator turned off (so the 401 can say
    so, #434); None for a secret that matches nothing."""
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
        now = datetime.now(timezone.utc)
        last = row.last_used_at
        if last is not None and last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if last is None or (now - last) > _LAST_USED_RESOLUTION:
            row.last_used_at = now
            db.commit()
        return {
            "label": row.label,
            "role": row.role,
            "allow_backups": bool(row.allow_backups),
        }
    finally:
        db.close()
