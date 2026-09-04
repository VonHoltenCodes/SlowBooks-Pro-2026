from datetime import date
from typing import Optional

from pydantic import BaseModel, field_validator

from app.models.benefits import (
    BENEFIT_CATEGORIES,
    BENEFIT_KINDS,
    BURDEN_ROUTINGS,
    CALC_METHODS,
    EMPLOYER_CALC_METHODS,
)


def _one_of(value, allowed, label):
    if value is not None and value not in allowed:
        raise ValueError(f"{label} must be one of {', '.join(allowed)}")
    return value


# --- Rates -----------------------------------------------------------------
class BenefitRateIn(BaseModel):
    effective_from: Optional[date] = None
    effective_to: Optional[date] = None
    employee_rate: float = 0
    employer_rate: float = 0
    per_period_cap: Optional[float] = None
    annual_cap: Optional[float] = None
    wage_base_ceiling: Optional[float] = None
    employer_annual_cap: Optional[float] = None
    employer_match_limit_pct: Optional[float] = None
    tiers_json: Optional[str] = None


class BenefitRateResponse(BenefitRateIn):
    id: int
    benefit_code_id: int
    effective_from: date
    model_config = {"from_attributes": True}


# --- Codes -----------------------------------------------------------------
class _CodeValidators(BaseModel):
    """Shared enum checks (check_fields=False so the Update model, where
    every field is Optional, gets the same guards)."""

    @field_validator("kind", check_fields=False)
    @classmethod
    def _kind(cls, v):
        return _one_of(v, BENEFIT_KINDS, "kind")

    @field_validator("category", check_fields=False)
    @classmethod
    def _category(cls, v):
        return _one_of(v, BENEFIT_CATEGORIES, "category")

    @field_validator("calc_method", check_fields=False)
    @classmethod
    def _method(cls, v):
        return _one_of(v, CALC_METHODS, "calc_method")

    @field_validator("employer_calc_method", check_fields=False)
    @classmethod
    def _emethod(cls, v):
        return _one_of(v, EMPLOYER_CALC_METHODS, "employer_calc_method")

    @field_validator("burden_routing", check_fields=False)
    @classmethod
    def _routing(cls, v):
        return _one_of(v, BURDEN_ROUTINGS, "burden_routing")


class BenefitCodeBase(_CodeValidators):
    code: str
    name: str
    kind: str = "deduction"
    category: str = "pretax"
    calc_method: str = "fixed_amount"
    employer_calc_method: Optional[str] = None
    reduces_federal: bool = False
    reduces_state: bool = False
    reduces_fica: bool = False
    employer_taxable: bool = False
    sequence: int = 100
    expense_account_id: Optional[int] = None
    liability_account_id: Optional[int] = None
    remittance_vendor_id: Optional[int] = None
    burden_routing: str = "fringe_pool"
    tracks_balance: bool = False
    effective_from: Optional[date] = None
    effective_to: Optional[date] = None
    notes: Optional[str] = None


class BenefitCodeCreate(BenefitCodeBase):
    # The first dated rate row, created with the code
    rate: Optional[BenefitRateIn] = None


class BenefitCodeUpdate(_CodeValidators):
    code: Optional[str] = None
    name: Optional[str] = None
    kind: Optional[str] = None
    category: Optional[str] = None
    calc_method: Optional[str] = None
    employer_calc_method: Optional[str] = None
    reduces_federal: Optional[bool] = None
    reduces_state: Optional[bool] = None
    reduces_fica: Optional[bool] = None
    employer_taxable: Optional[bool] = None
    sequence: Optional[int] = None
    expense_account_id: Optional[int] = None
    liability_account_id: Optional[int] = None
    remittance_vendor_id: Optional[int] = None
    burden_routing: Optional[str] = None
    tracks_balance: Optional[bool] = None
    effective_from: Optional[date] = None
    effective_to: Optional[date] = None
    is_active: Optional[bool] = None
    notes: Optional[str] = None


class BenefitCodeResponse(BenefitCodeBase):
    id: int
    is_active: bool = True
    rates: list[BenefitRateResponse] = []
    # The rate row in force today (None when no dated row covers today)
    current_rate: Optional[BenefitRateResponse] = None
    model_config = {"from_attributes": True}


# --- Groups ----------------------------------------------------------------
class GroupCodeIn(BaseModel):
    benefit_code_id: int
    employee_rate: Optional[float] = None
    employer_rate: Optional[float] = None
    per_period_cap: Optional[float] = None
    annual_cap: Optional[float] = None


class GroupCodeResponse(GroupCodeIn):
    id: int
    code: Optional[str] = None
    name: Optional[str] = None
    model_config = {"from_attributes": True}


class EmployeeGroupCreate(BaseModel):
    name: str
    description: Optional[str] = None
    codes: list[GroupCodeIn] = []


class EmployeeGroupUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None


class EmployeeGroupResponse(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    is_active: bool = True
    codes: list[GroupCodeResponse] = []
    member_count: int = 0
    model_config = {"from_attributes": True}


class GroupMembersIn(BaseModel):
    employee_ids: list[int]


# --- Assignments -----------------------------------------------------------
class EmployeeBenefitCreate(BaseModel):
    employee_id: int
    benefit_code_id: int
    employee_rate: Optional[float] = None
    employer_rate: Optional[float] = None
    per_period_cap: Optional[float] = None
    annual_cap: Optional[float] = None
    balance_remaining: Optional[float] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    notes: Optional[str] = None


class EmployeeBenefitUpdate(BaseModel):
    employee_rate: Optional[float] = None
    employer_rate: Optional[float] = None
    per_period_cap: Optional[float] = None
    annual_cap: Optional[float] = None
    balance_remaining: Optional[float] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    is_active: Optional[bool] = None
    notes: Optional[str] = None


class EmployeeBenefitResponse(BaseModel):
    id: int
    employee_id: int
    benefit_code_id: int
    code: Optional[str] = None
    name: Optional[str] = None
    employee_rate: Optional[float] = None
    employer_rate: Optional[float] = None
    per_period_cap: Optional[float] = None
    annual_cap: Optional[float] = None
    balance_remaining: Optional[float] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    is_active: bool = True
    notes: Optional[str] = None
    model_config = {"from_attributes": True}


# --- Resolved view / YTD / remittance ---------------------------------------
class ResolvedBenefit(BaseModel):
    benefit_code_id: int
    code: str
    name: str
    kind: str
    category: str
    sequence: int
    calc_method: str
    employer_calc_method: Optional[str] = None
    employee_rate: float = 0
    employer_rate: float = 0
    per_period_cap: Optional[float] = None
    annual_cap: Optional[float] = None
    wage_base_ceiling: Optional[float] = None
    source: str  # "assignment" | "group"
    balance_remaining: Optional[float] = None
    ytd_employee: float = 0
    ytd_employer: float = 0


class BenefitYTDResponse(BaseModel):
    employee_id: int
    benefit_code_id: int
    code: Optional[str] = None
    name: Optional[str] = None
    year: int
    employee_amount: float = 0
    employer_amount: float = 0
    model_config = {"from_attributes": True}


class PayStubBenefitResponse(BaseModel):
    id: int
    benefit_code_id: Optional[int] = None
    code: str
    name: str
    kind: str
    category: str
    sequence: int
    calc_method: str
    employee_rate: float = 0
    employer_rate: float = 0
    employee_amount: float = 0
    employer_amount: float = 0
    model_config = {"from_attributes": True}


class RemittanceBillIn(BaseModel):
    vendor_id: int
    start_date: date
    end_date: date
    bill_date: Optional[date] = None
    bill_number: Optional[str] = None
