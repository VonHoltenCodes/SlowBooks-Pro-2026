from datetime import date as dt_date, datetime
from decimal import Decimal
from typing import Optional
from pydantic import BaseModel, field_validator, model_validator

from app.schemas.common import validate_non_negative_line


class BillLineCreate(BaseModel):
    item_id: Optional[int] = None
    account_id: Optional[int] = None
    description: Optional[str] = None
    quantity: float = 1
    rate: float = 0
    line_order: int = 0

    @model_validator(mode="after")
    def _check_non_negative(self):
        validate_non_negative_line(self.quantity, self.rate)
        return self


class BillLineResponse(BaseModel):
    id: int
    item_id: Optional[int] = None
    account_id: Optional[int] = None
    description: Optional[str] = None
    quantity: Decimal = Decimal("0")
    rate: Decimal = Decimal("0")
    amount: Decimal = Decimal("0")
    line_order: int = 0
    model_config = {"from_attributes": True}


class BillCreate(BaseModel):
    vendor_id: int
    bill_number: str
    date: dt_date
    due_date: Optional[dt_date] = None
    terms: str = "Net 30"
    ref_number: Optional[str] = None
    po_id: Optional[int] = None
    tax_rate: float = 0
    notes: Optional[str] = None
    lines: list[BillLineCreate] = []

    @field_validator("lines")
    @classmethod
    def _require_lines(cls, v):
        if not v:
            raise ValueError("bill must have at least one line")
        return v


class BillUpdate(BaseModel):
    bill_number: Optional[str] = None
    date: Optional[dt_date] = None
    due_date: Optional[dt_date] = None
    terms: Optional[str] = None
    ref_number: Optional[str] = None
    tax_rate: Optional[float] = None
    notes: Optional[str] = None
    lines: Optional[list[BillLineCreate]] = None


class BillResponse(BaseModel):
    id: int
    bill_number: str
    vendor_id: int
    vendor_name: Optional[str] = None
    status: str
    po_id: Optional[int] = None
    date: dt_date
    due_date: Optional[dt_date] = None
    terms: Optional[str] = None
    ref_number: Optional[str] = None
    subtotal: Decimal = Decimal("0")
    tax_rate: Decimal = Decimal("0")
    tax_amount: Decimal = Decimal("0")
    total: Decimal = Decimal("0")
    amount_paid: Decimal = Decimal("0")
    balance_due: Decimal = Decimal("0")
    notes: Optional[str] = None
    lines: list[BillLineResponse] = []
    created_at: Optional[datetime] = None
    model_config = {"from_attributes": True}


class BillPaymentAllocationCreate(BaseModel):
    bill_id: int
    amount: float


class BillPaymentCreate(BaseModel):
    vendor_id: int
    date: dt_date
    amount: float
    method: Optional[str] = None
    check_number: Optional[str] = None
    pay_from_account_id: Optional[int] = None
    notes: Optional[str] = None
    allocations: list[BillPaymentAllocationCreate] = []


class BillPaymentResponse(BaseModel):
    id: int
    vendor_id: int
    vendor_name: Optional[str] = None
    date: dt_date
    amount: Decimal = Decimal("0")
    method: Optional[str] = None
    check_number: Optional[str] = None
    notes: Optional[str] = None
    created_at: Optional[datetime] = None
    model_config = {"from_attributes": True}
