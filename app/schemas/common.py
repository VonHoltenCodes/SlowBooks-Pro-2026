from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, StringConstraints

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
