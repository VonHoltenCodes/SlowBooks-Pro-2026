from datetime import date
from typing import Optional
from pydantic import BaseModel, model_validator


# ---------------------------------------------------------------------------
# Employees — 2020+ Form W-4 (no "allowances"), per-employee pay frequency
# ---------------------------------------------------------------------------
class EmployeeCreate(BaseModel):
    first_name: str
    last_name: str
    ssn_last_four: Optional[str] = None
    pay_type: str = "hourly"
    pay_rate: float = 0
    pay_frequency: str = "biweekly"
    filing_status: str = "single"
    # 2020+ Form W-4
    multiple_jobs: bool = False
    dependents_amount: float = 0
    other_income_annual: float = 0
    deductions_annual: float = 0
    extra_withholding: float = 0
    address1: Optional[str] = None
    address2: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip: Optional[str] = None
    work_state: Optional[str] = None
    residence_state: Optional[str] = None
    wc_class_code: Optional[str] = None
    email: Optional[str] = None
    role: str = "employee"
    manager_id: Optional[int] = None
    hire_date: Optional[date] = None
    notes: Optional[str] = None


class EmployeeUpdate(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    ssn_last_four: Optional[str] = None
    pay_type: Optional[str] = None
    pay_rate: Optional[float] = None
    pay_frequency: Optional[str] = None
    filing_status: Optional[str] = None
    multiple_jobs: Optional[bool] = None
    dependents_amount: Optional[float] = None
    other_income_annual: Optional[float] = None
    deductions_annual: Optional[float] = None
    extra_withholding: Optional[float] = None
    address1: Optional[str] = None
    address2: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip: Optional[str] = None
    work_state: Optional[str] = None
    residence_state: Optional[str] = None
    wc_class_code: Optional[str] = None
    email: Optional[str] = None
    role: Optional[str] = None
    manager_id: Optional[int] = None
    hire_date: Optional[date] = None
    is_active: Optional[bool] = None
    notes: Optional[str] = None


class EmployeeResponse(BaseModel):
    id: int
    first_name: str
    last_name: str
    ssn_last_four: Optional[str] = None
    pay_type: str
    pay_rate: float = 0
    pay_frequency: str = "biweekly"
    filing_status: str
    multiple_jobs: bool = False
    dependents_amount: float = 0
    other_income_annual: float = 0
    deductions_annual: float = 0
    extra_withholding: float = 0
    address1: Optional[str] = None
    address2: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip: Optional[str] = None
    work_state: Optional[str] = None
    residence_state: Optional[str] = None
    wc_class_code: Optional[str] = None
    email: Optional[str] = None
    role: str = "employee"
    manager_id: Optional[int] = None
    is_active: bool = True
    hire_date: Optional[date] = None
    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Pay runs / pay stubs
# ---------------------------------------------------------------------------
class PayStubInput(BaseModel):
    employee_id: int
    hours: float = 0  # total hours (hourly employees)
    regular_hours: Optional[float] = None
    overtime_hours: float = 0
    doubletime_hours: float = 0
    gross_override: Optional[float] = None  # explicit gross (bonuses / off-cycle runs)
    pretax_deductions: float = (
        0  # ad-hoc; configured EmployeeDeductions are auto-applied
    )
    posttax_deductions: float = 0
    reimbursements: float = 0  # non-taxable accountable-plan reimbursements
    supplemental: bool = False  # treat as supplemental wages (bonus/off-cycle)
    supplemental_method: str = "flat"  # "flat" 22% or "aggregate"
    work_state: Optional[str] = None  # per-stub work location (multi-state)
    use_time_entries: bool = (
        False  # pull approved time entries for the period instead of `hours`
    )

    @model_validator(mode="after")
    def _check_non_negative(self):
        """Negative hours / deductions / overrides silently produce a $0
        paystub today (the tax engines clamp at zero, and the response just
        says total_gross=0). That's confusing UX and masks data-entry
        mistakes; reject at the boundary instead."""
        nonneg_fields = [
            ("hours", self.hours),
            ("overtime_hours", self.overtime_hours),
            ("doubletime_hours", self.doubletime_hours),
            ("pretax_deductions", self.pretax_deductions),
            ("posttax_deductions", self.posttax_deductions),
            ("reimbursements", self.reimbursements),
        ]
        if self.regular_hours is not None:
            nonneg_fields.append(("regular_hours", self.regular_hours))
        if self.gross_override is not None:
            nonneg_fields.append(("gross_override", self.gross_override))
        for name, val in nonneg_fields:
            if val is not None and val < 0:
                raise ValueError(f"{name} must be non-negative (got {val})")
        return self


class PayRunCreate(BaseModel):
    period_start: date
    period_end: date
    pay_date: date
    run_type: str = "regular"
    stubs: list[PayStubInput] = []


class PayStubResponse(BaseModel):
    id: int
    employee_id: int
    employee_name: Optional[str] = None
    hours: float = 0
    regular_hours: float = 0
    overtime_hours: float = 0
    doubletime_hours: float = 0
    gross_pay: float = 0
    federal_tax: float = 0
    state_tax: float = 0
    state_other_employee: float = 0
    ss_tax: float = 0
    medicare_tax: float = 0
    pretax_deductions: float = 0
    posttax_deductions: float = 0
    garnishments: float = 0
    reimbursements: float = 0
    net_pay: float = 0
    work_state: Optional[str] = None
    employer_ss_tax: float = 0
    employer_medicare_tax: float = 0
    futa_tax: float = 0
    suta_tax: float = 0
    state_other_employer: float = 0
    model_config = {"from_attributes": True}


class PayRunResponse(BaseModel):
    id: int
    period_start: date
    period_end: date
    pay_date: date
    status: str
    run_type: str = "regular"
    total_gross: float = 0
    total_net: float = 0
    total_taxes: float = 0
    total_employer_taxes: float = 0
    stubs: list[PayStubResponse] = []
    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Year-to-date / employee bank accounts
# ---------------------------------------------------------------------------
class YTDResponse(BaseModel):
    employee_id: int
    year: int
    gross: float = 0
    federal: float = 0
    state: float = 0
    state_other: float = 0
    ss: float = 0
    medicare: float = 0
    pretax_deductions: float = 0
    net: float = 0


class BankAccountCreate(BaseModel):
    nickname: Optional[str] = None
    account_kind: str = "checking"
    routing_number: str
    account_number: str
    deposit_type: str = "full"
    deposit_value: float = 0
    priority: int = 0


class BankAccountResponse(BaseModel):
    id: int
    employee_id: int
    nickname: Optional[str] = None
    account_kind: str
    account_last_four: Optional[str] = None
    deposit_type: str
    deposit_value: float = 0
    priority: int = 0
    prenote_status: str
    is_active: bool = True
    model_config = {"from_attributes": True}
