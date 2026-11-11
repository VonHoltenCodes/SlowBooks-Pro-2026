import re
from datetime import date as _date
from decimal import Decimal, ROUND_HALF_UP
from typing import Annotated, Optional

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    StrictInt,
    StringConstraints,
    field_validator,
)

# A required display name that cannot be blank.
#
# `min_length` alone is not enough: a name of "   " has length 3 and passes,
# producing a record that is invisible in list views and unsearchable — there
# is nothing to click and nothing to type to find it again. strip_whitespace
# runs BEFORE the length check, so "   " collapses to "" and is rejected, and
# a padded "  Acme  " is stored cleanly as "Acme".
#
# max_length mirrors the VARCHAR(200) width shared by Customer.name and
# Vendor.name. Validating here keeps the two supported backends consistent:
# PostgreSQL raises StringDataRightTruncation (an opaque HTTP 500) while
# SQLite ignores VARCHAR(n) and stores the oversized value.
# A stored email address. Deliberately a permissive shape check, not RFC 5322
# validation: the goal is to reject "not-an-email", which was accepted and
# stored verbatim, without pulling in the email-validator dependency that
# pydantic.EmailStr requires — requirements.txt is tightly pinned with CVE
# rationale per line and is not somewhere to add a dependency casually.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

OptionalEmail = Annotated[
    str,
    StringConstraints(strip_whitespace=True, max_length=200, pattern=_EMAIL_RE.pattern),
]


def _blank_to_none(v):
    if isinstance(v, str) and not v.strip():
        return None
    return v


def clean_line_cost_type(v):
    """Shape a per-line cost_type (#550): blank is None, otherwise the key
    form the cost_types table uses (lowercase, spaces to underscores). The
    value is checked against the table in the route, like the cost code."""
    if v is None:
        return None
    v = str(v).strip().lower().replace(" ", "_")
    if not v:
        return None
    if len(v) > 20:
        raise ValueError("cost_type must be 20 characters or fewer")
    return v


def choice_error(label: str, choices: dict, field: str) -> ValueError:
    """A refused choice as a sentence a person reads: the field's label and
    the choices' labels ("Line type must be Labor, Equipment, Material or
    Other."), with the codes an API caller sends in a trailing note that
    validation_messages keeps in "msg" only."""
    names = list(choices.values())
    listed = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " or " + names[-1]
    return ValueError(
        f"{label} must be {listed}. (API: {field} is one of {', '.join(choices)}.)"
    )


# An email field that treats blank as absent. HTML forms submit every empty
# input as "" — Optional[OptionalEmail] alone rejects that with a pattern
# 422 even though the user typed nothing (#64). Blank collapses to None
# before the pattern runs; anything non-blank must still look like an email.
BlankableEmail = Annotated[Optional[OptionalEmail], BeforeValidator(_blank_to_none)]


NonBlankName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]


class MessageResponse(BaseModel):
    message: str


class PaginatedResponse(BaseModel):
    items: list
    total: int
    page: int
    per_page: int


def validate_non_negative_line(quantity, rate) -> None:
    """Shared line-amount validation for Create schemas.

    Rejects negative quantity or rate. Lines with both zero are allowed
    (description-only placeholder rows are common in invoice/bill builders).

    Negative line amounts on an invoice or bill silently create off-books
    obligations — the journal-entry posting code skips lines with
    amount <= 0, so a negative line produces a bill with no JE and a
    negative balance_due. Anything that smells like a refund / discount /
    return belongs in a credit memo, not a negative invoice line.
    """
    check_line_size(quantity, rate)
    q = Decimal(str(quantity if quantity is not None else 0))
    r = Decimal(str(rate if rate is not None else 0))
    if q < 0:
        raise ValueError("quantity must be non-negative; use a credit memo for refunds")
    if r < 0:
        raise ValueError("rate must be non-negative; use a credit memo for refunds")
    if q != q.quantize(QUANTITY_PLACES):
        raise ValueError("Quantities are kept to four decimal places.")


# A line's quantity is Numeric(12, 4) on invoices, estimates, bills, purchase
# orders, credit memos, vendor credits and recurring and memorized lines; its
# rate is Numeric(17, 4); an estimate line's unit cost is Numeric(12, 4). A
# figure past its column overflows Postgres (a 500) or is stored wrong on
# SQLite, and "1e40" at a rate of 0 passed the line check (it comes to 0)
# and raised in the quantity rounding (#782). Each is held to its column
# whatever the other figure is.
MAX_QUANTITY = Decimal("99999999.9999")
MAX_RATE = Decimal("9999999999999.9999")
MAX_UNIT_COST = Decimal("99999999.9999")


def _check_figure(value, limit: Decimal, label: str) -> None:
    if value is None:
        return
    d = Decimal(str(value))
    if not d.is_finite() or abs(d) > limit:
        raise ValueError(f"{label} can be at most {limit:,}. Check the line.")


def check_line_size(quantity, rate, unit_cost=None) -> None:
    """Refuse a quantity, rate or unit cost too large for its column (or not a
    number at all: "Infinity", "NaN") with a sentence, before anything rounds
    it."""
    _check_figure(quantity, MAX_QUANTITY, "A quantity")
    _check_figure(rate, MAX_RATE, "A rate")
    _check_figure(unit_cost, MAX_UNIT_COST, "A unit cost")


# Money columns are Numeric(15, 2): a line or a document total past this
# overflows Postgres (a 500) or, on SQLite, is stored as a float error.
MAX_LINE_AMOUNT = Decimal("9999999999999.99")


def line_total(quantity, rate) -> Decimal:
    """quantity x rate computed exactly, however large, so a figure past the
    money column can be named in a sentence instead of raising in a quantize."""
    q = Decimal(str(quantity if quantity is not None else 0))
    r = Decimal(str(rate if rate is not None else 0))
    return q * r if q.is_finite() and r.is_finite() else Decimal("NaN")


def check_line_fits(quantity, rate) -> None:
    """Refuse a line whose quantity x rate would not fit a money column (a
    sentence, never a 500)."""
    amount = line_total(quantity, rate)
    if not amount.is_finite() or abs(amount) > MAX_LINE_AMOUNT:
        raise ValueError(
            "This line comes to more than a line can be "
            f"({MAX_LINE_AMOUNT:,.2f}). Check its quantity and rate."
        )


# Percent columns (an estimate's markups, a change order's markup, a job's
# default change-order markup) are Numeric(7, 4): three whole digits. A
# larger figure overflows Postgres (a 500) and "1e30" raised in the money
# rounding before it got there.
MAX_PERCENT = Decimal("999.9999")


def check_markup_percent(v, label: str):
    """A markup percent is 0 to 999.9999; the sentence names the field by
    `label`. A negative markup would price the work below its cost."""
    if v is not None and not (0 <= v <= MAX_PERCENT):
        raise ValueError(f"{label} must be between 0 and {MAX_PERCENT}%.")
    return v


def check_lines_fit(lines) -> None:
    """The lines' sum also fits a money column."""
    total = sum((abs(line_total(ln.quantity, ln.rate)) for ln in lines), Decimal(0))
    if total > MAX_LINE_AMOUNT:
        raise ValueError(
            "The lines come to more than a document can be "
            f"({MAX_LINE_AMOUNT:,.2f}). Split the work across documents."
        )


# A line's quantity is stored to four decimal places (2.23.0, NEW-94: the
# imports stored 2.125 and every read gave 2.12); finer is refused above,
# as a sub-cent amount is.
QUANTITY_PLACES = Decimal("0.0001")


def quantity_text(value) -> str:
    """A quantity as the API and the documents show it: two places as
    before ("3.00", "1.50"), or the places it has when finer ("2.125",
    "0.3333"), never a tail of zeros."""
    d = Decimal(str(value if value is not None else 0))
    if d == d.quantize(Decimal("0.01")):
        return f"{d.quantize(Decimal('0.01')):f}"
    return format(d.normalize(), "f")


# Request date fields are bounded to this range. The engine computes a due
# date as ``invoice.date + timedelta(days=30)`` (routes/invoices/helpers.py):
# an invoice dated 9999-12-31 overflowed ``date.max`` and raised an uncaught
# OverflowError — an HTTP 500. The lower bound catches the mirror case (a
# document dated 0001-01-01, accepted before). 1900..2199 is wide enough for
# any real document, hire date or birth date we will ever store, and keeps
# date arithmetic clear of the type's edges. File importers (QuickBooks /
# Xero / IIF / …) build ORM rows directly, not these request schemas, so
# historical imports outside this window are unaffected.
MIN_REQUEST_YEAR = 1900
MAX_REQUEST_YEAR = 2199


class StrictModel(BaseModel):
    """Base for every request body: unknown fields are a 422, not silence.

    Field report (2.9.0 gate, both platforms): ``POST /api/invoices`` with
    ``line_items`` instead of ``lines`` returned 201 and a $0.00 invoice.
    Pydantic's default is to drop keys it does not know, which is the
    worst possible answer for an API agents drive from the spec — the
    caller supplied the data and was told it worked. ``extra="forbid"``
    turns that into an error naming the field. Response models keep the
    default; only what the client sends is checked.

    ``allow_inf_nan=False`` is the second half of that posture: a float
    field sent ``NaN`` or ``Infinity`` is a 422 here, not a time-bomb
    downstream. Left on (the pydantic default), a NaN quantity/rate reached
    ``Decimal(str(nan))`` and raised ``decimal.InvalidOperation`` (500); a
    NaN payment amount became a NULL insert (IntegrityError, 500). Decimal
    money fields already refused non-finite values; this extends the same
    rule to the ~200 ``float`` fields in one place instead of per field.

    The wildcard validator rejects the two hostile scalar shapes that slip
    past type checks: a NUL byte (``\\x00``) in any string — stored silently
    on SQLite, a 500 on PostgreSQL — and a date outside MIN/MAX_REQUEST_YEAR.
    A ``list[str]`` field is walked too. It is inherited by every request
    schema (and runs on their nested models and list items), so one
    definition covers them all.
    """

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    @field_validator("*", mode="after")
    @classmethod
    def _reject_hostile_scalars(cls, value):
        if isinstance(value, str):
            if "\x00" in value:
                raise ValueError("Text may not contain a NUL byte (\\x00).")
        elif isinstance(value, (list, tuple, set)):
            # A ``list[str]`` field: pydantic hands the validator the whole
            # list, so its strings are checked here, not one by one above.
            if any(isinstance(v, str) and "\x00" in v for v in value):
                raise ValueError("Text may not contain a NUL byte (\\x00).")
        elif isinstance(value, _date):  # also datetime, a date subclass
            if not (MIN_REQUEST_YEAR <= value.year <= MAX_REQUEST_YEAR):
                raise ValueError(
                    f"Date {value.isoformat()} is out of range; the year must "
                    f"be between {MIN_REQUEST_YEAR} and {MAX_REQUEST_YEAR}."
                )
        return value


def _as_percent(value) -> str:
    """0.089 -> "8.9", 1.5 -> "150": the rate as the person typed it."""
    pct = (Decimal(str(value)) * 100).normalize()
    return format(pct, "f")


# The places a document's tax rate keeps (Numeric(7, 6) on every document).
TAX_RATE_PLACES = Decimal("0.000001")


def _check_tax_rate(value):
    """A document tax rate is a FRACTION of the subtotal (0.089 = 8.9%).
    The company default in Settings (``default_tax_rate``) is a PERCENT
    string ("8.9") because it is what the user types; the invoice form
    divides by 100 before posting. An agent that copies the setting onto
    a document books 890% tax with a 201 (2.9.0 gate: $1.94M of tax on
    $731K of revenue, and the trial balance still balanced). Anything
    above 1 cannot be a fraction, so reject it and say which unit.

    The same sentence reaches a person on the invoice form, who typed a
    percent (or had one filled in from Settings), so it leads in percent
    and says where a bad default lives; the unit note for API callers
    follows (explore 2.17.3: "tax_rate: Value error, tax_rate cannot be
    negative" under a field labelled "Tax Rate (%)")."""
    if value is None:
        return value
    rate = Decimal(str(value))
    if not rate.is_finite():
        raise ValueError("Tax rate must be a number from 0 to 100%.")
    if value > 1:
        raise ValueError(
            f"Tax rate {_as_percent(value)}% is more than 100%. Use a rate "
            "from 0 to 100%; if it came from the company default, correct "
            "Default Tax Rate in Settings. (API: tax_rate is a fraction of "
            f"the subtotal, 0.089 = 8.9%, so {value} looks like a percent; "
            "divide by 100.)"
        )
    if value < 0:
        raise ValueError(
            f"Tax rate {_as_percent(value)}% is negative. Use a rate from 0 "
            "to 100%; if it came from the company default, correct Default "
            "Tax Rate in Settings."
        )
    # A rate typed as a percent reaches us divided by 100 in floating point:
    # 7.25% arrives as 0.07249999999999999, which put a half-cent tax a
    # cent low. A document keeps its rate to six places, a percent to four
    # (New York City's 8.875% is 0.08875), so it is cut to six here and tax
    # is worked out from exactly the rate that is stored. A finer rate
    # rounds half up, a float by the digits it shows (0.0000005 is 0.000001).
    rate = rate.quantize(TAX_RATE_PLACES, rounding=ROUND_HALF_UP)
    return rate if isinstance(value, Decimal) else float(rate)


_TAX_RATE_DOC = dict(
    description=(
        "Tax rate as a FRACTION of the taxable subtotal: 0.089 means 8.9%. "
        "Not a percent — Settings.default_tax_rate is the percent form "
        "('8.9'); divide it by 100 before sending. Values above 1 are rejected. "
        "Kept to six places, a percent to four (0.08875 means 8.875%); a finer "
        "rate is rounded half up."
    ),
    examples=[0.089],
    json_schema_extra={"minimum": 0, "maximum": 1},
)

# Reusable annotated types: `tax_rate: TaxRate = Decimal("0")`.
TaxRate = Annotated[Decimal, AfterValidator(_check_tax_rate), Field(**_TAX_RATE_DOC)]
TaxRateFloat = Annotated[float, AfterValidator(_check_tax_rate), Field(**_TAX_RATE_DOC)]


def _tax_rate_json(value) -> str:
    """A document's tax rate as the API writes it: four places as before
    ("0.0825"), up to six when it has them ("0.08875"). Rates are stored to
    six places now, and "0.082500" on every document would be noise."""
    rate = Decimal(str(value))
    if rate == rate.quantize(Decimal("0.0001")):
        return f"{rate:.4f}"
    return format(rate.normalize(), "f")


# A document's tax rate in a response.
TaxRateOut = Annotated[
    Decimal, PlainSerializer(_tax_rate_json, return_type=str, when_used="json")
]


# The most a discount can be: the money columns are Numeric(15, 2).
DISCOUNT_MAX = Decimal("9999999999999.99")


def _check_discount(value):
    """An early-payment discount (#664) in a sentence when it is past what
    a money column holds (1e30 was a 500 when the route rounded it). Whole
    cents are checked where it is taken, beside the account."""
    if value is None:
        return value
    if abs(value) > DISCOUNT_MAX:
        raise ValueError(f"A discount is at most ${DISCOUNT_MAX:,.2f}.")
    return value


Discount = Annotated[Decimal, AfterValidator(_check_discount)]


# A row id a request carries, for a column stored as a signed 64-bit integer.
# StrictInt refuses a bool (``true`` was read as id 1) and a string; the
# bounds keep an id out of the range that overflowed SQLite with a 500
# (#665: discount_account_id/invoice_id/bill_id of 2**63 or more). The 422's
# sentence names the field ("Discount account must be ... or less.").
BIGINT_MAX = 9223372036854775807

StrictId = Annotated[StrictInt, Field(ge=1, le=BIGINT_MAX)]


# Money is kept in whole cents (#747). A payment or an allocation finer
# than a cent was rounded on its own when stored: two allocations of 0.005
# passed the "allocations exceed payment" check as 0.01 and were stored as
# 0.01 each, two cents applied from a one-cent payment. Such an amount is
# refused, not rounded, so what passes the checks is what is stored.
def _finer_than_cents(value: Decimal) -> bool:
    """Whether a finite Decimal has a nonzero digit past the cents, judged
    on its digits (a quantize would round, or raise past 28 digits)."""
    if not value.is_finite():
        return False
    _sign, digits, exponent = value.as_tuple()
    if exponent >= -2:
        return False
    return any(digits[exponent + 2 :])


def _plain(value: Decimal) -> str:
    """The amount as sent, without an exponent or trailing zeros."""
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def whole_cents(label: str):
    """A validator refusing an amount finer than a cent, in a sentence that
    names the field by `label` ("A payment amount is in whole cents; 0.005
    isn't."). It judges the decimal the client sent, a JSON number or a
    string alike, so it runs before a float field converts the value; a
    value that isn't a number is left to the field's own validation."""

    def check(value):
        if value is None or isinstance(value, bool):
            return value
        try:
            exact = value if isinstance(value, Decimal) else Decimal(str(value).strip())
        except (ArithmeticError, ValueError, TypeError):
            return value
        if _finer_than_cents(exact):
            raise ValueError(f"{label} is in whole cents; {_plain(exact)} isn't.")
        return value

    return check


def WholeCents(label: str, kind=Decimal):
    """A money field of `kind` (Decimal, or float where a route still does
    float arithmetic) that refuses anything finer than a cent (#747)."""
    return Annotated[kind, BeforeValidator(whole_cents(label))]
