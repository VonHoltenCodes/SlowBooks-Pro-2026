from datetime import date as dt_date, datetime
from decimal import Decimal
from typing import Optional
from pydantic import BaseModel, field_validator, model_validator

from app.schemas.common import validate_non_negative_line


class CreditMemoLineCreate(BaseModel):
    item_id: Optional[int] = None
    description: Optional[str] = None
    quantity: float = 1
    rate: float = 0
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
    rate: Decimal = Decimal("0")
    amount: Decimal = Decimal("0")
    line_order: int = 0
    model_config = {"from_attributes": True}


class CreditApplicationCreate(BaseModel):
    invoice_id: int
    amount: float


class CreditMemoCreate(BaseModel):
    customer_id: int
    date: dt_date
    original_invoice_id: Optional[int] = None
    tax_rate: float = 0
    notes: Optional[str] = None
    lines: list[CreditMemoLineCreate] = []

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
    lines: list[CreditMemoLineResponse] = []
    created_at: Optional[datetime] = None
    model_config = {"from_attributes": True}
