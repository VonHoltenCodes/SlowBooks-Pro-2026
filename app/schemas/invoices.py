from datetime import date as dt_date, datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel

from app.models.invoices import InvoiceStatus


class InvoiceLineCreate(BaseModel):
    item_id: Optional[int] = None
    description: Optional[str] = None
    quantity: Decimal = Decimal("1")
    rate: Decimal = Decimal("0")
    amount: Decimal = Decimal("0")
    class_name: Optional[str] = None
    line_order: int = 0


class InvoiceLineResponse(BaseModel):
    id: int
    item_id: Optional[int]
    description: Optional[str]
    quantity: Decimal
    rate: Decimal
    amount: Decimal
    class_name: Optional[str]
    line_order: int

    model_config = {"from_attributes": True}


class InvoiceCreate(BaseModel):
    customer_id: int
    date: dt_date
    due_date: Optional[dt_date] = None
    terms: str = "Net 30"
    po_number: Optional[str] = None
    bill_address1: Optional[str] = None
    bill_address2: Optional[str] = None
    bill_city: Optional[str] = None
    bill_state: Optional[str] = None
    bill_zip: Optional[str] = None
    ship_address1: Optional[str] = None
    ship_address2: Optional[str] = None
    ship_city: Optional[str] = None
    ship_state: Optional[str] = None
    ship_zip: Optional[str] = None
    tax_rate: Decimal = Decimal("0")
    notes: Optional[str] = None
    lines: list[InvoiceLineCreate] = []


class InvoiceUpdate(BaseModel):
    customer_id: Optional[int] = None
    date: Optional[dt_date] = None
    due_date: Optional[dt_date] = None
    terms: Optional[str] = None
    po_number: Optional[str] = None
    status: Optional[InvoiceStatus] = None
    tax_rate: Optional[Decimal] = None
    notes: Optional[str] = None
    lines: Optional[list[InvoiceLineCreate]] = None


class InvoiceResponse(BaseModel):
    id: int
    invoice_number: str
    customer_id: int
    status: InvoiceStatus
    date: dt_date
    due_date: Optional[dt_date]
    terms: Optional[str]
    po_number: Optional[str]
    bill_address1: Optional[str]
    bill_address2: Optional[str]
    bill_city: Optional[str]
    bill_state: Optional[str]
    bill_zip: Optional[str]
    ship_address1: Optional[str]
    ship_address2: Optional[str]
    ship_city: Optional[str]
    ship_state: Optional[str]
    ship_zip: Optional[str]
    subtotal: Decimal
    tax_rate: Decimal
    tax_amount: Decimal
    total: Decimal
    amount_paid: Decimal
    balance_due: Decimal
    notes: Optional[str]
    lines: list[InvoiceLineResponse] = []
    customer_name: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
