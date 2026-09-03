from datetime import date as dt_date, datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, field_validator

from app.models.jobs import JOB_STATUSES


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


class JobCreate(BaseModel):
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
    contract_amount: Optional[Decimal] = None
    notes: Optional[str] = None

    _name = field_validator("name")(_clean_name)
    _status = field_validator("status")(_check_status)


class JobUpdate(BaseModel):
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
    contract_amount: Optional[Decimal] = None
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
    committed_cost: float = 0.0


class JobDetailResponse(JobResponse):
    summary: JobSummary
