from datetime import date as dt_date, datetime
from decimal import Decimal
from typing import Annotated, Optional

from pydantic import BaseModel, Field, PlainSerializer, field_validator, model_validator

from app.models.invoices import InvoiceStatus
from app.schemas.common import StrictModel, TaxRate, validate_non_negative_line


def _rate_json(value) -> str:
    """A unit price as the API writes it: two places as before ("12.50"),
    up to four when it has them ("0.045"). Sales line rates are stored to
    four places now, and "12.5000" on every line would be noise."""
    d = Decimal(str(value))
    if d == d.quantize(Decimal("0.01")):
        return f"{d:.2f}"
    return format(d.normalize(), "f")


# A sales line's unit price in a response.
RateOut = Annotated[
    Decimal, PlainSerializer(_rate_json, return_type=str, when_used="json")
]


class InvoiceLineCreate(StrictModel):
    item_id: Optional[int] = None
    description: Optional[str] = None
    quantity: Decimal = Decimal("1")
    rate: Decimal = Decimal("0")
    amount: Decimal = Decimal("0")
    class_name: Optional[str] = None
    job_id: Optional[int] = None
    class_id: Optional[int] = None
    cost_code_id: Optional[int] = None
    # None = default from the item (its taxable flag) and the customer
    is_taxable: Optional[bool] = None
    line_order: int = 0

    @model_validator(mode="after")
    def _check_non_negative(self):
        validate_non_negative_line(self.quantity, self.rate)
        return self


class InvoiceLineResponse(BaseModel):
    id: int
    item_id: Optional[int]
    description: Optional[str]
    quantity: Decimal
    rate: RateOut
    amount: Decimal
    class_name: Optional[str]
    job_id: Optional[int] = None
    class_id: Optional[int] = None
    cost_code_id: Optional[int] = None
    is_taxable: bool = True
    line_order: int

    model_config = {"from_attributes": True}


class InvoiceCreate(StrictModel):
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
    tax_rate: TaxRate = Decimal("0")
    notes: Optional[str] = None
    class_id: Optional[int] = None
    job_id: Optional[int] = None
    currency: Optional[str] = None
    exchange_rate: Optional[Decimal] = None
    lines: list[InvoiceLineCreate] = []
    # Nonprofit: pledge face; goods/services the donor received (gala dinner)
    is_pledge: bool = False
    fair_value_amount: Optional[Decimal] = None
    fair_value_description: Optional[str] = Field(None, max_length=200)
    # An invoice that adds up to $0.00 (no-charge warranty work) is saved
    # only when this says so; otherwise it is refused with 409 "zero_total".
    allow_zero_total: bool = False

    @field_validator("lines")
    @classmethod
    def _require_lines(cls, v):
        if not v:
            raise ValueError("invoice must have at least one line")
        return v


class InvoiceUpdate(StrictModel):
    customer_id: Optional[int] = None
    date: Optional[dt_date] = None
    due_date: Optional[dt_date] = None
    terms: Optional[str] = None
    po_number: Optional[str] = None
    status: Optional[InvoiceStatus] = None
    tax_rate: Optional[TaxRate] = None
    notes: Optional[str] = None
    class_id: Optional[int] = None
    job_id: Optional[int] = None
    currency: Optional[str] = None
    exchange_rate: Optional[Decimal] = None
    is_pledge: Optional[bool] = None
    fair_value_amount: Optional[Decimal] = None
    fair_value_description: Optional[str] = Field(None, max_length=200)
    lines: Optional[list[InvoiceLineCreate]] = None
    # As on create: an edit that leaves the invoice at $0.00 needs this.
    allow_zero_total: bool = False


class ZeroTotalConfirmation(StrictModel):
    """The optional body of Duplicate and of an estimate's Convert: a copy
    that adds up to $0.00 is made only with ``allow_zero_total: true``."""

    allow_zero_total: bool = False


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
    class_id: Optional[int] = None
    job_id: Optional[int] = None
    is_sales_receipt: bool = False
    is_pledge: bool = False
    fair_value_amount: Optional[Decimal] = None
    fair_value_description: Optional[str] = None
    recurring_invoice_id: Optional[int] = None
    currency: Optional[str] = None
    exchange_rate: Optional[Decimal] = None
    payment_token: Optional[str] = None
    checkout_provider: Optional[str] = None
    lines: list[InvoiceLineResponse] = []
    customer_name: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
