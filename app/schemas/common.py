import re
from decimal import Decimal
from typing import Annotated, Optional

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
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
    q = Decimal(str(quantity if quantity is not None else 0))
    r = Decimal(str(rate if rate is not None else 0))
    if q < 0:
        raise ValueError("quantity must be non-negative; use a credit memo for refunds")
    if r < 0:
        raise ValueError("rate must be non-negative; use a credit memo for refunds")


class StrictModel(BaseModel):
    """Base for every request body: unknown fields are a 422, not silence.

    Field report (2.9.0 gate, both platforms): ``POST /api/invoices`` with
    ``line_items`` instead of ``lines`` returned 201 and a $0.00 invoice.
    Pydantic's default is to drop keys it does not know, which is the
    worst possible answer for an API agents drive from the spec — the
    caller supplied the data and was told it worked. ``extra="forbid"``
    turns that into an error naming the field. Response models keep the
    default; only what the client sends is checked.
    """

    model_config = ConfigDict(extra="forbid")


def _as_percent(value) -> str:
    """0.089 -> "8.9", 1.5 -> "150": the rate as the person typed it."""
    pct = (Decimal(str(value)) * 100).normalize()
    return format(pct, "f")


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
    if value is not None and value > 1:
        raise ValueError(
            f"Tax rate {_as_percent(value)}% is more than 100%. Use a rate "
            "from 0 to 100%; if it came from the company default, correct "
            "Default Tax Rate in Settings. (API: tax_rate is a fraction of "
            f"the subtotal, 0.089 = 8.9%, so {value} looks like a percent; "
            "divide by 100.)"
        )
    if value is not None and value < 0:
        raise ValueError(
            f"Tax rate {_as_percent(value)}% is negative. Use a rate from 0 "
            "to 100%; if it came from the company default, correct Default "
            "Tax Rate in Settings."
        )
    return value


_TAX_RATE_DOC = dict(
    description=(
        "Tax rate as a FRACTION of the taxable subtotal: 0.089 means 8.9%. "
        "Not a percent — Settings.default_tax_rate is the percent form "
        "('8.9'); divide it by 100 before sending. Values above 1 are rejected."
    ),
    examples=[0.089],
    json_schema_extra={"minimum": 0, "maximum": 1},
)

# Reusable annotated types: `tax_rate: TaxRate = Decimal("0")`.
TaxRate = Annotated[Decimal, AfterValidator(_check_tax_rate), Field(**_TAX_RATE_DOC)]
TaxRateFloat = Annotated[float, AfterValidator(_check_tax_rate), Field(**_TAX_RATE_DOC)]
