from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from pydantic import BaseModel


class RecurringLineCreate(BaseModel):
    item_id: Optional[int] = None
    description: Optional[str] = None
    quantity: float = 1
    rate: float = 0
    gst_code: str = "GST15"
    gst_rate: Decimal = Decimal("0.1500")
    line_order: int = 0


class RecurringLineResponse(BaseModel):
    id: int
    item_id: Optional[int] = None
    description: Optional[str] = None
    quantity: float = 1
    rate: float = 0
    gst_code: str = "GST15"
    gst_rate: Decimal = Decimal("0.1500")
    line_order: int = 0
    model_config = {"from_attributes": True}


class RecurringCreate(BaseModel):
    customer_id: int
    frequency: str  # weekly, monthly, quarterly, yearly
    start_date: date
    end_date: Optional[date] = None
    terms: str = "Net 30"
    tax_rate: float = 0
    notes: Optional[str] = None
    lines: list[RecurringLineCreate] = []


class RecurringUpdate(BaseModel):
    frequency: Optional[str] = None
    end_date: Optional[date] = None
    is_active: Optional[bool] = None
    terms: Optional[str] = None
    tax_rate: Optional[float] = None
    notes: Optional[str] = None
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
    invoices_created: int = 0
    lines: list[RecurringLineResponse] = []
    model_config = {"from_attributes": True}
