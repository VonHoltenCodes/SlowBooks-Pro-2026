"""Shared helpers for reading and writing Settings rows.

Extracted here so multiple routers can import them without creating
cross-router dependencies or violating the "don't import private _functions
from other modules" convention.
"""

import re
from datetime import date
from decimal import Decimal, InvalidOperation

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
    """A copy of the settings with every credential replaced.

    GHSA-c3v4-f43f-4wqm. `get_all_settings()` decrypts on the way out, and
    anything handed the result can read a live credential. `GET /api/settings`
    redacts for every role including admin, so the intent has always been that
    these values do not leave the server — but an editable email template
    rendered against the raw dict walks straight around that, and Jinja's
    sandbox is no help because `company` is a plain dict and reading a key
    from it is an ordinary permitted operation.

    It lives here, beside `ENCRYPTED_SETTINGS_KEYS`, so a credential added
    later is redacted by the same act that encrypts it. Two lists in two files
    is how they drift.
    """
    return {
        k: (SECRET_PLACEHOLDER if k in ENCRYPTED_SETTINGS_KEYS and v else v)
        for k, v in settings.items()
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


# Settings a person types a number or a date into. Every other setting is
# free text (or an enum, checked by the route). Before these were checked, a
# default tax rate of 150 or -5 and a next invoice number of "abc" all
# answered "Settings saved" — and then every new invoice was refused with a
# validator message about fractions (explore 2.17.3, skytech M4 / macbase1
# F4). A value that cannot work is refused here, where it was typed, in the
# words of the field it was typed into.
_PERCENT_SETTINGS = {
    "default_tax_rate": "Default tax rate",
    "late_fee_rate": "Late fee rate",
}
# key -> (label, smallest allowed, largest allowed or None)
_WHOLE_NUMBER_SETTINGS = {
    "invoice_next_number": ("Next invoice number", 1, None),
    "estimate_next_number": ("Next estimate number", 1, None),
    "late_fee_grace_days": ("Grace days", 0, None),
    "smtp_port": ("SMTP port", 1, 65535),
}
# A closing date that does not parse used to be stored as typed, and then
# read as "no closing date" — the lock silently off.
_DATE_SETTINGS = {"closing_date": "Closing date"}

_DIGITS = re.compile(r"[0-9]+")


class SettingValueError(ValueError):
    """A setting value a person has to correct; str() is the sentence to
    show them."""


def clean_setting_value(key: str, value) -> str:
    """The string to store for ``key``, or SettingValueError saying what to
    type instead. Settings without a rule are stored as given."""
    text = "" if value is None else str(value).strip()
    if key in _PERCENT_SETTINGS:
        label = _PERCENT_SETTINGS[key]
        if text == "":
            return "0"
        try:
            number = Decimal(text)
        except InvalidOperation:
            number = None
        if number is None or not number.is_finite() or not 0 <= number <= 100:
            raise SettingValueError(
                f"{label} must be a number from 0 to 100. It is a percent: "
                "8.25 means 8.25%."
            )
        return "0" if number == 0 else format(number, "f")
    if key in _WHOLE_NUMBER_SETTINGS:
        label, low, high = _WHOLE_NUMBER_SETTINGS[key]
        number = int(text) if _DIGITS.fullmatch(text) else None
        if number is None or number < low or (high is not None and number > high):
            if high is not None:
                wanted = f"a whole number from {low} to {high}"
            else:
                wanted = f"a whole number, {low} or more"
            raise SettingValueError(f"{label} must be {wanted}.")
        return str(number)
    if key in _DATE_SETTINGS:
        if text == "":
            return ""
        try:
            return date.fromisoformat(text).isoformat()
        except ValueError:
            raise SettingValueError(
                f"{_DATE_SETTINGS[key]} must be a date (YYYY-MM-DD), or empty "
                "for no closing date."
            ) from None
    return "" if value is None else str(value)


def is_nonprofit(db: Session) -> bool:
    """True when the company file is set to nonprofit mode (Settings ->
    company_type). Gates the nonprofit documents, reports and vocabulary."""
    return get_setting_raw(db, "company_type") == "nonprofit"
