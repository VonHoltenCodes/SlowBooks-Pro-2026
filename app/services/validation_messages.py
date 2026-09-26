# ============================================================================
# Validation errors, in sentences a person can act on.
#
# A request the schemas refuse answers 422 with Pydantic's own list of
# errors: {"type", "loc", "msg", ...}. The page used to join them as
# "name: String should have at least 1 character" — validator text, with the
# field's code name in front (explore 2.17.3, skytech L5). Every entry now
# also carries "message": the same fact as a plain sentence naming the field
# the way the form labels it ("Name is required.", "Name must be 200
# characters or fewer."). Nothing is removed: agents and scripts that read
# type/loc/msg see exactly what they saw before.
# ============================================================================

import re

# Words that read as letters, not as a word, in a field label.
_ACRONYMS = {
    "api": "API",
    "cc": "CC",
    "ein": "EIN",
    "fx": "FX",
    "id": "ID",
    "iif": "IIF",
    "ofx": "OFX",
    "pdf": "PDF",
    "po": "PO",
    "qbo": "QBO",
    "smtp": "SMTP",
    "ssn": "SSN",
    "tls": "TLS",
    "url": "URL",
    "zip": "ZIP",
}

_WHERE = {"body", "query", "path", "header", "cookie"}

_DATE_KINDS = {
    "date_parsing",
    "date_type",
    "date_from_datetime_parsing",
    "date_from_datetime_inexact",
}
_DATETIME_KINDS = {
    "datetime_parsing",
    "datetime_type",
    "datetime_from_date_parsing",
    "datetime_object_invalid",
}


def field_label(name: str) -> str:
    """customer_id -> "Customer", bill_address1 -> "Bill address 1",
    ssn_last_four -> "SSN last four"."""
    name = str(name)
    if name.endswith("_id") and len(name) > 3:
        name = name[:-3]
    words = []
    for word in name.split("_"):
        if not word:
            continue
        m = re.fullmatch(r"([a-zA-Z]+)(\d+)", word)
        parts = [m.group(1), m.group(2)] if m else [word]
        for part in parts:
            words.append(_ACRONYMS.get(part.lower(), part.lower()))
    if not words:
        return ""
    first = words[0]
    words[0] = first if first.isupper() else first[:1].upper() + first[1:]
    return " ".join(words)


def _singular(word: str) -> str:
    if word.endswith("ies"):
        return word[:-3] + "y"
    if word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _context_and_field(loc) -> tuple[str, str | None]:
    """("Line 2", "quantity") for ["body", "lines", 1, "quantity"]: the list
    rows the field sits in, and the field's own name (None for an error on
    a whole row or on the whole body)."""
    parts = list(loc or [])
    # Only the first element says where the value came from ("body",
    # "query", ...); a field deeper in the body may itself be called "path".
    if parts and isinstance(parts[0], str) and parts[0] in _WHERE:
        parts = parts[1:]
    context = []
    field = None
    for i, part in enumerate(parts):
        if isinstance(part, int):
            parent = parts[i - 1] if i and isinstance(parts[i - 1], str) else "item"
            context.append(f"{field_label(_singular(parent))} {part + 1}")
            field = None
        else:
            field = part
    # a list's own name is not the field when an index follows it
    return ", ".join(context), field


def _sentence(text: str) -> str:
    text = text.strip()
    if not text:
        return text
    text = text[0].upper() + text[1:]
    ended = text[-1] in ".!?" or text[-2:] in (".)", "!)", "?)")
    return text if ended else text + "."


def _number(v) -> str:
    """ctx limits arrive as numbers or strings; print 0 not 0.0."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return str(int(f)) if f.is_integer() else str(v)


def plain_message(error: dict) -> str:
    """One Pydantic error entry as a sentence."""
    kind = error.get("type") or ""
    ctx = error.get("ctx") or {}
    msg = str(error.get("msg") or "")
    context, field = _context_and_field(error.get("loc"))
    label = field_label(field) if field else ""
    subject = label or "This value"

    def done(sentence: str) -> str:
        sentence = _sentence(sentence)
        return f"{context}: {sentence}" if context else sentence

    if kind in ("value_error", "assertion_error"):
        # A validator's own words: already a sentence about its subject.
        text = re.sub(r"^(Value error|Assertion failed),\s*", "", msg)
        # A trailing note for API callers stays in "msg" only.
        text = re.sub(r"\s*\(API: .*\)\s*$", "", text)
        return done(text)
    if kind == "missing":
        return done(f"{subject} is required")
    if kind == "string_too_short":
        n = ctx.get("min_length", 1)
        if n == 1:
            return done(f"{subject} is required")
        return done(f"{subject} must be at least {n} characters")
    if kind == "string_too_long":
        return done(f"{subject} must be {ctx.get('max_length')} characters or fewer")
    if kind == "string_pattern_mismatch":
        if "email" in (field or "").lower():
            return done(f"{subject} must be an email address, like name@example.com")
        return done(f"{subject} is not in the expected format")
    if kind in ("int_parsing", "int_type", "int_from_float"):
        return done(f"{subject} must be a whole number")
    if kind in (
        "float_parsing",
        "float_type",
        "decimal_parsing",
        "decimal_type",
        "finite_number",
    ):
        return done(f"{subject} must be a number")
    if kind == "decimal_max_places":
        return done(
            f"{subject} can have at most {ctx.get('decimal_places')} decimal places"
        )
    if kind in ("decimal_max_digits", "decimal_whole_digits"):
        return done(f"{subject} has too many digits")
    if kind == "greater_than":
        return done(f"{subject} must be more than {_number(ctx.get('gt'))}")
    if kind == "greater_than_equal":
        return done(f"{subject} must be {_number(ctx.get('ge'))} or more")
    if kind == "less_than":
        return done(f"{subject} must be less than {_number(ctx.get('lt'))}")
    if kind == "less_than_equal":
        return done(f"{subject} must be {_number(ctx.get('le'))} or less")
    if kind in _DATE_KINDS:
        return done(f"{subject} must be a date (YYYY-MM-DD)")
    if kind in _DATETIME_KINDS:
        return done(f"{subject} must be a date and time")
    if kind in ("time_parsing", "time_type"):
        return done(f"{subject} must be a time")
    if kind in ("date_past", "datetime_past"):
        return done(f"{subject} must be in the past")
    if kind in ("date_future", "datetime_future"):
        return done(f"{subject} must be in the future")
    if kind in ("bool_parsing", "bool_type"):
        return done(f"{subject} must be yes or no (true or false)")
    if kind in ("enum", "literal_error"):
        expected = str(ctx.get("expected") or "").replace("'", "")
        return done(f"{subject} must be one of: {expected}")
    if kind == "string_type":
        return done(f"{subject} must be text")
    if kind in ("too_short", "too_long"):
        n = ctx.get("min_length" if kind == "too_short" else "max_length")
        one = field_label(_singular(field or "item")).lower()
        many = (label or "items").lower()
        if kind == "too_short":
            return done(
                f"At least one {one} is needed"
                if n == 1
                else f"At least {n} {many} are needed"
            )
        return done(
            f"At most one {one} is allowed"
            if n == 1
            else f"At most {n} {many} are allowed"
        )
    if kind == "extra_forbidden":
        return done(f"'{field}' is not a field this accepts" if field else msg)
    if kind == "json_invalid":
        return "The request was not valid JSON."
    if kind.startswith("url"):
        return done(f"{subject} must be a web address, like https://example.com")
    # Anything else: the validator's words, with the field in front.
    return done(f"{subject}: {msg}" if label else msg)


def with_messages(errors: list, wording=None) -> list:
    """The errors as FastAPI returns them, each with "message" added.
    ``wording`` (optional) rewords a sentence into the company's vocabulary
    (a nonprofit's "Donor is required", not "Customer is required")."""
    out = []
    for error in errors:
        if not isinstance(error, dict):
            out.append(error)
            continue
        try:
            message = plain_message(error)
            if wording is not None:
                message = wording(message)
        except Exception:
            message = str(error.get("msg") or "")
        out.append({**error, "message": message})
    return out
