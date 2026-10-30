from datetime import date as dt_date, datetime
from decimal import Decimal
from typing import Annotated, Optional

from pydantic import (
    BaseModel,
    Field,
    PlainSerializer,
    field_validator,
    model_validator,
    field_serializer,
)

from app.models.invoices import InvoiceStatus
from app.schemas.common import (
    StrictModel,
    TaxRate,
    TaxRateOut,
    validate_non_negative_line,
    quantity_text,
)


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
        # A negative price is a discount; whether this line may carry one
        # depends on its item and on the invoice it is on, which the route
        # decides (routes/invoices/helpers.py, refuse_negative_lines).
        validate_non_negative_line(self.quantity, None)
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

    @field_serializer("quantity")
    def _quantity(self, value):
        return quantity_text(value)


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
    # The Ship To's country; left out, the customer's ship country, as a
    # ship street left out is the customer's (2.31.1 gate, round 3).
    ship_to_country: Optional[str] = Field(None, max_length=100)
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
    # An invoice that takes the customer past their credit limit is saved
    # only with this; otherwise 409 "credit_limit" (#256).
    allow_over_credit_limit: bool = False

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
    # The addresses (2.31.1 gate, W-72): sent, they correct the invoice's
    # Bill To street or its Ship To, the Ship To's country with it (round
    # 3), and nothing else of the Bill To (the name and country stay the
    # ones it was billed with). An edit that moves the invoice to another
    # customer takes that customer's, the payer's Bill To whole as create
    # does and the customer's Ship To, and a part sent with it wins over
    # the one copied: "" (or null) blanks that part. Left out, an edit
    # keeps what is stored, and a move copies it.
    bill_address1: Optional[str] = Field(None, max_length=200)
    bill_address2: Optional[str] = Field(None, max_length=200)
    bill_city: Optional[str] = Field(None, max_length=100)
    bill_state: Optional[str] = Field(None, max_length=50)
    bill_zip: Optional[str] = Field(None, max_length=20)
    ship_address1: Optional[str] = Field(None, max_length=200)
    ship_address2: Optional[str] = Field(None, max_length=200)
    ship_city: Optional[str] = Field(None, max_length=100)
    ship_state: Optional[str] = Field(None, max_length=50)
    ship_zip: Optional[str] = Field(None, max_length=20)
    ship_to_country: Optional[str] = Field(None, max_length=100)
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
    allow_over_credit_limit: bool = False


class ZeroTotalConfirmation(StrictModel):
    """The optional body of Duplicate and of an estimate's Convert: a copy
    that adds up to $0.00 is made only with ``allow_zero_total: true``, and
    one that takes the payer past their credit limit only with
    ``allow_over_credit_limit: true`` (#256; Duplicate since the 2.31.1
    gate, W-79)."""

    allow_zero_total: bool = False
    allow_over_credit_limit: bool = False


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
    # Whom the invoice was billed to, frozen with the address when it was
    # made (#638, W-59): the parent of a sub-customer billed with it, else
    # the customer. What the printed Bill To block shows.
    bill_to_customer_id: Optional[int] = None
    bill_to_name: Optional[str] = None
    bill_to_country: Optional[str] = None
    ship_address1: Optional[str]
    ship_address2: Optional[str]
    ship_city: Optional[str]
    ship_state: Optional[str]
    ship_zip: Optional[str]
    # The Ship To's country, frozen with the ship-to street (round 3): the
    # customer's own ship country when the invoice was made or moved to
    # it, or what a request gave. What the printed Ship To block shows.
    ship_to_country: Optional[str] = None
    subtotal: Decimal
    tax_rate: TaxRateOut
    tax_amount: Decimal
    total: Decimal
    amount_paid: Decimal
    balance_due: Decimal
    # Construction progress billing (#555): retainage held this period (to
    # Retainage Receivable, 1150), retainage billed back (released), and the
    # owner pay application this invoice came from. Zero/null otherwise.
    retainage_held: Decimal = Decimal("0")
    retainage_released: Decimal = Decimal("0")
    pay_application_id: Optional[int] = None
    notes: Optional[str]
    class_id: Optional[int] = None
    job_id: Optional[int] = None
    # Set by convert and duplicate only: the jobs of another customer the
    # copy left off, as "Customer: Job" (a stored job from before the rule
    # that an invoice carries its own customer's jobs). Empty otherwise.
    jobs_left_off: list[str] = []
    # Set by create and edit only (#642): a sentence for each stock item the
    # sale took past what was on hand ("Widget: 2 on hand; this sale takes
    # it to -4."). The sale posted; this says what it did. Empty otherwise.
    warnings: list[str] = []
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
    # From the terms (#664): the early-payment discount the terms offer and
    # the last day to take it, as the document prints it; and a sentence
    # when the terms aren't on the list and aren't Net N, so the due date
    # was the old 30 days.
    discount_percent: Optional[Decimal] = None
    discount_date: Optional[dt_date] = None
    discount_line: Optional[str] = None
    terms_note: Optional[str] = None
    customer_name: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
