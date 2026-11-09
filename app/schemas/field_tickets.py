from datetime import date as dt_date, datetime
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, field_validator

from app.models.field_tickets import FT_KINDS, FT_LINE_TYPES
from app.schemas.common import StrictModel, choice_error

# A line's quantity and rates are Numeric(15, 4): eleven whole digits.
_LINE_LIMIT = Decimal("100000000000")
_LINE_TYPE_LABELS = dict(
    labor="Labor", equipment="Equipment", material="Material", other="Other"
)
_KIND_LABELS = dict(
    tm="T&M ticket", material="Material slip", delivery="Delivery ticket"
)
# The list filter's status choices, as the screen names them.
STATUS_LABELS = dict(draft="Draft", signed="Signed", closed="Closed", void="Void")
KIND_LABELS = _KIND_LABELS


class FieldTicketLineIn(StrictModel):
    line_type: str = "other"
    employee_id: Optional[int] = None
    equipment_id: Optional[int] = None
    item_id: Optional[int] = None
    po_line_id: Optional[int] = None
    description: Optional[str] = None
    quantity: Decimal = Decimal("0")
    unit: Optional[str] = None
    # Blank takes the default: the employee's loaded rate, the equipment's
    # hourly rate, the item's average cost; the bill rate is the cost rate
    # with the job's change-order markup.
    cost_rate: Optional[Decimal] = None
    bill_rate: Optional[Decimal] = None
    cost_code_id: Optional[int] = None
    cost_type: Optional[str] = None

    @field_validator("line_type")
    @classmethod
    def _line_type(cls, v):
        v = (v or "other").strip().lower()
        if v not in FT_LINE_TYPES:
            raise choice_error("Line type", _LINE_TYPE_LABELS, "line_type")
        return v

    @field_validator("quantity")
    @classmethod
    def _quantity(cls, v):
        if abs(v) >= _LINE_LIMIT:
            raise ValueError(f"Quantity must be less than {_LINE_LIMIT:,}.")
        return v

    @field_validator("cost_rate", "bill_rate")
    @classmethod
    def _rate(cls, v, info):
        if v is not None and v < 0:
            raise ValueError("a rate cannot be negative")
        if v is not None and v >= _LINE_LIMIT:
            what = "Cost rate" if info.field_name == "cost_rate" else "Bill rate"
            raise ValueError(f"{what} must be less than {_LINE_LIMIT:,}.")
        return v


class FieldTicketCreate(StrictModel):
    job_id: int
    kind: str = "tm"
    date: Optional[dt_date] = None
    cost_code_id: Optional[int] = None
    description: Optional[str] = None
    submitted_by: Optional[str] = None
    commitment_id: Optional[int] = None
    purchase_order_id: Optional[int] = None
    vendor_ticket_ref: Optional[str] = None
    # A delivery that takes a PO line past its ordered quantity is refused
    # unless the request says it is meant.
    over_receipt: bool = False
    lines: List[FieldTicketLineIn] = []

    @field_validator("kind")
    @classmethod
    def _kind(cls, v):
        v = (v or "tm").strip().lower()
        if v not in FT_KINDS:
            raise choice_error("Kind", _KIND_LABELS, "kind")
        return v


class FieldTicketUpdate(StrictModel):
    """Edit a draft ticket. A field left out is unchanged."""

    date: Optional[dt_date] = None
    cost_code_id: Optional[int] = None
    description: Optional[str] = None
    submitted_by: Optional[str] = None
    commitment_id: Optional[int] = None
    purchase_order_id: Optional[int] = None
    vendor_ticket_ref: Optional[str] = None
    over_receipt: bool = False
    lines: Optional[List[FieldTicketLineIn]] = None


class SignIn(StrictModel):
    signed_by_name: str
    signed_at: Optional[dt_date] = None

    @field_validator("signed_by_name")
    @classmethod
    def _name(cls, v):
        v = (v or "").strip()
        if not v:
            raise ValueError("Type the name of the person who signed the ticket.")
        return v[:120]


class TicketIdsIn(StrictModel):
    ticket_ids: List[int]


class BillIn(TicketIdsIn):
    # The invoice date; blank is today
    date: Optional[dt_date] = None


class PostIn(StrictModel):
    date: Optional[dt_date] = None


class VoidIn(StrictModel):
    """Void takes nothing: an unknown field is a 422, as on post."""


class FieldTicketLineOut(BaseModel):
    id: int
    line_order: int
    line_type: str
    employee_id: Optional[int] = None
    employee_name: Optional[str] = None
    equipment_id: Optional[int] = None
    equipment_name: Optional[str] = None
    item_id: Optional[int] = None
    item_name: Optional[str] = None
    po_line_id: Optional[int] = None
    description: Optional[str] = None
    quantity: float
    unit: Optional[str] = None
    cost_rate: float
    bill_rate: Optional[float] = None
    cost_code_id: Optional[int] = None
    cost_code_label: Optional[str] = None
    cost_type: Optional[str] = None
    cost_amount: float = 0
    bill_amount: Optional[float] = None
    # Delivery: the PO line's ordered and received-to-date quantity
    ordered_qty: Optional[float] = None
    received_qty: Optional[float] = None
    # Delivery: this line took the PO line past what was ordered
    over_receipt: bool = False


class FieldTicketOut(BaseModel):
    id: int
    job_id: int
    kind: str
    number: str
    date: dt_date
    status: str
    cost_code_id: Optional[int] = None
    cost_code_label: Optional[str] = None
    description: Optional[str] = None
    submitted_by: Optional[str] = None
    signed_by_name: Optional[str] = None
    signed_at: Optional[dt_date] = None
    commitment_id: Optional[int] = None
    commitment_number: Optional[str] = None
    purchase_order_id: Optional[int] = None
    purchase_order_number: Optional[str] = None
    vendor_ticket_ref: Optional[str] = None
    change_order_id: Optional[int] = None
    change_order_number: Optional[str] = None
    sub_change_order_id: Optional[int] = None
    sub_change_order_number: Optional[str] = None
    invoice_id: Optional[int] = None
    invoice_number: Optional[str] = None
    job_cost_id: Optional[int] = None
    job_cost_number: Optional[str] = None
    voided_at: Optional[datetime] = None
    cost_total: float = 0
    bill_total: Optional[float] = None
    attachment_count: int = 0
    # Whether Void would be accepted now (the tab offers it only then)
    can_void: bool = False
    # Over-receipts on this ticket and, on a posted slip, stock gone short
    warnings: List[str] = []
    lines: List[FieldTicketLineOut] = []
