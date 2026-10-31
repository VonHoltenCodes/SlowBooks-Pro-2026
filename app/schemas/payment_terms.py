from datetime import date as dt_date
from decimal import Decimal
from typing import Annotated, Literal, Optional

from pydantic import (
    BaseModel,
    AfterValidator,
    StrictBool,
    StrictInt,
)

from app.schemas.common import StrictModel


def _clean(value):
    """NFKC, invisible format characters out (a zero-width space made a
    second "Net 30" on every list), then trimmed, and its length said in a
    sentence: a name is at most what a document's terms column holds (50)."""
    if not isinstance(value, str):
        return value
    from app.services.payment_terms import clean_name

    value = clean_name(value).strip()
    if not value:
        raise ValueError("A term needs a name.")
    if len(value) > 50:
        raise ValueError(
            f"A term name is at most 50 characters; this one is {len(value)}."
        )
    return value


# The documents' terms column is String(50), so a name fits it.
TermName = Annotated[str, AfterValidator(_clean)]
TermKind = Literal["standard", "date_driven"]


class PaymentTermCreate(StrictModel):
    name: TermName
    kind: TermKind = "standard"
    # Whole numbers only: true is not one day (StrictInt refuses a bool).
    net_days: Optional[StrictInt] = None
    due_day: Optional[StrictInt] = None
    cutoff_days: StrictInt = 0
    discount_percent: Optional[Decimal] = None
    discount_days: Optional[StrictInt] = None
    discount_day: Optional[StrictInt] = None
    is_active: StrictBool = True


class PaymentTermUpdate(StrictModel):
    """Whatever is sent changes; the rule as a whole is checked again. A
    rule field sent as null clears it (the discount, say)."""

    name: Optional[TermName] = None
    kind: Optional[TermKind] = None
    net_days: Optional[StrictInt] = None
    due_day: Optional[StrictInt] = None
    cutoff_days: Optional[StrictInt] = None
    discount_percent: Optional[Decimal] = None
    discount_days: Optional[StrictInt] = None
    discount_day: Optional[StrictInt] = None
    is_active: Optional[StrictBool] = None


class PaymentTermResponse(BaseModel):
    id: int
    name: str
    kind: str
    net_days: Optional[int] = None
    due_day: Optional[int] = None
    cutoff_days: int = 0
    discount_percent: Optional[Decimal] = None
    discount_days: Optional[int] = None
    discount_day: Optional[int] = None
    is_active: bool = True
    # The rule in words ("Due in 30 days; 2% off if paid within 10 days").
    description: str = ""
    # How many invoices and bills were saved under it; a term in use keeps
    # its rule and name.
    documents: int = 0
    # How many customers, vendors, recurring invoices and memorized bills
    # carry its name, and the default terms setting: what a change of its
    # rule reaches from here on (their next documents), not what it changes.
    elsewhere: int = 0

    model_config = {"from_attributes": True}


class TermsDatesResponse(BaseModel):
    """What a date and terms make of a document: the forms show it as the
    person picks, so the page never computes a due date of its own."""

    due_date: dt_date
    discount_percent: Optional[Decimal] = None
    discount_date: Optional[dt_date] = None
    # "2% discount if paid by Nov 10, 2026", as the document prints it.
    discount_line: Optional[str] = None
    # A sentence when the terms aren't understood: the due date is then the
    # old 30 days.
    note: Optional[str] = None
