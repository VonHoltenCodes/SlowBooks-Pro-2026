"""Shared helpers for reading and writing Settings rows.

Extracted here so multiple routers can import them without creating
cross-router dependencies or violating the "don't import private _functions
from other modules" convention.
"""

from sqlalchemy.orm import Session

from app.models.settings import Settings, DEFAULT_SETTINGS
from app.services.crypto import decrypt_value, encrypt_value, is_encrypted

_SENSITIVE_KEYS = frozenset(
    {
        "auth_password_hash",
        "session_secret",
    }
)

# Credentials that must be Fernet-encrypted at rest (fernet:v1: prefix).
# Redaction-on-read (routes/settings SECRET_KEYS) hides these from the
# API; this set hides them from the database file itself. AI provider
# keys are handled by analytics.py with the same crypto primitives;
# auth_password_hash is argon2 (already non-reversible).
ENCRYPTED_SETTINGS_KEYS = frozenset(
    {
        "closing_date_password",
        "smtp_password",
        "stripe_secret_key",
        "stripe_webhook_secret",
        "paypal_client_secret",
        "square_access_token",
        "square_webhook_signature_key",
        "qbo_client_secret",
        "qbo_access_token",
        "qbo_refresh_token",
        "simplefin_access_url",
    }
)


SECRET_PLACEHOLDER = "********"


def redact_secrets(settings: dict) -> dict:
    """Copy of `settings` with every encrypted value replaced by a placeholder.

    get_all_settings() decrypts secrets because the server needs the real
    values to send mail and call payment providers. Anywhere that dict can
    reach a user — an API response, or a user-authored Jinja template —
    has to go through this first. Keyed off ENCRYPTED_SETTINGS_KEYS so a
    newly added credential is redacted by the same act that encrypts it.
    """
    return {
        key: (SECRET_PLACEHOLDER if key in ENCRYPTED_SETTINGS_KEYS and value else value)
        for key, value in settings.items()
    }


def _maybe_decrypt(key: str, value):
    if key in ENCRYPTED_SETTINGS_KEYS and value:
        return decrypt_value(value)
    return value


def get_all_settings(db: Session) -> dict:
    """Return all settings as a dict, merging DB rows over DEFAULT_SETTINGS.

    Sensitive keys (password hashes, secrets) are excluded from the result.
    """
    rows = db.query(Settings).all()
    result = dict(DEFAULT_SETTINGS)
    for row in rows:
        if row.key not in _SENSITIVE_KEYS:
            result[row.key] = _maybe_decrypt(row.key, row.value)
    return result


def get_setting_raw(db: Session, key: str) -> str | None:
    """Return a single setting value by key (including sensitive keys)."""
    row = db.query(Settings).filter(Settings.key == key).first()
    return _maybe_decrypt(key, row.value) if row else None


def set_setting(db: Session, key: str, value: str) -> None:
    """Upsert a single setting row (caller must db.commit())."""
    if key in ENCRYPTED_SETTINGS_KEYS and value and not is_encrypted(value):
        value = encrypt_value(value)
    row = db.query(Settings).filter(Settings.key == key).first()
    if row:
        row.value = value
    else:
        row = Settings(key=key, value=value)
        db.add(row)


def upgrade_plaintext_secrets(db: Session) -> int:
    """One-shot at-rest upgrade: encrypt any legacy plaintext secret rows.

    Called at startup so existing installs close the gap on their first
    boot after upgrading. Returns how many rows were upgraded. Never
    raises — a failed upgrade must not block the app from serving.
    """
    upgraded = 0
    try:
        rows = (
            db.query(Settings)
            .filter(Settings.key.in_(list(ENCRYPTED_SETTINGS_KEYS)))
            .all()
        )
        for row in rows:
            if row.value and not is_encrypted(row.value):
                row.value = encrypt_value(row.value)
                upgraded += 1
        if upgraded:
            db.commit()
    except Exception:
        db.rollback()
    return upgraded


def is_nonprofit(db: Session) -> bool:
    """True when the company file is set to nonprofit mode (Settings ->
    company_type). Gates the nonprofit documents, reports and vocabulary."""
    return get_setting_raw(db, "company_type") == "nonprofit"
