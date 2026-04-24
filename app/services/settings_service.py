"""Shared helpers for reading and writing Settings rows.

Extracted here so multiple routers can import them without creating
cross-router dependencies or violating the "don't import private _functions
from other modules" convention.
"""

from sqlalchemy.orm import Session

from app.models.settings import Settings, DEFAULT_SETTINGS

_SENSITIVE_KEYS = frozenset(
    {
        "auth_password_hash",
        "session_secret",
    }
)


def get_all_settings(db: Session) -> dict:
    """Return all settings as a dict, merging DB rows over DEFAULT_SETTINGS.

    Sensitive keys (password hashes, secrets) are excluded from the result.
    """
    rows = db.query(Settings).all()
    result = dict(DEFAULT_SETTINGS)
    for row in rows:
        if row.key not in _SENSITIVE_KEYS:
            result[row.key] = row.value
    return result


def get_setting_raw(db: Session, key: str) -> str | None:
    """Return a single setting value by key (including sensitive keys)."""
    row = db.query(Settings).filter(Settings.key == key).first()
    return row.value if row else None


def set_setting(db: Session, key: str, value: str) -> None:
    """Upsert a single setting row (caller must db.commit())."""
    row = db.query(Settings).filter(Settings.key == key).first()
    if row:
        row.value = value
    else:
        row = Settings(key=key, value=value)
        db.add(row)
