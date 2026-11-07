from datetime import date as dt_date, datetime
from decimal import Decimal
from typing import Annotated, List, Optional

from pydantic import BaseModel, Field, field_validator
from app.schemas.common import StrictModel

from app.models.jobs import (
    CONTRACT_TYPES,
    JOB_STATUSES,
    SALES_TAX_TREATMENTS,
    SOV_KINDS,
)

# Money columns are Numeric(15, 2): anything larger overflows Postgres (a 500).
_MAX_MONEY = Decimal("9999999999999.99")
Money = Annotated[Decimal, Field(ge=0, le=_MAX_MONEY)]
# A scheduled value may be negative (a deduct alternate), but is still bounded.
SignedMoney = Annotated[Decimal, Field(ge=-_MAX_MONEY, le=_MAX_MONEY)]


def _clean_name(v):
    v = (v or "").strip()
    if not v:
        raise ValueError("Job name is required")
    if ":" in v:
        raise ValueError("Job name cannot contain ':' — pick the customer instead")
    return v


def _check_status(v):
    if v is None:
        return v
    v = v.strip().lower().replace(" ", "_")
    if v not in JOB_STATUSES:
        raise ValueError(f"status must be one of {', '.join(JOB_STATUSES)}")
    return v


class JobCreate(StrictModel):
    customer_id: int
    name: str
    job_number: Optional[str] = None
    status: str = "in_progress"
    job_type: Optional[str] = None
    description: Optional[str] = None
    site_address: Optional[str] = None
    start_date: Optional[dt_date] = None
    projected_end_date: Optional[dt_date] = None
    end_date: Optional[dt_date] = None
    contract_amount: Optional[Money] = None
    notes: Optional[str] = None

    _name = field_validator("name")(_clean_name)
    _status = field_validator("status")(_check_status)


class JobUpdate(StrictModel):
    customer_id: Optional[int] = None
    name: Optional[str] = None
    job_number: Optional[str] = None
    status: Optional[str] = None
    job_type: Optional[str] = None
    description: Optional[str] = None
    site_address: Optional[str] = None
    start_date: Optional[dt_date] = None
    projected_end_date: Optional[dt_date] = None
    end_date: Optional[dt_date] = None
    contract_amount: Optional[Money] = None
    notes: Optional[str] = None
    is_active: Optional[bool] = None

    @field_validator("name")
    @classmethod
    def _name(cls, v):
        return None if v is None else _clean_name(v)

    _status = field_validator("status")(_check_status)


class JobResponse(BaseModel):
    id: int
    customer_id: int
    customer_name: str = ""
    name: str
    full_name: str = ""
    job_number: Optional[str] = None
    status: str
    job_type: Optional[str] = None
    description: Optional[str] = None
    site_address: Optional[str] = None
    start_date: Optional[dt_date] = None
    projected_end_date: Optional[dt_date] = None
    end_date: Optional[dt_date] = None
    contract_amount: Optional[Decimal] = None
    notes: Optional[str] = None
    is_active: bool
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class JobSummary(BaseModel):
    """Profitability figures for one job (all-time unless dated)."""

    job_id: Optional[int]
    job_name: str
    customer_id: Optional[int] = None
    customer_name: str = ""
    status: Optional[str] = None
    contract_amount: Optional[float] = None
    income: float = 0.0
    cogs: float = 0.0
    expenses: float = 0.0
    total_costs: float = 0.0
    gross_profit: float = 0.0
    net_income: float = 0.0
    margin_pct: Optional[float] = None
    committed_cost: float = 0.0  # remaining open commitment (not yet billed)
    committed_total: float = 0.0  # Σ of every non-draft commitment (#553)
    billed_to_date: float = 0.0  # Σ billed against commitments (#553)


def _check_contract_type(v):
    if v is None:
        return v
    v = v.strip().lower().replace(" ", "_").replace("-", "_")
    if v not in CONTRACT_TYPES:
        raise ValueError(f"contract_type must be one of {', '.join(CONTRACT_TYPES)}")
    return v


def _bounded_markup(v):
    if v is not None and not (-100 < v <= 1000):
        raise ValueError("co_markup_pct must be above -100 and at most 1000")
    return v


class ContractTerms(StrictModel):
    """The PUT body for a job's contract terms. Every field is optional —
    a field left out is unchanged; a field sent as null clears it."""

    contract_type: Optional[str] = None
    contract_amount: Optional[Money] = None
    fee_pct: Optional[Decimal] = None
    fee_amount: Optional[Money] = None
    cap_amount: Optional[Money] = None
    contingency_amount: Optional[Money] = None
    retainage_pct: Optional[Decimal] = None
    co_markup_pct: Optional[Decimal] = None
    contract_date: Optional[dt_date] = None
    # "charge_owner" (null) or "contractor_pays" (models.jobs)
    sales_tax_treatment: Optional[str] = None

    _type = field_validator("contract_type")(_check_contract_type)
    _comarkup = field_validator("co_markup_pct")(_bounded_markup)

    @field_validator("sales_tax_treatment")
    @classmethod
    def _tax_treatment(cls, v):
        if v is not None and v not in SALES_TAX_TREATMENTS:
            raise ValueError(
                "sales_tax_treatment must be charge_owner (sales tax on the "
                "owner's pay applications) or contractor_pays (the contractor "
                "pays tax on materials)"
            )
        return v

    @field_validator("fee_pct")
    @classmethod
    def _fee_pct(cls, v):
        if v is not None and not (0 <= v <= 100):
            raise ValueError("fee_pct must be between 0 and 100")
        return v

    @field_validator("retainage_pct")
    @classmethod
    def _retainage(cls, v):
        if v is not None and not (0 <= v <= 100):
            raise ValueError("retainage_pct must be between 0 and 100")
        return v


class SovLineIn(StrictModel):
    # The existing line this row is (so a billed line round-trips by id, #555);
    # absent on a freshly added row.
    id: Optional[int] = None
    item_no: Optional[str] = Field(default=None, max_length=10)
    description: Optional[str] = Field(default=None, max_length=200)
    trade_package_id: Optional[int] = None
    cost_code_id: Optional[int] = None
    # The income account the line's billing credits (null: 4050 Contract
    # Revenue) and whether its billing is taxed. Left out on a line that
    # round-trips by id, each keeps what the line had; a new line is taxed.
    revenue_account_id: Optional[int] = None
    is_taxable: Optional[bool] = None
    kind: str = "work"
    # A scheduled value may be negative — a deduct alternate carried onto the
    # schedule reduces the contract sum (#551 allows a deduct alternate).
    scheduled_value: SignedMoney = Decimal("0")

    @field_validator("kind")
    @classmethod
    def _kind(cls, v):
        v = (v or "work").strip().lower()
        if v not in SOV_KINDS:
            raise ValueError(f"kind must be one of {', '.join(SOV_KINDS)}")
        return v


class SovSave(StrictModel):
    lines: List[SovLineIn] = []


class SovLineOut(BaseModel):
    id: int
    line_no: int
    item_no: Optional[str] = None
    description: Optional[str] = None
    trade_package_id: Optional[int] = None
    trade_package_name: Optional[str] = None
    cost_code_id: Optional[int] = None
    cost_code_label: Optional[str] = None
    revenue_account_id: Optional[int] = None
    revenue_account_name: Optional[str] = None
    is_taxable: bool = True
    kind: str
    scheduled_value: Decimal
    source_estimate_id: Optional[int] = None

    model_config = {"from_attributes": True}


class ContractSummary(BaseModel):
    """The contract roll-up for one job — figures the Contract tab's tiles
    read. Additive; a job with no contract type still answers (the money
    fields fall back to the plain contract_amount)."""

    contract_type: Optional[str] = None
    contract_amount: Optional[float] = None
    original_contract_amount: Optional[float] = None
    changes: float = 0.0
    fee: float = 0.0
    fee_pct: Optional[float] = None
    fee_amount: Optional[float] = None
    cost_to_date: float = 0.0
    cap_amount: Optional[float] = None
    cap_remaining: Optional[float] = None
    contingency_amount: Optional[float] = None
    contingency_used: float = 0.0
    contingency_remaining: Optional[float] = None
    retainage_pct: Optional[float] = None
    co_markup_pct: Optional[float] = None
    contract_date: Optional[dt_date] = None
    sales_tax_treatment: str = "charge_owner"
    contract_estimate_id: Optional[int] = None
    sov_total: float = 0.0
    sov_balance: float = 0.0
    billed_to_date: float = 0.0
    billed_pct: Optional[float] = None
    # Change orders (#554): allowance draw-down per line and the CO roll-up.
    allowances: List[dict] = []
    change_orders: dict = {}
    # Progress billing (#555): owner deposits and owner-side retainage.
    deposits: dict = {}
    retainage: dict = {}


class ContractResponse(BaseModel):
    contract: ContractSummary
    sov: List[SovLineOut] = []
    # Set by the terms save only: a sentence for a saved term that will quietly
    # do nothing (the contractor pays the tax with no use tax rate set).
    warnings: List[str] = []


class JobDetailResponse(JobResponse):
    summary: JobSummary
    contract: Optional[ContractSummary] = None
