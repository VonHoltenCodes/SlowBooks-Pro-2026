from datetime import date as dt_date, datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, field_validator, model_validator

from app.models.estimates import EstimateStatus
from app.schemas.common import validate_non_negative_line


class EstimateLineCreate(BaseModel):
    item_id: Optional[int] = None
    description: Optional[str] = None
    quantity: Decimal = Decimal("1")
    rate: Decimal = Decimal("0")
    amount: Decimal = Decimal("0")
    cost_code_id: Optional[int] = None
    unit_cost: Optional[Decimal] = None
    class_name: Optional[str] = None
    job_id: Optional[int] = None
    line_order: int = 0

    @model_validator(mode="after")
    def _check_non_negative(self):
        validate_non_negative_line(self.quantity, self.rate)
        return self


class EstimateLineResponse(BaseModel):
    id: int
    item_id: Optional[int]
    description: Optional[str]
    quantity: Decimal
    rate: Decimal
    amount: Decimal
    cost_code_id: Optional[int] = None
    unit_cost: Optional[Decimal] = None
    class_name: Optional[str]
    job_id: Optional[int] = None
    line_order: int

    model_config = {"from_attributes": True}


class EstimateCreate(BaseModel):
    customer_id: int
    date: dt_date
    expiration_date: Optional[dt_date] = None
    tax_rate: Decimal = Decimal("0")
    notes: Optional[str] = None
    class_id: Optional[int] = None
    job_id: Optional[int] = None
    lines: list[EstimateLineCreate] = []

    @field_validator("lines")
    @classmethod
    def _require_lines(cls, v):
        if not v:
            raise ValueError("estimate must have at least one line")
        return v


class EstimateUpdate(BaseModel):
    customer_id: Optional[int] = None
    date: Optional[dt_date] = None
    expiration_date: Optional[dt_date] = None
    status: Optional[EstimateStatus] = None
    tax_rate: Optional[Decimal] = None
    notes: Optional[str] = None
    class_id: Optional[int] = None
    job_id: Optional[int] = None
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
    class_id: Optional[int] = None
    job_id: Optional[int] = None
    converted_invoice_id: Optional[int]
    lines: list[EstimateLineResponse] = []
    customer_name: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
