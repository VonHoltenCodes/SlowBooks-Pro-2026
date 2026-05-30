from datetime import date as dt_date
from decimal import Decimal
from typing import Optional
from pydantic import BaseModel, field_validator, model_validator

from app.schemas.common import validate_non_negative_line


class POLineCreate(BaseModel):
    item_id: Optional[int] = None
    description: Optional[str] = None
    quantity: float = 1
    rate: float = 0
    line_order: int = 0

    @model_validator(mode="after")
    def _check_non_negative(self):
        validate_non_negative_line(self.quantity, self.rate)
        return self


class POLineResponse(BaseModel):
    id: int
    item_id: Optional[int] = None
    description: Optional[str] = None
    quantity: Decimal = Decimal("0")
    rate: Decimal = Decimal("0")
    amount: Decimal = Decimal("0")
    received_qty: Decimal = Decimal("0")
    line_order: int = 0
    model_config = {"from_attributes": True}


class POCreate(BaseModel):
    vendor_id: int
    date: dt_date
    expected_date: Optional[dt_date] = None
    ship_to: Optional[str] = None
    tax_rate: float = 0
    notes: Optional[str] = None
    lines: list[POLineCreate] = []

    @field_validator("lines")
    @classmethod
    def _require_lines(cls, v):
        if not v:
            raise ValueError("purchase order must have at least one line")
        return v


class POUpdate(BaseModel):
    vendor_id: Optional[int] = None
    date: Optional[dt_date] = None
    expected_date: Optional[dt_date] = None
    ship_to: Optional[str] = None
    status: Optional[str] = None
    tax_rate: Optional[float] = None
    notes: Optional[str] = None
    lines: Optional[list[POLineCreate]] = None


class POResponse(BaseModel):
    id: int
    po_number: str
    vendor_id: int
    vendor_name: Optional[str] = None
    status: str
    date: dt_date
    expected_date: Optional[dt_date] = None
    ship_to: Optional[str] = None
    subtotal: Decimal = Decimal("0")
    tax_rate: Decimal = Decimal("0")
    tax_amount: Decimal = Decimal("0")
    total: Decimal = Decimal("0")
    notes: Optional[str] = None
    lines: list[POLineResponse] = []
    model_config = {"from_attributes": True}
