from datetime import date as dt_date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional
from pydantic import (
    BaseModel,
    StrictBool,
    field_validator,
    model_validator,
    field_serializer,
)

from app.schemas.common import (
    Discount,
    StrictModel,
    TaxRateFloat,
    TaxRateOut,
    clean_line_cost_type,
    validate_non_negative_line,
    quantity_text,
)
from app.schemas.invoices import RateOut


class BillLineCreate(StrictModel):
    """One line of a bill: a quantity and a rate, or an amount alone (#391).

    A utility bill or a pest-control bill has an amount and no quantity or
    rate, so a line may send ``amount`` by itself. The row is stored as
    quantity 1 x rate = amount, which is what the bill says and what every
    reader of the line (the journal, job costing, the PDF, the exports)
    already works from; nothing downstream learns a new shape. A line that
    sends a quantity or a rate is priced from them as before, and an amount
    sent with them must agree to the cent or the line is refused: a client
    that sends both and means the amount would otherwise be told nothing.
    """

    item_id: Optional[int] = None
    account_id: Optional[int] = None
    job_id: Optional[int] = None
    class_id: Optional[int] = None
    cost_code_id: Optional[int] = None
    cost_type: Optional[str] = None
    purchase_order_line_id: Optional[int] = None
    function: Optional[str] = None
    is_billable: bool = False
    description: Optional[str] = None
    quantity: Optional[float] = None
    rate: Optional[float] = None
    amount: Optional[float] = None
    line_order: int = 0

    _cost_type = field_validator("cost_type")(clean_line_cost_type)

    @model_validator(mode="after")
    def _price_the_line(self):
        sent = None if self.amount is None else _cents(Decimal(str(self.amount)))
        if sent is not None and sent < 0:
            raise ValueError(
                "amount must be non-negative; use a vendor credit for refunds"
            )
        if sent and not self.quantity and not self.rate:
            # No quantity and no rate (absent, null or 0): the amount is the line.
            self.quantity, self.rate = 1, float(sent)
        else:
            # Priced from the quantity and rate; with neither and no amount
            # it is a description-only line, as before.
            self.quantity = 1 if self.quantity is None else self.quantity
            self.rate = self.rate or 0
        validate_non_negative_line(self.quantity, self.rate)
        priced = _cents(Decimal(str(self.quantity)) * Decimal(str(self.rate)))
        if sent is not None and abs(sent - priced) > Decimal("0.01"):
            raise ValueError(
                f"amount {sent:.2f} is not quantity x rate ({priced:.2f}); "
                "send the quantity and rate, or the amount alone"
            )
        self.amount = float(priced)
        return self


def _cents(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


LIEN_WAIVER_STATUSES = ("not_required", "pending", "conditional", "unconditional")


def _check_lien_waiver(v):
    if v is None:
        return v
    v = v.strip().lower()
    if v not in LIEN_WAIVER_STATUSES:
        raise ValueError(
            f"lien_waiver_status must be one of {', '.join(LIEN_WAIVER_STATUSES)}"
        )
    return v


class VendorCompliance(BaseModel):
    """What a Sub vendor's paperwork looks like at pay time (#553). A missing
    W-9 or COI only warns; an expired COI blocks payment unless overridden."""

    is_sub: bool = False
    w9_on_file: bool = False
    coi_expires: Optional[dt_date] = None
    coi_expired: bool = False


class BillLineResponse(BaseModel):
    id: int
    item_id: Optional[int] = None
    account_id: Optional[int] = None
    job_id: Optional[int] = None
    class_id: Optional[int] = None
    cost_code_id: Optional[int] = None
    cost_type: Optional[str] = None
    purchase_order_line_id: Optional[int] = None
    function: Optional[str] = None
    is_billable: bool = False
    description: Optional[str] = None
    quantity: Decimal = Decimal("0")
    rate: RateOut = Decimal("0")
    amount: Decimal = Decimal("0")
    line_order: int = 0
    model_config = {"from_attributes": True}

    @field_serializer("quantity")
    def _quantity(self, value):
        return quantity_text(value)


class BillCreate(StrictModel):
    vendor_id: int
    # The vendor's invoice number; blank → generated (date + vendor initials).
    bill_number: Optional[str] = None
    date: dt_date
    due_date: Optional[dt_date] = None
    terms: str = "Net 30"
    ref_number: Optional[str] = None
    po_id: Optional[int] = None
    tax_rate: TaxRateFloat = 0
    notes: Optional[str] = None
    class_id: Optional[int] = None
    job_id: Optional[int] = None
    currency: Optional[str] = None
    exchange_rate: Optional[Decimal] = None
    # Construction (#553): retainage held on a hand-entered bill, and the
    # sub's lien-waiver status. 0 <= retainage_held <= subtotal (checked in
    # the route, which knows the subtotal).
    retainage_held: Decimal = Decimal("0")
    lien_waiver_status: Optional[str] = None
    lines: list[BillLineCreate] = []

    _lien = field_validator("lien_waiver_status")(_check_lien_waiver)

    @field_validator("retainage_held")
    @classmethod
    def _retainage_non_negative(cls, v):
        if v is not None and v < 0:
            raise ValueError("retainage_held cannot be negative")
        return v

    @field_validator("lines")
    @classmethod
    def _require_lines(cls, v):
        if not v:
            raise ValueError("bill must have at least one line")
        return v


class BillUpdate(StrictModel):
    """An edit of a posted bill (#225): whatever is sent changes, the rest
    stays. Lines, when sent, replace the bill's lines."""

    vendor_id: Optional[int] = None
    bill_number: Optional[str] = None
    date: Optional[dt_date] = None
    due_date: Optional[dt_date] = None
    terms: Optional[str] = None
    ref_number: Optional[str] = None
    tax_rate: Optional[TaxRateFloat] = None
    notes: Optional[str] = None
    class_id: Optional[int] = None
    job_id: Optional[int] = None
    currency: Optional[str] = None
    exchange_rate: Optional[Decimal] = None
    retainage_held: Optional[Decimal] = None
    lien_waiver_status: Optional[str] = None
    lines: Optional[list[BillLineCreate]] = None

    _lien = field_validator("lien_waiver_status")(_check_lien_waiver)

    @field_validator("retainage_held")
    @classmethod
    def _retainage_non_negative(cls, v):
        if v is not None and v < 0:
            raise ValueError("retainage_held cannot be negative")
        return v


class BillResponse(BaseModel):
    id: int
    bill_number: str
    vendor_id: int
    vendor_name: Optional[str] = None
    status: str
    po_id: Optional[int] = None
    date: dt_date
    due_date: Optional[dt_date] = None
    terms: Optional[str] = None
    ref_number: Optional[str] = None
    subtotal: Decimal = Decimal("0")
    tax_rate: TaxRateOut = Decimal("0")
    tax_amount: Decimal = Decimal("0")
    total: Decimal = Decimal("0")
    amount_paid: Decimal = Decimal("0")
    balance_due: Decimal = Decimal("0")
    retainage_held: Decimal = Decimal("0")
    retainage_released: Decimal = Decimal("0")
    # True when retainage_held was set by hand; False while it follows the
    # commitment's rate.
    retainage_manual: bool = False
    lien_waiver_status: Optional[str] = None
    notes: Optional[str] = None
    class_id: Optional[int] = None
    job_id: Optional[int] = None
    currency: Optional[str] = None
    exchange_rate: Optional[Decimal] = None
    lines: list[BillLineResponse] = []
    # Set by the route from the bill's vendor (#553); the list and Pay Bills
    # read it to flag a Sub's missing W-9 or expired COI.
    vendor_compliance: Optional[VendorCompliance] = None
    created_at: Optional[datetime] = None
    # Set by void and edit only (W-67, the shape of an invoice's #642
    # warnings): a sentence for each stock item the reversal of the bill's
    # receipts took past what was on hand ("Widget: 2 on hand; this void
    # takes it to -3."). The void or edit posted; this says what it did.
    # Empty otherwise.
    warnings: list[str] = []
    # From the terms (#664): the early-payment discount the terms offer and
    # the last day to take it, as the document prints it; and a sentence
    # when the terms aren't on the list and aren't Net N, so the due date
    # was the old 30 days.
    discount_percent: Optional[Decimal] = None
    discount_date: Optional[dt_date] = None
    discount_line: Optional[str] = None
    terms_note: Optional[str] = None
    model_config = {"from_attributes": True}


class BillPaymentAllocationCreate(StrictModel):
    bill_id: int
    amount: float
    # An early-payment discount we took on this bill (#664), on top of
    # `amount`: posted to the payment's discount_account_id.
    discount: Discount = Decimal("0")


class BillPaymentCreate(StrictModel):
    vendor_id: int
    date: dt_date
    amount: float
    method: Optional[str] = None
    check_number: Optional[str] = None
    pay_from_account_id: Optional[int] = None
    notes: Optional[str] = None
    # No class_id or job_id (v2.22.0, #234): the schema accepted both and the
    # route dropped them — BillPayment has no column and the payment's
    # journal never carried them — so a client that sent one was told
    # nothing. Nothing sends them (Pay Bills offers neither), and a body
    # that does is now refused like any other unknown field.
    currency: Optional[str] = None
    exchange_rate: Optional[Decimal] = None
    # Pay a Sub whose COI has expired anyway (#553); recorded on the payment.
    coi_override: bool = False
    allocations: list[BillPaymentAllocationCreate] = []
    # Where the allocations' discounts post (#664): required when one takes
    # a discount, refused when none does.
    discount_account_id: Optional[int] = None
    # Take a discount the bill's terms don't offer: none at all, after its
    # date, more than its percent, or a second one (#664). Each refusal
    # names this field.
    allow_discount_override: StrictBool = False


class BillPaymentDiscountAdd(StrictModel):
    """An early-payment discount taken after the payment that paid the rest
    of the bill (#665), recorded against that payment."""

    bill_id: int
    discount: Discount
    discount_account_id: int
    allow_discount_override: StrictBool = False


class BillPaymentMemoUpdate(StrictModel):
    notes: Optional[str] = None


class BillPaymentAllocationResponse(BaseModel):
    bill_id: int
    amount: Decimal = Decimal("0")
    model_config = {"from_attributes": True}


class BillPaymentResponse(BaseModel):
    id: int
    vendor_id: int
    vendor_name: Optional[str] = None
    date: dt_date
    amount: Decimal = Decimal("0")
    method: Optional[str] = None
    check_number: Optional[str] = None
    notes: Optional[str] = None
    currency: Optional[str] = None
    exchange_rate: Optional[Decimal] = None
    is_voided: bool = False
    coi_override: bool = False
    coi_override_note: Optional[str] = None
    created_at: Optional[datetime] = None
    # Which bills this payment paid, and how much of each — the bill's view
    # lists its payments from here so one can be voided on screen.
    allocations: list[BillPaymentAllocationResponse] = []
    # The early-payment discounts taken with it (#664); empty when none.
    discounts: list["BillPaymentDiscountResponse"] = []
    # A discount taken against the terms (#665): who, on what, and why.
    discount_override: bool = False
    discount_override_note: Optional[str] = None
    model_config = {"from_attributes": True}


class BillPaymentDiscountResponse(BaseModel):
    """A discount we took with this payment: the vendor credit applied to
    the bill (#664)."""

    bill_id: int
    bill_number: Optional[str] = None
    amount: Decimal
    vendor_credit_id: int
    credit_number: str
    account_id: Optional[int] = None
    # taken against the terms, with allow_discount_override (#665)
    override: bool = False


BillPaymentResponse.model_rebuild()
