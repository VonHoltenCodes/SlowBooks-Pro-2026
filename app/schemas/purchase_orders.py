from datetime import date as dt_date
from decimal import Decimal
from typing import Optional
from pydantic import BaseModel, field_validator, model_validator, field_serializer

from app.schemas.common import (
    StrictModel,
    TaxRateFloat,
    TaxRateOut,
    clean_line_cost_type,
    validate_non_negative_line,
    check_line_fits,
    check_lines_fit,
    quantity_text,
)
from app.schemas.invoices import RateOut


class POLineCreate(StrictModel):
    # An existing line's id, sent on an update so its billed-to-date and the
    # bill links to it survive the edit (#553). Absent = a new line.
    id: Optional[int] = None
    item_id: Optional[int] = None
    description: Optional[str] = None
    quantity: float = 1
    rate: float = 0
    job_id: Optional[int] = None
    cost_code_id: Optional[int] = None
    cost_type: Optional[str] = None
    line_order: int = 0

    _cost_type = field_validator("cost_type")(clean_line_cost_type)

    @model_validator(mode="after")
    def _check_non_negative(self):
        validate_non_negative_line(self.quantity, self.rate)
        check_line_fits(self.quantity, self.rate)
        return self


class POLineResponse(BaseModel):
    id: int
    item_id: Optional[int] = None
    description: Optional[str] = None
    quantity: Decimal = Decimal("0")
    rate: RateOut = Decimal("0")
    amount: Decimal = Decimal("0")  # scheduled value of the line
    billed_amount: Decimal = Decimal("0")  # billed so far (#553)
    received_qty: Decimal = Decimal("0")
    job_id: Optional[int] = None
    cost_code_id: Optional[int] = None
    cost_type: Optional[str] = None
    line_order: int = 0
    model_config = {"from_attributes": True}

    @field_serializer("quantity")
    def _quantity(self, value):
        return quantity_text(value)


COMMITMENT_TYPES = ("po", "subcontract")

PO_STATUSES = ("draft", "sent", "partial", "received", "closed")


def _check_status(v):
    """A status the model knows, else a 422 that names the valid ones (an
    unknown word used to reach the enum and fail as a 500)."""
    if v is not None and v not in PO_STATUSES:
        raise ValueError(
            f"Unknown status '{v}'. Valid statuses are: {', '.join(PO_STATUSES)}."
        )
    return v


def _check_commitment_type(v):
    if v is None:
        return v
    v = v.strip().lower()
    if v not in COMMITMENT_TYPES:
        raise ValueError(
            f"commitment_type must be one of {', '.join(COMMITMENT_TYPES)}"
        )
    return v


def _check_retainage_pct(v):
    if v is not None and not (0 <= v <= 100):
        raise ValueError("Retainage must be between 0 and 100%.")
    return v


class POCreate(StrictModel):
    vendor_id: int
    date: dt_date
    expected_date: Optional[dt_date] = None
    ship_to: Optional[str] = None
    tax_rate: TaxRateFloat = 0
    notes: Optional[str] = None
    job_id: Optional[int] = None
    # Construction commitment fields (#553); every company accepts them, and
    # the defaults keep a plain PO exactly as it was.
    commitment_type: str = "po"
    trade_package_id: Optional[int] = None
    retainage_pct: Optional[Decimal] = None
    scope_notes: Optional[str] = None
    lines: list[POLineCreate] = []

    _ctype = field_validator("commitment_type")(_check_commitment_type)
    _ret = field_validator("retainage_pct")(_check_retainage_pct)

    @field_validator("lines")
    @classmethod
    def _require_lines(cls, v):
        if not v:
            raise ValueError("purchase order must have at least one line")
        check_lines_fit(v)
        return v


class POUpdate(StrictModel):
    vendor_id: Optional[int] = None
    date: Optional[dt_date] = None
    expected_date: Optional[dt_date] = None
    ship_to: Optional[str] = None
    status: Optional[str] = None
    tax_rate: Optional[TaxRateFloat] = None
    notes: Optional[str] = None
    job_id: Optional[int] = None
    commitment_type: Optional[str] = None
    trade_package_id: Optional[int] = None
    retainage_pct: Optional[Decimal] = None
    scope_notes: Optional[str] = None
    lines: Optional[list[POLineCreate]] = None

    @field_validator("lines")
    @classmethod
    def _lines_fit(cls, v):
        if v:
            check_lines_fit(v)
        return v

    _ctype = field_validator("commitment_type")(_check_commitment_type)
    _ret = field_validator("retainage_pct")(_check_retainage_pct)
    _status = field_validator("status")(_check_status)


class POConvertLineAccount(StrictModel):
    line_id: int  # a line of the purchase order being converted
    account_id: Optional[int] = None  # where that line posts on the bill
    # This period's amount for the line (#553 partial billing). None/absent =
    # bill the line's full remaining (amount - billed_amount).
    amount: Optional[Decimal] = None


class POConvertToBill(StrictModel):
    """The To Bill dialog's choices: an account for each line that needs one,
    and optionally the amount to bill this period (partial billing, #553).
    Lines left out post to their item's expense account, else the vendor's
    default, and bill their full remaining."""

    lines: list[POConvertLineAccount] = []
    # The bill's date (the dialog defaults it to today); the PO's own date
    # when left out.
    date: Optional[dt_date] = None


class POResponse(BaseModel):
    id: int
    po_number: str
    vendor_id: int
    job_id: Optional[int] = None
    vendor_name: Optional[str] = None
    status: str
    date: dt_date
    expected_date: Optional[dt_date] = None
    ship_to: Optional[str] = None
    commitment_type: str = "po"
    trade_package_id: Optional[int] = None
    trade_package_name: Optional[str] = None
    retainage_pct: Optional[Decimal] = None
    retainage_released: Decimal = Decimal("0")
    scope_notes: Optional[str] = None
    subtotal: Decimal = Decimal("0")
    tax_rate: TaxRateOut = Decimal("0")
    tax_amount: Decimal = Decimal("0")
    total: Decimal = Decimal("0")
    notes: Optional[str] = None
    lines: list[POLineResponse] = []
    model_config = {"from_attributes": True}
