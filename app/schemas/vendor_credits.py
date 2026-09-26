from datetime import date as dt_date, datetime
from decimal import Decimal
from typing import Optional
from pydantic import BaseModel, field_validator, model_validator

from app.schemas.common import StrictModel, TaxRateFloat, validate_non_negative_line
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
    line_order: int = 0

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
    line_order: int = 0
    model_config = {"from_attributes": True}


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
    tax_rate: Decimal = Decimal("0")
    tax_amount: Decimal = Decimal("0")
    total: Decimal = Decimal("0")
    amount_applied: Decimal = Decimal("0")
    balance_remaining: Decimal = Decimal("0")
    notes: Optional[str] = None
    class_id: Optional[int] = None
    job_id: Optional[int] = None
    lines: list[VendorCreditLineResponse] = []
    created_at: Optional[datetime] = None
    model_config = {"from_attributes": True}
