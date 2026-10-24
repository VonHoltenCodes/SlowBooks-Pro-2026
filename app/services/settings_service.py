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
from cryptography.fernet import InvalidToken

from app.services.crypto import decrypt_value, encrypt_value, is_encrypted, is_readable

_SENSITIVE_KEYS = frozenset(
    {
        "auth_password_hash",
        "session_secret",
        # which company a sign-in belongs to (app/services/auth.py)
        "company_session_id",
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
        "simplefin_access_url",
        # the bank account an ACH file debits (discussion #211)
        "ach_company_account",
    }
)


# The settings of the QuickBooks Online connector, retired in 2.23.2
# (services/qbo_common.py says why): an Intuit app's credentials and the
# tokens of a connection, with no reader left. A company file that holds
# them is cleaned on the first start after upgrading.
RETIRED_SETTINGS_KEYS = (
    "qbo_enabled",
    "qbo_client_id",
    "qbo_client_secret",
    "qbo_redirect_uri",
    "qbo_environment",
    "qbo_access_token",
    "qbo_refresh_token",
    "qbo_realm_id",
    "qbo_token_expires_at",
    "qbo_oauth_state",
)


def remove_retired_settings(db: Session) -> int:
    """Delete the rows of RETIRED_SETTINGS_KEYS; the number removed."""
    rows = db.query(Settings).filter(Settings.key.in_(RETIRED_SETTINGS_KEYS)).all()
    for row in rows:
        db.delete(row)
    if rows:
        db.commit()
    return len(rows)


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
        try:
            return decrypt_value(value)
        except (InvalidToken, ValueError):
            # Saved under a settings key this install no longer has (a
            # Docker container recreated without its key did this). Read as
            # not set: every use of these fails closed (a webhook with no
            # secret is refused, a closing date with no password refuses
            # changes), and Settings names it to be entered again. Raising
            # here failed every page that reads the settings.
            return ""
    return value


def unreadable_secret_keys(db: Session) -> list[str]:
    """The settings saved encrypted that no key here decrypts: they read as
    not set until someone enters them again."""
    rows = db.query(Settings).filter(Settings.value.like("fernet:%")).all()
    return sorted(r.key for r in rows if not is_readable(r.value))


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
    # A row added earlier in this session and not yet flushed is the row to
    # update: the session doesn't autoflush, so the query below can't see
    # it, and a second insert broke the commit on the unique key (2.22.1
    # gate round 3, W-3 / NEW-55: the first key saved over a pre-2.22.1 AI
    # key was a 500, its provider written twice in one save).
    row = (
        next((o for o in db.new if isinstance(o, Settings) and o.key == key), None)
        or db.query(Settings).filter(Settings.key == key).first()
    )
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
# A document keeps its tax rate to four places of a percent (New York
# City's 8.875%, 7.0625%), so the default that fills it in can have no more.
_FOUR_PLACE_PERCENTS = {"default_tax_rate"}
# key -> (label, smallest allowed, largest allowed or None)
_WHOLE_NUMBER_SETTINGS = {
    "invoice_next_number": ("Next invoice number", 1, None),
    "estimate_next_number": ("Next estimate number", 1, None),
    "late_fee_grace_days": ("Grace days", 0, None),
    "smtp_port": ("SMTP port", 1, 65535),
}
# Document counters keep the zeros typed in front: "0001" numbers invoices
# 0001, 0002 ... ("INV-0001" style), where "1" numbers them 1, 2 ...
_KEEP_TYPED_DIGITS = {"invoice_next_number", "estimate_next_number"}
# A closing date that does not parse used to be stored as typed, and then
# read as "no closing date" — the lock silently off.
_DATE_SETTINGS = {"closing_date": "Closing date"}

_DIGITS = re.compile(r"[0-9]+")
# The two identifiers a bank prints on its ACH origination sheet: what the
# bank assigned, letters or digits, in 10-character fields (nacha_export).
# The company account number is stored as typed; the file's field takes 17
# characters of it.
_ACH_ID_SETTINGS = {
    "ach_immediate_origin": "Immediate origin",
    "ach_company_id": "Company identification",
}
_ALNUM = re.compile(r"[0-9A-Za-z]+")

# Free-text settings with a length cap (#415): a pasted block would run off
# the page it prints on. {key: (label, max_chars)}.
_MAX_LENGTH_SETTINGS = {
    "giving_statement_footer": ("Giving statement footer", 500),
}
# ...and a line cap: the footer prints in the statement's bottom margin, which
# holds six 8pt lines inside the printable area (#426 review: a 14-line
# footer of 140 characters overprinted the last gift row and ran off the
# page). A line longer than fits across the page wraps, so each started
# FOOTER_LINE_CHARS characters of a line count as a line.
# {key: (label, max_lines)}
FOOTER_MAX_LINES = 6
FOOTER_LINE_CHARS = 90
_MAX_LINE_SETTINGS = {
    "giving_statement_footer": ("Giving statement footer", FOOTER_MAX_LINES),
}


def printed_lines(text: str, width: int = FOOTER_LINE_CHARS) -> int:
    """How many lines ``text`` takes on the page: one per typed line, more
    for a line that wraps."""
    if not text:
        return 0
    return sum(max(1, -(-len(line.rstrip()) // width)) for line in text.splitlines())


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
        if key in _FOUR_PLACE_PERCENTS and number != number.quantize(Decimal("0.0001")):
            raise SettingValueError(
                f"{label} can have up to four decimal places: 8.875 means 8.875%."
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
        return text if key in _KEEP_TYPED_DIGITS else str(number)
    if key == "ach_bank_routing":
        from app.services.nacha_export import validate_routing_number

        if text == "":
            return ""
        if not validate_routing_number(text):
            raise SettingValueError(
                "Bank routing number must be the 9-digit ABA routing number "
                "of the company's bank, as printed on the ACH origination "
                "agreement."
            )
        return text
    if key in _ACH_ID_SETTINGS:
        if text == "":
            return ""
        if not _ALNUM.fullmatch(text) or len(text) > 10:
            raise SettingValueError(
                f"{_ACH_ID_SETTINGS[key]} must be up to 10 letters or digits, "
                "as the bank assigned it."
            )
        return text
    if key in _MAX_LENGTH_SETTINGS:
        label, cap = _MAX_LENGTH_SETTINGS[key]
        if len(text) > cap:
            raise SettingValueError(f"{label} is at most {cap} characters.")
        if key in _MAX_LINE_SETTINGS:
            label, most = _MAX_LINE_SETTINGS[key]
            if printed_lines(text) > most:
                raise SettingValueError(
                    f"{label} is at most {most} lines, so it fits at the foot of "
                    f"each page; a line longer than {FOOTER_LINE_CHARS} "
                    "characters wraps and counts as two."
                )
        return text
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


# Legal entity type (#488) — the one table that maps an entity type (and, for
# an LLC, its tax election) to a human label and the federal return it files.
# The Schedule C gate (routes/tax.py) and the settings/tax UI read entity_info
# rather than matching strings; nothing else computes this.
# Labels carry their own article ("an LLC", "an S corporation") because the
# right one depends on how the acronym is read, not its first letter — the
# letter-based _article in control_accounts.py would get "a LLC"/"a S corp"
# wrong — so the sentence that uses a label adds no article of its own.
_ENTITY_LABELS = {
    "sole_proprietor": "a sole proprietor",
    "general_partnership": "a general partnership",
    "limited_partnership": "a limited partnership",
    "llp": "a limited liability partnership",
    "llc": "an LLC",
    "c_corp": "a C corporation",
    "s_corp": "an S corporation",
}

# entity_type -> (return_form, schedule_c); the non-LLC entities.
_ENTITY_RETURNS = {
    "sole_proprietor": ("Schedule C", True),
    "general_partnership": ("Form 1065", False),
    "limited_partnership": ("Form 1065", False),
    "llp": ("Form 1065", False),
    "c_corp": ("Form 1120", False),
    "s_corp": ("Form 1120-S", False),
}

# llc_tax_election -> (label, return_form, schedule_c).
_LLC_ELECTIONS = {
    "disregarded": ("an LLC taxed as a sole proprietor", "Schedule C", True),
    "partnership": ("an LLC taxed as a partnership", "Form 1065", False),
    "s_corp": ("an LLC taxed as an S corporation", "Form 1120-S", False),
    "c_corp": ("an LLC taxed as a C corporation", "Form 1120", False),
}


def entity_info(settings) -> dict:
    """The company's legal entity type as one record: the stored value, the
    LLC tax election, a human `label`, the `return_form` it files, and whether
    Schedule C is its form (`schedule_c`).

    `return_form` is "Schedule C" for a sole proprietor and a disregarded
    single-member LLC; "Form 1065" for the partnerships and an LLC taxed as
    one; "Form 1120-S" for an S corp (or LLC-as-S-corp); "Form 1120" for a C
    corp (or LLC-as-C-corp); and "" when the entity type is unset, or an LLC
    whose election has not been chosen yet. An empty `return_form` is the
    signal to leave Schedule C exactly as before (#488)."""
    settings = settings or {}
    entity_type = (settings.get("entity_type") or "").strip()
    election = (settings.get("llc_tax_election") or "").strip()
    unset = {
        "entity_type": "",
        "election": "",
        "label": "",
        "return_form": "",
        "schedule_c": False,
    }
    if entity_type == "":
        return unset
    if entity_type == "llc":
        if election in _LLC_ELECTIONS:
            label, return_form, schedule_c = _LLC_ELECTIONS[election]
            return {
                "entity_type": "llc",
                "election": election,
                "label": label,
                "return_form": return_form,
                "schedule_c": schedule_c,
            }
        # LLC with no election chosen yet: we cannot say which return applies,
        # so Schedule C stays as it was (return_form left empty, no gate).
        return {
            "entity_type": "llc",
            "election": "",
            "label": "an LLC",
            "return_form": "",
            "schedule_c": False,
        }
    if entity_type not in _ENTITY_RETURNS:
        return unset
    return_form, schedule_c = _ENTITY_RETURNS[entity_type]
    return {
        "entity_type": entity_type,
        "election": "",
        "label": _ENTITY_LABELS[entity_type],
        "return_form": return_form,
        "schedule_c": schedule_c,
    }
