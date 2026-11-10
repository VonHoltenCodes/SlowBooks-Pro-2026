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
        "mercury_api_token",
        "mercury_webhook_secret",
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
    "use_tax_rate": "Use tax rate",
}
# A document keeps its tax rate to four places of a percent (New York
# City's 8.875%, 7.0625%), so the default that fills it in can have no more.
_FOUR_PLACE_PERCENTS = {"default_tax_rate", "use_tax_rate"}
# Percents whose empty value means "follow another setting", kept empty.
_BLANK_IS_DEFAULT = {"use_tax_rate"}
# key -> (label, smallest allowed, largest allowed or None)
_WHOLE_NUMBER_SETTINGS = {
    "invoice_next_number": ("Next invoice number", 1, None),
    "estimate_next_number": ("Next estimate number", 1, None),
    "late_fee_grace_days": ("Grace days", 0, None),
    "smtp_port": ("SMTP port", 1, 65535),
    "warn_date_past_days": ("Days in the past", 1, 3650),
    "warn_date_future_days": ("Days in the future", 1, 3650),
}
# Document counters keep the zeros typed in front: "0001" numbers invoices
# 0001, 0002 ... ("INV-0001" style), where "1" numbers them 1, 2 ...
_KEEP_TYPED_DIGITS = {"invoice_next_number", "estimate_next_number"}
# A closing date that does not parse used to be stored as typed, and then
# read as "no closing date" — the lock silently off.
_DATE_SETTINGS = {"closing_date": "Closing date"}

# A check-alignment nudge in inches (#440): a small shift of the check
# portion so the values land on pre-printed stock. Kept to a half inch each
# way, two decimal places (0.01in steps), stored as a decimal string.
_INCH_NUDGE_SETTINGS = {
    "check_nudge_x_in": "Horizontal nudge",
    "check_nudge_y_in": "Vertical nudge",
}
_NUDGE_LIMIT = Decimal("0.5")

# The check face's per-field layout (#749): pre-printed stock is not
# universal, so each value the face prints can be moved, sized and bolded on
# its own, on top of the whole-face nudge. Stored as one JSON object,
# check_field_layout, keyed by field, each holding only what differs from
# today's face: "x" / "y" (inches, positive = right / down, -1 to 1 in 0.01
# steps), "size" (points, 6 to 18, one decimal place) and "bold" (true or
# false). "" (the default) is today's layout. {field: label}
CHECK_LAYOUT_FIELDS = {
    "payee": "Payee",
    "payee_address": "Payee address",
    "date": "Date",
    "amount": "Amount in figures",
    "amount_words": "Amount in words",
    "memo": "Memo",
}
CHECK_LAYOUT_OFFSET_LIMIT = Decimal("1")
CHECK_LAYOUT_SIZE_MIN = Decimal("6")
CHECK_LAYOUT_SIZE_MAX = Decimal("18")

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


_ADDRESS_LIST_SETTINGS = {
    "smtp_always_cc": "Always CC",
    "smtp_always_bcc": "Always BCC",
}


def _clean_deposit_account_ids(value, text: str) -> str:
    """Make Deposits' destination list (#735) as stored: a JSON list of
    account ids, each once, in id order; "[]" for every bank account. The
    API may send the list itself or its JSON text; an empty value is the
    empty list. Whether each id is a bank account in the chart is checked
    by the settings route, which has the database."""
    import json

    if isinstance(value, (list, tuple)):
        items = list(value)
    elif text == "":
        items = []
    else:
        try:
            items = json.loads(text)
        except ValueError:
            items = None
    wanted = (
        "Deposit into these accounts must be a list of bank account ids, "
        "such as [1, 2], or [] for every bank account."
    )
    if not isinstance(items, list):
        raise SettingValueError(wanted)
    ids = set()
    for item in items:
        if isinstance(item, bool):
            raise SettingValueError(wanted)
        if isinstance(item, int):
            ids.add(item)
        elif isinstance(item, str) and _DIGITS.fullmatch(item.strip()):
            ids.add(int(item.strip()))
        else:
            raise SettingValueError(wanted)
    return json.dumps(sorted(ids))


def _layout_number(value):
    """A number typed or sent for a layout attribute, as a Decimal; None
    for blank (today's value); False for anything that is not a number."""
    if value is None or isinstance(value, bool):
        return None if value is None else False
    text = str(value).strip()
    if text == "":
        return None
    try:
        number = Decimal(text)
    except InvalidOperation:
        return False
    return number if number.is_finite() else False


def _json_number(number: Decimal):
    return int(number) if number == number.to_integral_value() else float(number)


def _clean_check_field_layout(value, text: str) -> str:
    """check_field_layout (#749) as stored: compact JSON with each field's
    attributes that differ from today's face, or "" for none. The API may
    send the object itself or its JSON text. Every field and attribute is
    checked; the first problem of each field is named."""
    import json

    if isinstance(value, dict):
        layout = value
    elif text == "":
        return ""
    else:
        try:
            layout = json.loads(text)
        except ValueError:
            layout = None
    if not isinstance(layout, dict):
        raise SettingValueError(
            "Check field layout must be an object of fields, such as "
            '{"date": {"x": -0.2, "size": 12, "bold": true}}.'
        )
    problems = []
    stored = {}
    for field in sorted(layout):
        attrs = layout[field]
        label = CHECK_LAYOUT_FIELDS.get(field)
        if label is None:
            problems.append(
                f"Check field layout: {field!s} is not a field; the fields are "
                + ", ".join(CHECK_LAYOUT_FIELDS)
                + "."
            )
            continue
        if attrs is None:
            continue
        if not isinstance(attrs, dict):
            problems.append(f"{label}: the layout must be an object.")
            continue
        unknown = sorted(set(attrs) - {"x", "y", "size", "bold"})
        if unknown:
            problems.append(
                f"{label}: {', '.join(map(str, unknown))} is not part of a field's "
                "layout (x, y, size, bold)."
            )
            continue
        kept = {}
        for attr, what in (("x", "left/right"), ("y", "up/down")):
            number = _layout_number(attrs.get(attr))
            if number is None:
                continue
            if number is False or abs(number) > CHECK_LAYOUT_OFFSET_LIMIT:
                problems.append(
                    f"{label} {what} offset must be a number of inches from -1 to 1."
                )
            elif number != number.quantize(Decimal("0.01")):
                problems.append(
                    f"{label} {what} offset can have up to two decimal places "
                    "(0.01in steps)."
                )
            elif number != 0:
                kept[attr] = _json_number(number)
        number = _layout_number(attrs.get("size"))
        if number is not None:
            if (
                number is False
                or not CHECK_LAYOUT_SIZE_MIN <= number <= CHECK_LAYOUT_SIZE_MAX
            ):
                problems.append(
                    f"{label} text size must be a number of points from 6 to 18, "
                    "or empty for the size it prints at today."
                )
            elif number != number.quantize(Decimal("0.1")):
                problems.append(f"{label} text size can have one decimal place (10.5).")
            else:
                kept["size"] = _json_number(number)
        bold = attrs.get("bold")
        if bold in ("true", "false"):
            bold = bold == "true"
        if isinstance(bold, bool):
            kept["bold"] = bold
        elif bold not in (None, ""):
            problems.append(f"{label} bold must be true or false.")
        if kept:
            stored[field] = kept
    if problems:
        raise SettingValueError(" ".join(problems))
    return json.dumps(stored, sort_keys=True, separators=(",", ":")) if stored else ""


def check_field_layout(text) -> dict:
    """The stored check_field_layout (#749) as {field: {attr: value}}, with
    anything that does not check out left out, so a damaged value prints
    today's face rather than failing the print."""
    import json

    try:
        cleaned = _clean_check_field_layout(None, (text or "").strip())
    except SettingValueError:
        # keep the fields that are fine on their own
        try:
            raw = json.loads(text)
        except (TypeError, ValueError):
            return {}
        if not isinstance(raw, dict):
            return {}
        out = {}
        for field, attrs in raw.items():
            try:
                one = _clean_check_field_layout({field: attrs}, "")
            except SettingValueError:
                continue
            if one:
                out.update(json.loads(one))
        return out
    return json.loads(cleaned) if cleaned else {}


def clean_setting_value(key: str, value) -> str:
    """The string to store for ``key``, or SettingValueError saying what to
    type instead. Settings without a rule are stored as given."""
    text = "" if value is None else str(value).strip()
    if key == "deposit_account_ids":
        return _clean_deposit_account_ids(value, text)
    if key == "check_field_layout":
        return _clean_check_field_layout(value, text)
    if key in _ADDRESS_LIST_SETTINGS:
        # stored as the clean comma-separated list, so what the send uses is
        # what the page shows; an entry that is not an address is refused
        from app.services.email_service import LOG_ADDRESSES_MAX, parse_addresses

        label = _ADDRESS_LIST_SETTINGS[key]
        kept, rejected = parse_addresses(text)
        if rejected:
            raise SettingValueError(
                f"{label}: not an email address: " + ", ".join(rejected[:5]) + "."
            )
        joined = ", ".join(kept)
        if len(joined) > LOG_ADDRESSES_MAX:
            raise SettingValueError(
                f"{label} is at most {LOG_ADDRESSES_MAX} characters of addresses."
            )
        return joined
    if key in _PERCENT_SETTINGS:
        label = _PERCENT_SETTINGS[key]
        if text == "":
            return "" if key in _BLANK_IS_DEFAULT else "0"
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
    if key == "report_basis":
        # the statements' default basis (#483): accrual | cash
        if text == "":
            return "accrual"
        if text.lower() not in ("accrual", "cash"):
            raise SettingValueError("Report basis must be accrual or cash.")
        return text.lower()
    if key == "fiscal_year_start_month":
        # The month a fiscal year starts, 1-12 (#444). "1" is the calendar year.
        number = int(text) if _DIGITS.fullmatch(text) else None
        if number is None or not 1 <= number <= 12:
            raise SettingValueError(
                "First month of the fiscal year must be a month, 1 to 12."
            )
        return str(number)
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
    if key in _INCH_NUDGE_SETTINGS:
        label = _INCH_NUDGE_SETTINGS[key]
        if text == "":
            return "0"
        try:
            number = Decimal(text)
        except InvalidOperation:
            number = None
        if number is None or not number.is_finite() or abs(number) > _NUDGE_LIMIT:
            raise SettingValueError(
                f"{label} must be a number of inches from -0.5 to 0.5."
            )
        if number != number.quantize(Decimal("0.01")):
            raise SettingValueError(
                f"{label} can have up to two decimal places (0.01in steps)."
            )
        return "0" if number == 0 else format(number.normalize(), "f")
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


def new_lines_taxable(db: Session) -> bool:
    """Whether a new sales line or item is taxable when it does not say
    (Settings -> new_lines_taxable, #486). On unless set to "false"."""
    return get_setting_raw(db, "new_lines_taxable") != "false"


def use_account_numbers(db: Session) -> bool:
    """Whether the chart shows and sorts by account numbers (Settings ->
    use_account_numbers, #567). On unless set to "false"."""
    return get_setting_raw(db, "use_account_numbers") != "false"


def refuse_oversell(db: Session) -> bool:
    """Whether a sale of more of a stock item than is on hand is refused
    (Settings -> inventory_refuse_oversell, #642). Off unless set to
    "true": by default such a sale posts and the response warns."""
    return get_setting_raw(db, "inventory_refuse_oversell") == "true"


def auto_enter_on_open(db: Session) -> bool:
    """Whether the app enters due auto-enter memorized bills and journal
    entries by itself, when it opens and once a day while it runs (Settings
    -> auto_enter_on_open, #605). Recurring invoices are never entered by
    those runs (app/services/scheduler.py). On unless set to "false"."""
    return get_setting_raw(db, "auto_enter_on_open") != "false"


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
    signal to leave Schedule C exactly as before (#488).

    Nonprofit mode (company_type == "nonprofit") overrides all of this: a
    nonprofit files Form 990 and never Schedule C, whatever entity type it has
    picked, so the gate and the page show Form 990, not Schedule C."""
    settings = settings or {}
    entity_type = (settings.get("entity_type") or "").strip()
    election = (settings.get("llc_tax_election") or "").strip()
    nonprofit = (settings.get("company_type") or "").strip() == "nonprofit"
    if nonprofit:
        # A nonprofit files Form 990 regardless of entity type; it never files
        # Schedule C. Overrides the entity table below.
        return {
            "entity_type": entity_type,
            "election": election if entity_type == "llc" else "",
            "label": "a nonprofit",
            "return_form": "Form 990",
            "schedule_c": False,
            "nonprofit": nonprofit,
        }
    unset = {
        "entity_type": "",
        "election": "",
        "label": "",
        "return_form": "",
        "schedule_c": False,
        "nonprofit": nonprofit,
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
                "nonprofit": nonprofit,
            }
        # LLC with no election chosen yet: we cannot say which return applies,
        # so Schedule C stays as it was (return_form left empty, no gate).
        return {
            "entity_type": "llc",
            "election": "",
            "label": "an LLC",
            "return_form": "",
            "schedule_c": False,
            "nonprofit": nonprofit,
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
        "nonprofit": nonprofit,
    }
