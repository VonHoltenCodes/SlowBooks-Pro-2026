from datetime import date as dt_date, datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel

from app.models.estimates import EstimateStatus


class EstimateLineCreate(BaseModel):
    item_id: Optional[int] = None
    description: Optional[str] = None
    quantity: Decimal = Decimal("1")
    rate: Decimal = Decimal("0")
    amount: Decimal = Decimal("0")
    class_name: Optional[str] = None
    line_order: int = 0


class EstimateLineResponse(BaseModel):
    id: int
    item_id: Optional[int]
    description: Optional[str]
    quantity: Decimal
    rate: Decimal
    amount: Decimal
    class_name: Optional[str]
    line_order: int

    model_config = {"from_attributes": True}


class EstimateCreate(BaseModel):
    customer_id: int
    date: dt_date
    expiration_date: Optional[dt_date] = None
    tax_rate: Decimal = Decimal("0")
    notes: Optional[str] = None
    lines: list[EstimateLineCreate] = []


class EstimateUpdate(BaseModel):
    customer_id: Optional[int] = None
    date: Optional[dt_date] = None
    expiration_date: Optional[dt_date] = None
    status: Optional[EstimateStatus] = None
    tax_rate: Optional[Decimal] = None
    notes: Optional[str] = None
    lines: Optional[list[EstimateLineCreate]] = None


class EstimateResponse(BaseModel):
    id: int
    estimate_number: str
    customer_id: int
    status: EstimateStatus
    date: dt_date
    expiration_date: Optional[dt_date]
    subtotal: Decimal
    tax_rate: Decimal
    tax_amount: Decimal
    total: Decimal
    notes: Optional[str]
    converted_invoice_id: Optional[int]
    lines: list[EstimateLineResponse] = []
    customer_name: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
