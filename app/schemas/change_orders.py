from datetime import date as dt_date, datetime
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, field_validator

from app.models.change_orders import CO_FUNDING, CO_KINDS
from app.schemas.common import MAX_PERCENT, StrictModel, choice_error

_KIND_LABELS = dict(
    pco="Potential change order", owner="Owner change order", sub="Sub change order"
)
_FUNDING_LABELS = dict(
    contract="Contract", allowance="Allowance", contingency="Contingency"
)


def _bounded_markup(v):
    """A markup percent must keep the sell above zero (more than -100%) and
    fit its Numeric(7, 4) column (at most 999.9999%; 1000 overflowed
    Postgres)."""
    if v is not None and not (Decimal("-100") < v <= MAX_PERCENT):
        raise ValueError(f"Markup must be above -100% and at most {MAX_PERCENT}%.")
    return v


# A line's quantity and unit cost are Numeric(15, 4): eleven whole digits.
_LINE_LIMIT = Decimal("100000000000")


class ChangeOrderLineIn(StrictModel):
    description: Optional[str] = None
    cost_type: str
    cost_code_id: Optional[int] = None
    trade_package_id: Optional[int] = None
    quantity: Decimal = Decimal("1")
    unit: Optional[str] = None
    unit_cost: Decimal = Decimal("0")
    markup_pct: Optional[Decimal] = None

    _markup = field_validator("markup_pct")(_bounded_markup)

    @field_validator("quantity", "unit_cost")
    @classmethod
    def _in_column(cls, v, info):
        if abs(v) >= _LINE_LIMIT:
            what = "Quantity" if info.field_name == "quantity" else "Unit cost"
            raise ValueError(
                f"{what} must be between -{_LINE_LIMIT:,} and {_LINE_LIMIT:,}."
            )
        return v


class ChangeOrderCreate(StrictModel):
    job_id: int
    kind: str = "owner"
    title: Optional[str] = None
    description: Optional[str] = None
    date: Optional[dt_date] = None
    funding: str = "contract"
    allowance_sov_line_id: Optional[int] = None
    markup_pct: Optional[Decimal] = None
    # None = the default for the funding: on for contract, off otherwise.
    apply_markup: Optional[bool] = None
    commitment_id: Optional[int] = None
    owner_change_order_id: Optional[int] = None
    lines: List[ChangeOrderLineIn] = []

    _markup = field_validator("markup_pct")(_bounded_markup)

    @field_validator("kind")
    @classmethod
    def _kind(cls, v):
        v = (v or "owner").strip().lower()
        if v not in CO_KINDS:
            raise choice_error("Kind", _KIND_LABELS, "kind")
        return v

    @field_validator("funding")
    @classmethod
    def _funding(cls, v):
        v = (v or "contract").strip().lower()
        if v not in CO_FUNDING:
            raise choice_error("Funding", _FUNDING_LABELS, "funding")
        return v


class ChangeOrderUpdate(StrictModel):
    """Edit a draft/pending change order. A field left out is unchanged."""

    title: Optional[str] = None
    description: Optional[str] = None
    date: Optional[dt_date] = None
    funding: Optional[str] = None
    allowance_sov_line_id: Optional[int] = None
    markup_pct: Optional[Decimal] = None
    apply_markup: Optional[bool] = None
    commitment_id: Optional[int] = None
    owner_change_order_id: Optional[int] = None
    lines: Optional[List[ChangeOrderLineIn]] = None

    _markup = field_validator("markup_pct")(_bounded_markup)

    @field_validator("funding")
    @classmethod
    def _funding(cls, v):
        if v is None:
            return v
        v = v.strip().lower()
        if v not in CO_FUNDING:
            raise choice_error("Funding", _FUNDING_LABELS, "funding")
        return v


class ApproveIn(StrictModel):
    # The day the change order takes effect. None = the change order's own
    # date, else today.
    approved_on: Optional[dt_date] = None


class AllowanceReturnIn(StrictModel):
    allowance_sov_line_id: int
    # None = everything the allowance has unspent.
    amount: Optional[Decimal] = None
    # The change order's date: approving it cannot take effect earlier.
    # None = today.
    date: Optional[dt_date] = None


class PromoteIn(StrictModel):
    # Roll several PCOs (all on one job, same funding) into one owner CO. The
    # PCO in the URL is included automatically. Empty = just that PCO.
    pco_ids: Optional[List[int]] = None


class SubOrderIn(StrictModel):
    commitment_id: int
    line_ids: Optional[List[int]] = None  # None = the subcontract lines


class ChangeOrderLineOut(BaseModel):
    id: int
    line_order: Optional[int] = None
    description: Optional[str] = None
    cost_type: str
    cost_code_id: Optional[int] = None
    cost_code_label: Optional[str] = None
    trade_package_id: Optional[int] = None
    trade_package_name: Optional[str] = None
    quantity: Decimal
    unit: Optional[str] = None
    unit_cost: Decimal
    amount: Decimal
    markup_pct: Optional[Decimal] = None
    sell_amount: Decimal

    model_config = {"from_attributes": True}


class ChangeOrderOut(BaseModel):
    id: int
    job_id: int
    number: str
    kind: str
    status: str
    title: Optional[str] = None
    description: Optional[str] = None
    date: Optional[dt_date] = None
    funding: str
    allowance_sov_line_id: Optional[int] = None
    markup_pct: Optional[Decimal] = None
    apply_markup: bool = True
    commitment_id: Optional[int] = None
    owner_change_order_id: Optional[int] = None
    source_pco_id: Optional[int] = None
    revision: int = 1
    supersedes_id: Optional[int] = None
    promoted_to_id: Optional[int] = None
    # On an owner CO promoted from PCOs: every PCO rolled into it.
    source_pco_ids: List[int] = []
    approved_at: Optional[datetime] = None
    approved_on: Optional[dt_date] = None
    allowance_return: bool = False
    voided_at: Optional[datetime] = None
    lines: List[ChangeOrderLineOut] = []
    # Server-computed roll-ups (cost / markup / sell, and per cost type).
    cost_total: float = 0.0
    markup_total: float = 0.0
    sell_total: float = 0.0
    by_cost_type: dict = {}
    # Approve only: what the approval did that is worth a second look.
    warnings: List[str] = []

    model_config = {"from_attributes": True}
