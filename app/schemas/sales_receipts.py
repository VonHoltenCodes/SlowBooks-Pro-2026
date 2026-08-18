from datetime import date as dt_date
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, field_validator

from app.schemas.invoices import InvoiceLineCreate, InvoiceResponse
from app.schemas.payments import PaymentResponse


class SalesReceiptCreate(BaseModel):
    """One-screen sales receipt: an invoice and its full payment entered
    together (QB's "Enter Sales Receipts"). No terms/due date — payment is
    at the time of sale."""

    customer_id: int
    date: dt_date
    tax_rate: Decimal = Decimal("0")
    method: Optional[str] = None
    check_number: Optional[str] = None
    reference: Optional[str] = None
    deposit_to_account_id: Optional[int] = None
    notes: Optional[str] = None
    class_id: Optional[int] = None
    currency: Optional[str] = None
    exchange_rate: Optional[Decimal] = None
    lines: list[InvoiceLineCreate] = []

    @field_validator("lines")
    @classmethod
    def _require_lines(cls, v):
        if not v:
            raise ValueError("sales receipt must have at least one line")
        return v


class SalesReceiptResponse(BaseModel):
    invoice: InvoiceResponse
    payment: PaymentResponse
