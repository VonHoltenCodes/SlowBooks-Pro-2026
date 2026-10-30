from datetime import date as dt_date
from decimal import Decimal
from typing import Annotated, Literal, Optional

from pydantic import BaseModel, StringConstraints

from app.schemas.common import StrictModel

# The documents' terms column is String(50), so a name fits it.
TermName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)
]
TermKind = Literal["standard", "date_driven"]


class PaymentTermCreate(StrictModel):
    name: TermName
    kind: TermKind = "standard"
    net_days: Optional[int] = None
    due_day: Optional[int] = None
    cutoff_days: int = 0
    discount_percent: Optional[Decimal] = None
    discount_days: Optional[int] = None
    discount_day: Optional[int] = None
    is_active: bool = True


class PaymentTermUpdate(StrictModel):
    """Whatever is sent changes; the rule as a whole is checked again. A
    rule field sent as null clears it (the discount, say)."""

    name: Optional[TermName] = None
    kind: Optional[TermKind] = None
    net_days: Optional[int] = None
    due_day: Optional[int] = None
    cutoff_days: Optional[int] = None
    discount_percent: Optional[Decimal] = None
    discount_days: Optional[int] = None
    discount_day: Optional[int] = None
    is_active: Optional[bool] = None


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
    # How many invoices and bills carry it; a term in use keeps its rule.
    documents: int = 0

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
