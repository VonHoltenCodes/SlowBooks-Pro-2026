from datetime import date as dt_date, datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, field_validator, model_validator

from app.models.job_costing import ALLOCATION_METHODS


def _strip_required(label):
    def _v(v):
        v = (v or "").strip()
        if not v:
            raise ValueError(f"{label} is required")
        return v

    return _v


# ── Cost types ───────────────────────────────────────────────────────────


class CostTypeCreate(BaseModel):
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


class CostTypeUpdate(BaseModel):
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


class EquipmentCreate(BaseModel):
    name: str
    code: Optional[str] = None
    hourly_rate: Decimal = Decimal("0")
    cost_code_id: Optional[int] = None
    recovery_account_id: Optional[int] = None
    notes: Optional[str] = None

    _name = field_validator("name")(_strip_required("Equipment name"))


class EquipmentUpdate(BaseModel):
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


class JobCostLineCreate(BaseModel):
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

    @model_validator(mode="after")
    def _amount(self):
        if self.amount is None:
            self.amount = (self.quantity * self.rate).quantize(Decimal("0.01"))
        if self.amount < 0 or self.quantity < 0:
            raise ValueError(
                "Job cost lines cannot be negative — void and re-enter instead"
            )
        return self


class JobCostCreate(BaseModel):
    date: dt_date
    job_id: Optional[int] = None
    memo: Optional[str] = None
    lines: list[JobCostLineCreate]

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

    model_config = {"from_attributes": True}


class AllocationTarget(BaseModel):
    job_id: int
    weight: Decimal = Decimal("1")


class AllocationCreate(BaseModel):
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

    @field_validator("method")
    @classmethod
    def _method(cls, v):
        v = (v or "").strip().lower()
        if v not in ALLOCATION_METHODS:
            raise ValueError(f"method must be one of {', '.join(ALLOCATION_METHODS)}")
        return v


# ── Budgets ──────────────────────────────────────────────────────────────


class JobBudgetRow(BaseModel):
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


class JobBudgetSave(BaseModel):
    rows: list[JobBudgetRow]


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
    notes: Optional[str] = None

    model_config = {"from_attributes": True}
