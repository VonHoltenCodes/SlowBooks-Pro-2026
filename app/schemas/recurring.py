from datetime import date
from typing import Annotated, Literal, Optional
from pydantic import (
    BaseModel,
    BeforeValidator,
    Field,
    field_validator,
    model_validator,
    field_serializer,
)

from app.services.recurring_service import FREQUENCIES

from app.schemas.common import (
    StrictModel,
    TaxRateFloat,
    check_line_fits,
    validate_non_negative_line,
    quantity_text,
)

# The words the generator steps by (advance_date). Any other word was saved
# and then stepped as monthly, or never as anything anyone chose (#779);
# a recurring invoice has no interval, so QuickBooks' counted names
# (biweekly, every two months) are not among them.
FREQUENCY_SENTENCE = (
    "Frequency must be daily, weekly, semimonthly (twice a month), monthly, "
    "quarterly or yearly."
)


def _known_frequency(value):
    if isinstance(value, str) and value in FREQUENCIES:
        return value
    raise ValueError(FREQUENCY_SENTENCE)


# The Literal puts the allowed words in the OpenAPI schema as an enum; the
# check before it answers in one sentence instead of Pydantic's list.
Frequency = Annotated[
    Literal["daily", "weekly", "semimonthly", "monthly", "quarterly", "yearly"],
    BeforeValidator(_known_frequency),
]


class RecurringLineCreate(StrictModel):
    item_id: Optional[int] = None
    description: Optional[str] = None
    quantity: float = 1
    rate: float = 0
    is_taxable: Optional[bool] = None
    line_order: int = 0

    @model_validator(mode="after")
    def _check_non_negative(self):
        # each figure to its own column first (#782), then the product (W-145)
        validate_non_negative_line(self.quantity, self.rate)
        check_line_fits(self.quantity, self.rate)
        return self


class RecurringLineResponse(BaseModel):
    id: int
    item_id: Optional[int] = None
    description: Optional[str] = None
    quantity: float = 1
    rate: float = 0
    is_taxable: bool = True
    line_order: int = 0
    model_config = {"from_attributes": True}

    @field_serializer("quantity")
    def _quantity(self, value):
        return quantity_text(value)


class RecurringCreate(StrictModel):
    customer_id: int
    frequency: Frequency
    start_date: date
    end_date: Optional[date] = None
    terms: str = "Net 30"
    # An explicit pick of a list entry (#664). 1..2**63-1.
    payment_term_id: Optional[int] = Field(None, ge=1, le=9223372036854775807)
    tax_rate: TaxRateFloat = 0
    notes: Optional[str] = None
    class_id: Optional[int] = None
    job_id: Optional[int] = None
    lines: list[RecurringLineCreate] = []

    @field_validator("lines")
    @classmethod
    def _require_lines(cls, v):
        if not v:
            raise ValueError("recurring invoice must have at least one line")
        return v


class RecurringUpdate(StrictModel):
    frequency: Optional[Frequency] = None
    end_date: Optional[date] = None
    is_active: Optional[bool] = None
    terms: Optional[str] = None
    # An explicit pick of a list entry (#664). 1..2**63-1.
    payment_term_id: Optional[int] = Field(None, ge=1, le=9223372036854775807)
    tax_rate: Optional[TaxRateFloat] = None
    notes: Optional[str] = None
    class_id: Optional[int] = None
    job_id: Optional[int] = None
    lines: Optional[list[RecurringLineCreate]] = None


class RecurringResponse(BaseModel):
    id: int
    customer_id: int
    customer_name: Optional[str] = None
    frequency: str
    start_date: date
    end_date: Optional[date] = None
    next_due: date
    is_active: bool = True
    terms: Optional[str] = None
    tax_rate: float = 0
    notes: Optional[str] = None
    class_id: Optional[int] = None
    job_id: Optional[int] = None
    invoices_created: int = 0
    lines: list[RecurringLineResponse] = []
    model_config = {"from_attributes": True}


class GenerateRequest(StrictModel):
    """The optional body of `POST /api/recurring/{id}/generate`, as Enter
    now's on a memorized schedule: the installment the page showed and the
    date to give the invoice. Each may come in the query instead; sent both
    ways, the two must be the same date (#810)."""

    occurrence: Optional[date] = Field(
        None,
        description=(
            "The installment to enter, its scheduled date (the schedule's "
            "next due date when left out)."
        ),
    )
    dated: Optional[date] = Field(
        None,
        description="The date the invoice carries (its scheduled date when left out).",
    )
