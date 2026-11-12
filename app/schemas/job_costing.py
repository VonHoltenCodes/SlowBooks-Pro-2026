from datetime import date as dt_date, datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator
from app.schemas.common import StrictModel

from app.models.job_costing import ALLOCATION_METHODS


def _strip_required(label):
    def _v(v):
        v = (v or "").strip()
        if not v:
            raise ValueError(f"{label} is required")
        return v

    return _v


# ── Cost types ───────────────────────────────────────────────────────────


class CostTypeCreate(StrictModel):
    code: str
    name: str
    is_labor: bool = False
    burden_pct: Optional[Decimal] = None
    burden_method: str = "flat"
    default_account_id: Optional[int] = None
    offset_account_id: Optional[int] = None
    burden_offset_account_id: Optional[int] = None
    sort_order: int = 0

    @field_validator("code")
    @classmethod
    def _code(cls, v):
        v = (v or "").strip().lower().replace(" ", "_")
        if not v or len(v) > 20:
            raise ValueError("Cost type code is required (20 characters max)")
        return v

    _name = field_validator("name")(_strip_required("Cost type name"))


class CostTypeUpdate(StrictModel):
    name: Optional[str] = None
    is_labor: Optional[bool] = None
    burden_pct: Optional[Decimal] = None
    burden_method: Optional[str] = None
    default_account_id: Optional[int] = None
    offset_account_id: Optional[int] = None
    burden_offset_account_id: Optional[int] = None
    sort_order: Optional[int] = None
    is_active: Optional[bool] = None


class CostTypeResponse(BaseModel):
    id: int
    code: str
    name: str
    is_labor: bool
    burden_pct: Optional[Decimal] = None
    burden_method: str = "flat"
    default_account_id: Optional[int] = None
    default_account_name: Optional[str] = None
    offset_account_id: Optional[int] = None
    offset_account_name: Optional[str] = None
    burden_offset_account_id: Optional[int] = None
    burden_offset_account_name: Optional[str] = None
    sort_order: int = 0
    is_active: bool

    model_config = {"from_attributes": True}


# ── Equipment ────────────────────────────────────────────────────────────


class EquipmentCreate(StrictModel):
    name: str
    code: Optional[str] = None
    hourly_rate: Decimal = Decimal("0")
    cost_code_id: Optional[int] = None
    recovery_account_id: Optional[int] = None
    notes: Optional[str] = None

    _name = field_validator("name")(_strip_required("Equipment name"))


class EquipmentUpdate(StrictModel):
    name: Optional[str] = None
    code: Optional[str] = None
    hourly_rate: Optional[Decimal] = None
    cost_code_id: Optional[int] = None
    recovery_account_id: Optional[int] = None
    notes: Optional[str] = None
    is_active: Optional[bool] = None


class EquipmentResponse(BaseModel):
    id: int
    code: Optional[str] = None
    name: str
    hourly_rate: Decimal
    cost_code_id: Optional[int] = None
    cost_code_label: Optional[str] = None
    recovery_account_id: Optional[int] = None
    notes: Optional[str] = None
    is_active: bool

    model_config = {"from_attributes": True}


# ── Job Cost Entry ───────────────────────────────────────────────────────


class JobCostLineCreate(StrictModel):
    job_id: Optional[int] = None
    cost_code_id: Optional[int] = None
    cost_type: Optional[str] = None
    description: Optional[str] = None
    quantity: Decimal = Decimal("1")
    rate: Decimal = Decimal("0")
    amount: Optional[Decimal] = None  # defaults to quantity × rate
    debit_account_id: Optional[int] = None
    credit_account_id: Optional[int] = None
    employee_id: Optional[int] = None
    equipment_id: Optional[int] = None
    is_burden: bool = False
    is_billable: bool = False
    # Accrue the cost (#897): the line's offset is 2010 Accrued Job Costs,
    # created on first use in a chart without it. Sending the 2010 account's
    # id as credit_account_id does the same.
    accrue: bool = False

    @model_validator(mode="after")
    def _amount(self):
        if self.amount is None:
            self.amount = (self.quantity * self.rate).quantize(Decimal("0.01"))
        if self.amount < 0 or self.quantity < 0:
            raise ValueError(
                "Job cost lines cannot be negative — void and re-enter instead"
            )
        return self


class JobCostCreate(StrictModel):
    date: dt_date
    job_id: Optional[int] = None
    memo: Optional[str] = None
    lines: list[JobCostLineCreate]
    # An accrual's reversal date (#897), when a line credits 2010 Accrued Job
    # Costs: left out, the first day of the month after the entry's date;
    # sent as null, no reversal (the accrual stays open until one is set).
    reverses_on: Optional[dt_date] = None

    @field_validator("lines")
    @classmethod
    def _lines(cls, v):
        if not v:
            raise ValueError("At least one line is required")
        return v


class JobCostLineResponse(BaseModel):
    id: int
    job_id: Optional[int] = None
    job_name: Optional[str] = None
    cost_code_id: Optional[int] = None
    cost_code_label: Optional[str] = None
    cost_type: Optional[str] = None
    description: Optional[str] = None
    quantity: Decimal
    rate: Decimal
    amount: Decimal
    debit_account_id: int
    debit_account_name: Optional[str] = None
    credit_account_id: int
    credit_account_name: Optional[str] = None
    employee_id: Optional[int] = None
    equipment_id: Optional[int] = None
    time_entry_id: Optional[int] = None
    is_burden: bool = False
    is_billable: bool = False
    line_order: int = 0

    model_config = {"from_attributes": True}


class JobCostResponse(BaseModel):
    id: int
    number: str
    date: dt_date
    job_id: Optional[int] = None
    job_name: Optional[str] = None
    memo: Optional[str] = None
    source: str
    status: str
    transaction_id: Optional[int] = None
    total: Decimal
    lines: list[JobCostLineResponse] = []
    created_at: Optional[datetime] = None
    # An accrual (#897): what it credited to 2010 Accrued Job Costs, the date
    # it reverses on, and the reversal once posted
    accrued: Decimal = Decimal("0")
    reverses_on: Optional[dt_date] = None
    reversal_transaction_id: Optional[int] = None
    reversed_on: Optional[dt_date] = None

    model_config = {"from_attributes": True}


class JobCostReversesOn(StrictModel):
    """PUT /api/job-costs/{id}/reverses-on: move or clear an accrual's
    reversal date before it reverses (#897)."""

    reverses_on: Optional[dt_date] = None


class AllocationTarget(StrictModel):
    job_id: int
    weight: Decimal = Decimal("1")


class AllocationCreate(StrictModel):
    date: dt_date
    amount: Decimal
    method: str = "equal"
    memo: Optional[str] = None
    cost_code_id: Optional[int] = None
    cost_type: Optional[str] = None
    debit_account_id: Optional[int] = None
    credit_account_id: Optional[int] = None
    start_date: Optional[dt_date] = None
    end_date: Optional[dt_date] = None
    targets: list[AllocationTarget] = []  # empty = every active job
    # as on an entry (#897): the offset is 2010 Accrued Job Costs, and the
    # allocation reverses itself on reverses_on (default the next month's 1st)
    accrue: bool = False
    reverses_on: Optional[dt_date] = None

    @field_validator("method")
    @classmethod
    def _method(cls, v):
        v = (v or "").strip().lower()
        if v not in ALLOCATION_METHODS:
            raise ValueError(f"method must be one of {', '.join(ALLOCATION_METHODS)}")
        return v


# ── Budgets ──────────────────────────────────────────────────────────────


class JobBudgetRow(StrictModel):
    cost_code_id: Optional[int] = None
    cost_type: Optional[str] = None
    amount: Decimal = Decimal("0")
    revenue_amount: Decimal = Decimal("0")
    notes: Optional[str] = None

    @model_validator(mode="after")
    def _one_key(self):
        if self.cost_code_id and self.cost_type:
            raise ValueError("A budget row is per cost code OR per cost type, not both")
        return self


class JobBudgetSave(StrictModel):
    rows: list[JobBudgetRow]


class CostToCompleteEntry(StrictModel):
    """One forecast cell: a section (cost_code_id; None with cost_type "" is the
    Unassigned cell), a cost type, and the amount — None clears the entry."""

    cost_code_id: Optional[int] = None
    cost_type: str = ""
    amount: Optional[Decimal] = Field(default=None, ge=0)


class CostToCompleteSave(StrictModel):
    entries: list[CostToCompleteEntry]


class JobBudgetResponse(BaseModel):
    id: int
    job_id: int
    cost_code_id: Optional[int] = None
    cost_code_label: Optional[str] = None
    cost_type: Optional[str] = None
    amount: Decimal
    revenue_amount: Decimal
    source: str
    estimate_id: Optional[int] = None
    locked: bool = False
    notes: Optional[str] = None

    model_config = {"from_attributes": True}
