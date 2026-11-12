from datetime import date as dt_date
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import StrictModel, TaxRate

from app.schemas.invoices import InvoiceLineCreate, InvoiceResponse
from app.schemas.payments import PaymentResponse


class SalesReceiptCreate(StrictModel):
    """One-screen sales receipt: an invoice and its full payment entered
    together (QB's "Enter Sales Receipts"). No terms/due date — payment is
    at the time of sale."""

    # Left out: a counter sale, recorded against the built-in walk-in
    # customer ("Walk-in Customer"; "Anonymous Donor" for a nonprofit).
    customer_id: Optional[int] = None
    date: dt_date
    tax_rate: TaxRate = Decimal("0")
    method: Optional[str] = None
    check_number: Optional[str] = None
    reference: Optional[str] = None
    deposit_to_account_id: Optional[int] = None
    notes: Optional[str] = None
    class_id: Optional[int] = None
    job_id: Optional[int] = None
    tax_exempt: Optional[bool] = Field(
        None,
        description="The document's Tax exempt switch (#864): true untaxes "
        "every line. Left out, a new one starts as its job is, else its "
        "customer, and an edit keeps what it had. A non-taxable customer's "
        "document is always exempt.",
    )
    currency: Optional[str] = None
    exchange_rate: Optional[Decimal] = None
    # Nonprofit donation receipt: what the donor got back, if anything
    fair_value_amount: Optional[Decimal] = None
    fair_value_description: Optional[str] = None
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
    # The receipt's customer by name, as GET /api/sales-receipts answers it
    # on each row ("Parent: Sub" for a sub-customer, the walk-in customer
    # for a counter sale): a client reading the top level found null (#810).
    customer_name: Optional[str] = Field(
        None,
        description=(
            "The receipt's customer by name, as GET /api/sales-receipts "
            "answers it; the same as invoice.customer_name."
        ),
    )
    # The invoice step's warnings (#642): a sentence for each stock item
    # the sale took past what was on hand. Empty when nothing was short.
    warnings: list[str] = []
