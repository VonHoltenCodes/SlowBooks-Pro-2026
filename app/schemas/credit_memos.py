from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from pydantic import BaseModel


class CreditMemoLineCreate(BaseModel):
    item_id: Optional[int] = None
    description: Optional[str] = None
    quantity: float = 1
    rate: float = 0
    gst_code: str = "GST15"
    gst_rate: Decimal = Decimal("0.1500")
    line_order: int = 0


class CreditMemoLineResponse(BaseModel):
    id: int
    item_id: Optional[int] = None
    description: Optional[str] = None
    quantity: float = 1
    rate: float = 0
    amount: float = 0
    gst_code: str = "GST15"
    gst_rate: Decimal = Decimal("0.1500")
    line_order: int = 0
    model_config = {"from_attributes": True}


class CreditApplicationCreate(BaseModel):
    invoice_id: int
    amount: float


class CreditMemoCreate(BaseModel):
    customer_id: int
    date: date
    original_invoice_id: Optional[int] = None
    tax_rate: float = 0
    notes: Optional[str] = None
    lines: list[CreditMemoLineCreate] = []


class CreditMemoResponse(BaseModel):
    id: int
    memo_number: str
    customer_id: int
    customer_name: Optional[str] = None
    status: str
    original_invoice_id: Optional[int] = None
    date: date
    subtotal: float = 0
    tax_rate: float = 0
    tax_amount: float = 0
    total: float = 0
    amount_applied: float = 0
    balance_remaining: float = 0
    notes: Optional[str] = None
    lines: list[CreditMemoLineResponse] = []
    created_at: Optional[datetime] = None
    model_config = {"from_attributes": True}
