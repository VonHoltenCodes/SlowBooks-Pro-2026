from datetime import date as dt_date, datetime
from decimal import Decimal
from typing import Optional
from pydantic import BaseModel, field_validator, model_validator

from app.schemas.common import StrictModel, TaxRateFloat, validate_non_negative_line
from app.schemas.invoices import RateOut


class CreditMemoLineCreate(StrictModel):
    item_id: Optional[int] = None
    description: Optional[str] = None
    quantity: float = 1
    rate: float = 0
    # Whether the memo's tax rate applies to this line. None = the item's
    # flag (a non-taxable customer: never), as on an invoice line. Used for
    # the memo's tax; the line itself does not store it.
    is_taxable: Optional[bool] = None
    line_order: int = 0

    @model_validator(mode="after")
    def _check_non_negative(self):
        validate_non_negative_line(self.quantity, self.rate)
        return self


class CreditMemoLineResponse(BaseModel):
    id: int
    item_id: Optional[int] = None
    description: Optional[str] = None
    quantity: Decimal = Decimal("0")
    rate: RateOut = Decimal("0")
    amount: Decimal = Decimal("0")
    line_order: int = 0
    model_config = {"from_attributes": True}


class CreditApplicationCreate(StrictModel):
    invoice_id: int
    amount: float


class CreditMemoCreate(StrictModel):
    customer_id: int
    date: dt_date
    original_invoice_id: Optional[int] = None
    # Left out: the original invoice's rate when one is named, else 0 (the
    # Credit Memo form fills in the company's default rate itself).
    tax_rate: Optional[TaxRateFloat] = None
    notes: Optional[str] = None
    class_id: Optional[int] = None
    job_id: Optional[int] = None
    lines: list[CreditMemoLineCreate] = []
    # A credit memo for $0.00 is saved only when this says so; otherwise it
    # is refused with 409 "zero_total".
    allow_zero_total: bool = False

    @field_validator("lines")
    @classmethod
    def _require_lines(cls, v):
        if not v:
            raise ValueError("credit memo must have at least one line")
        return v


class CreditMemoResponse(BaseModel):
    id: int
    memo_number: str
    customer_id: int
    customer_name: Optional[str] = None
    status: str
    original_invoice_id: Optional[int] = None
    date: dt_date
    subtotal: Decimal = Decimal("0")
    tax_rate: Decimal = Decimal("0")
    tax_amount: Decimal = Decimal("0")
    total: Decimal = Decimal("0")
    amount_applied: Decimal = Decimal("0")
    balance_remaining: Decimal = Decimal("0")
    notes: Optional[str] = None
    class_id: Optional[int] = None
    job_id: Optional[int] = None
    is_write_off: bool = False
    lines: list[CreditMemoLineResponse] = []
    created_at: Optional[datetime] = None
    model_config = {"from_attributes": True}
