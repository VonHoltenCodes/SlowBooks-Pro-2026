from datetime import date as dt_date, datetime
from decimal import Decimal
from typing import Optional
from pydantic import BaseModel, field_validator, model_validator, field_serializer

from app.schemas.common import (
    StrictModel,
    TaxRateFloat,
    TaxRateOut,
    clean_line_cost_type,
    validate_non_negative_line,
    quantity_text,
)
from app.schemas.invoices import RateOut


class VendorCreditLineCreate(StrictModel):
    item_id: Optional[int] = None
    account_id: Optional[int] = None
    description: Optional[str] = None
    quantity: float = 1
    rate: float = 0
    job_id: Optional[int] = None
    class_id: Optional[int] = None
    cost_code_id: Optional[int] = None
    cost_type: Optional[str] = None
    line_order: int = 0

    _cost_type = field_validator("cost_type")(clean_line_cost_type)

    @model_validator(mode="after")
    def _check_non_negative(self):
        # A credit is entered as a positive amount, the same way a bill is.
        # The direction is the document's job, not the operator's.
        validate_non_negative_line(self.quantity, self.rate)
        return self


class VendorCreditLineResponse(BaseModel):
    id: int
    item_id: Optional[int] = None
    account_id: Optional[int] = None
    description: Optional[str] = None
    quantity: Decimal = Decimal("0")
    rate: RateOut = Decimal("0")
    amount: Decimal = Decimal("0")
    job_id: Optional[int] = None
    class_id: Optional[int] = None
    cost_code_id: Optional[int] = None
    cost_type: Optional[str] = None
    line_order: int = 0
    model_config = {"from_attributes": True}

    @field_serializer("quantity")
    def _quantity(self, value):
        return quantity_text(value)


class VendorCreditApplicationCreate(StrictModel):
    bill_id: int
    amount: float


class VendorCreditCreate(StrictModel):
    vendor_id: int
    date: dt_date
    original_bill_id: Optional[int] = None
    ref_number: Optional[str] = None
    tax_rate: TaxRateFloat = 0
    notes: Optional[str] = None
    class_id: Optional[int] = None
    job_id: Optional[int] = None
    lines: list[VendorCreditLineCreate] = []

    @field_validator("lines")
    @classmethod
    def _require_lines(cls, v):
        if not v:
            raise ValueError("vendor credit must have at least one line")
        return v


class VendorCreditResponse(BaseModel):
    id: int
    credit_number: str
    vendor_id: int
    vendor_name: Optional[str] = None
    status: str
    original_bill_id: Optional[int] = None
    ref_number: Optional[str] = None
    date: dt_date
    subtotal: Decimal = Decimal("0")
    tax_rate: TaxRateOut = Decimal("0")
    tax_amount: Decimal = Decimal("0")
    total: Decimal = Decimal("0")
    amount_applied: Decimal = Decimal("0")
    balance_remaining: Decimal = Decimal("0")
    notes: Optional[str] = None
    class_id: Optional[int] = None
    job_id: Optional[int] = None
    # Set on an early-payment discount (#664): the bill payment it was
    # taken with.
    discount_bill_payment_id: Optional[int] = None
    # taken against the bill's terms, with allow_discount_override (#665)
    discount_override: bool = False
    lines: list[VendorCreditLineResponse] = []
    created_at: Optional[datetime] = None
    # Set by create only (W-74, the shape of an invoice's #642 warnings): a
    # sentence for each stock item the credit's return took past what was on
    # hand ("Widget: 2 on hand; this credit takes it to -3."). The credit
    # posted; this says what it did. Empty otherwise: a void puts stock back.
    warnings: list[str] = []
    model_config = {"from_attributes": True}
