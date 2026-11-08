from datetime import date as dt_date, datetime
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, field_validator

from app.schemas.common import StrictModel
from app.schemas.bills import _check_lien_waiver


class PayApplicationCreate(StrictModel):
    """A new owner pay application. Its lines are pre-filled from the job's
    schedule of values, so only the header is sent."""

    job_id: int
    date: Optional[dt_date] = None
    period_to: Optional[dt_date] = None
    retainage_pct: Optional[Decimal] = None
    notes: Optional[str] = None

    @field_validator("retainage_pct")
    @classmethod
    def _ret(cls, v):
        if v is not None and not (0 <= v <= 100):
            raise ValueError("Retainage must be between 0 and 100%.")
        return v


class PayApplicationLineIn(StrictModel):
    # this_period / stored may be negative on a deduct SOV line (a negatively
    # scheduled line); the route's direction-aware guard checks the sign
    # against the line's scheduled value (#555).
    sov_line_id: int
    this_period: Decimal = Decimal("0")
    stored: Decimal = Decimal("0")


class PayApplicationUpdate(StrictModel):
    """Edit a draft/submitted application: this period and stored per line,
    plus the header figures."""

    date: Optional[dt_date] = None
    period_to: Optional[dt_date] = None
    retainage_pct: Optional[Decimal] = None
    tax_rate: Optional[Decimal] = None
    charge_tax: Optional[bool] = None
    release_retainage: Optional[Decimal] = None
    deposit_applied: Optional[Decimal] = None
    lien_waiver_status: Optional[str] = None
    notes: Optional[str] = None
    lines: Optional[List[PayApplicationLineIn]] = None

    _lien = field_validator("lien_waiver_status")(_check_lien_waiver)

    @field_validator("retainage_pct")
    @classmethod
    def _ret(cls, v):
        if v is not None and not (0 <= v <= 100):
            raise ValueError("Retainage must be between 0 and 100%.")
        return v

    @field_validator("tax_rate", "release_retainage", "deposit_applied")
    @classmethod
    def _non_negative(cls, v):
        if v is not None and v < 0:
            raise ValueError("amounts cannot be negative")
        return v


class PayApplicationLineOut(BaseModel):
    id: int
    sov_line_id: Optional[int] = None
    line_no: int
    item_no: Optional[str] = None
    description: Optional[str] = None
    scheduled_value: Decimal
    previous: Decimal = Decimal("0")
    this_period: Decimal = Decimal("0")
    stored: Decimal = Decimal("0")
    completed_to_date: Decimal = Decimal("0")
    pct_complete: float = 0.0
    balance_to_finish: Decimal = Decimal("0")
    retainage: Decimal = Decimal("0")
    # For the form's live tax: whether the SOV line is taxed, and the stored
    # materials the earlier applications left on it.
    is_taxable: bool = True
    prior_stored: Decimal = Decimal("0")

    model_config = {"from_attributes": True}


class PayApplicationOut(BaseModel):
    id: int
    job_id: int
    job_number: Optional[str] = None
    application_no: int
    number: str
    period_to: Optional[dt_date] = None
    date: Optional[dt_date] = None
    status: str
    retainage_pct: Optional[Decimal] = None
    tax_rate: Decimal = Decimal("0")
    charge_tax: bool = True
    # The owner is tax-exempt, so no tax however the toggle stands.
    tax_exempt: bool = False
    # The job's contractor pays tax on materials: no sales tax is billed.
    contractor_pays_tax: bool = False
    release_retainage: Decimal = Decimal("0")
    deposit_applied: Decimal = Decimal("0")
    lien_waiver_status: Optional[str] = None
    invoice_id: Optional[int] = None
    invoice_number: Optional[str] = None
    notes: Optional[str] = None
    lines: List[PayApplicationLineOut] = []
    totals: dict = {}
    # Warn-only (#555): the job's bills dated on/before the period whose
    # lien-waiver status is missing or pending, so the GC sees what to collect.
    sub_waivers: dict = {}
    unapplied_deposits: float = 0.0
    retainage_outstanding: float = 0.0
    # Void would be accepted now: the latest approved application, its
    # invoice unpaid. The Pay apps tab offers Void only then.
    can_void: bool = False

    model_config = {"from_attributes": True}


# ── Owner deposits ──────────────────────────────────────────────────────────


class JobDepositCreate(StrictModel):
    date: dt_date
    amount: Decimal
    deposit_to_account_id: int
    memo: Optional[str] = None

    @field_validator("amount")
    @classmethod
    def _positive(cls, v):
        if v is None or v <= 0:
            raise ValueError("A deposit must be for more than zero")
        return v


class JobDepositOut(BaseModel):
    id: int
    job_id: int
    date: dt_date
    amount: Decimal
    deposit_to_account_id: Optional[int] = None
    memo: Optional[str] = None
    voided_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class JobDepositsResponse(BaseModel):
    deposits: List[JobDepositOut] = []
    received: float = 0.0
    applied: float = 0.0
    unapplied: float = 0.0


# ── Sub retainage release ────────────────────────────────────────────────────


class ReleaseRetainageRequest(StrictModel):
    amount: Optional[Decimal] = None  # default: the whole outstanding
    date: Optional[dt_date] = None

    @field_validator("amount")
    @classmethod
    def _positive(cls, v):
        if v is not None and v <= 0:
            raise ValueError("A release must be for more than zero")
        return v
