# ============================================================================
# Payroll — employee records, pay runs, withholding calculations
# Feature 17 / Tier 1: modern (2020+) W-4, per-employee pay frequency,
# employee + employer side tax capture on each stub.
# ============================================================================

import enum

from sqlalchemy import (
    Column,
    Integer,
    String,
    Date,
    Numeric,
    DateTime,
    Text,
    Enum,
    Boolean,
    ForeignKey,
    func,
)
from sqlalchemy.orm import relationship

from app.database import Base


class PayType(str, enum.Enum):
    SALARY = "salary"
    HOURLY = "hourly"


class FilingStatus(str, enum.Enum):
    # Maps to the three checkboxes on the 2020+ Form W-4, Step 1(c).
    SINGLE = "single"  # Single or Married filing separately
    MARRIED = "married"  # Married filing jointly
    HEAD_OF_HOUSEHOLD = "head_of_household"


class PayFrequency(str, enum.Enum):
    WEEKLY = "weekly"
    BIWEEKLY = "biweekly"
    SEMI_MONTHLY = "semi_monthly"
    MONTHLY = "monthly"


# Number of pay periods in a year for each frequency. Drives both the salary
# divisor and the annualization factor in the withholding math.
PERIODS_PER_YEAR = {
    PayFrequency.WEEKLY: 52,
    PayFrequency.BIWEEKLY: 26,
    PayFrequency.SEMI_MONTHLY: 24,
    PayFrequency.MONTHLY: 12,
}


def periods_per_year(freq) -> int:
    """Resolve a PayFrequency (or its string value) to pay periods per year."""
    if isinstance(freq, PayFrequency):
        return PERIODS_PER_YEAR[freq]
    try:
        return PERIODS_PER_YEAR[PayFrequency(freq)]
    except (ValueError, KeyError):
        return 26


class PayRunStatus(str, enum.Enum):
    DRAFT = "draft"
    PROCESSED = "processed"
    VOID = "void"


class PayRunType(str, enum.Enum):
    REGULAR = "regular"
    OFF_CYCLE = "off_cycle"
    BONUS = "bonus"


class EmployeeRole(str, enum.Enum):
    ADMIN = "admin"
    MANAGER = "manager"
    EMPLOYEE = "employee"


class Employee(Base):
    __tablename__ = "employees"

    id = Column(Integer, primary_key=True, index=True)
    first_name = Column(String(100), nullable=False)
    last_name = Column(String(100), nullable=False)
    ssn_last_four = Column(String(4), nullable=True)
    pay_type = Column(Enum(PayType), default=PayType.HOURLY)
    pay_rate = Column(Numeric(12, 2), default=0)  # hourly rate or annual salary
    pay_frequency = Column(Enum(PayFrequency), default=PayFrequency.BIWEEKLY)
    filing_status = Column(Enum(FilingStatus), default=FilingStatus.SINGLE)

    # --- 2020+ Form W-4 (the redesign removed "allowances" entirely) ---
    multiple_jobs = Column(Boolean, default=False)  # Step 2(c) checkbox
    dependents_amount = Column(
        Numeric(12, 2), default=0
    )  # Step 3 ($2000/child + $500/other)
    other_income_annual = Column(Numeric(12, 2), default=0)  # Step 4(a)
    deductions_annual = Column(Numeric(12, 2), default=0)  # Step 4(b)
    extra_withholding = Column(Numeric(12, 2), default=0)  # Step 4(c) per pay period

    address1 = Column(String(200), nullable=True)
    address2 = Column(String(200), nullable=True)
    city = Column(String(100), nullable=True)
    state = Column(String(50), nullable=True)
    zip = Column(String(20), nullable=True)

    # Tax situs — which state's withholding engine applies. Defaults to the
    # mailing-address state when unset (see schema/route logic).
    work_state = Column(String(2), nullable=True)
    # State of residence — drives reciprocity (withhold for the residence
    # state instead of the work state when an agreement exists).
    residence_state = Column(String(2), nullable=True)
    # Workers' comp / WA L&I risk classification code.
    wc_class_code = Column(String(20), nullable=True)

    hire_date = Column(Date, nullable=True)
    is_active = Column(Boolean, default=True)
    notes = Column(Text, nullable=True)

    # HR / self-service (Tier 3)
    email = Column(String(200), nullable=True)
    role = Column(Enum(EmployeeRole), default=EmployeeRole.EMPLOYEE)
    manager_id = Column(Integer, ForeignKey("employees.id"), nullable=True)
    # Secret used for token-based access to the self-service portal — the same
    # pattern as the public invoice-payment page. The companion timestamps
    # cap the blast radius if a URL leaks: tokens expire after 90 days of
    # inactivity (last_used rolls forward on every authenticated request) or
    # 1 year hard. Rotating the token via POST /api/employees/{id}/portal-token
    # bumps both columns to 'now' + the windows.
    portal_token = Column(String(64), nullable=True, unique=True)
    portal_token_last_used = Column(DateTime(timezone=True), nullable=True)
    portal_token_expires_at = Column(DateTime(timezone=True), nullable=True)
    # E-Verify case tracking. The actual federal E-Verify system is a
    # separate vendor-gated enrollment, so this is a record-keeping shim:
    # an operator who submits a case via the official portal (or via a
    # third-party service like Equifax) records the case number + status
    # here so it lives next to the employee for audit and DHS inspection.
    everify_case_number = Column(String(30), nullable=True)
    # Status mirrors the official E-Verify lifecycle:
    #   not_submitted, pending, photo_match_required, tnc (tentative
    #   non-confirmation), employment_authorized, final_non_confirmation,
    #   case_closed
    everify_status = Column(String(30), nullable=True)
    everify_submitted_at = Column(DateTime(timezone=True), nullable=True)
    everify_closed_at = Column(DateTime(timezone=True), nullable=True)
    everify_notes = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    pay_stubs = relationship("PayStub", back_populates="employee")
    bank_accounts = relationship(
        "EmployeeBankAccount",
        back_populates="employee",
        cascade="all, delete-orphan",
    )

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"


class PayRun(Base):
    __tablename__ = "pay_runs"

    id = Column(Integer, primary_key=True, index=True)
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)
    pay_date = Column(Date, nullable=False)
    status = Column(Enum(PayRunStatus), default=PayRunStatus.DRAFT)
    run_type = Column(Enum(PayRunType), default=PayRunType.REGULAR)

    total_gross = Column(Numeric(12, 2), default=0)
    total_net = Column(Numeric(12, 2), default=0)
    total_taxes = Column(Numeric(12, 2), default=0)
    total_employer_taxes = Column(Numeric(12, 2), default=0)
    transaction_id = Column(Integer, ForeignKey("transactions.id"), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    transaction = relationship("Transaction", foreign_keys=[transaction_id])
    stubs = relationship(
        "PayStub", back_populates="pay_run", cascade="all, delete-orphan"
    )


class PayStub(Base):
    __tablename__ = "pay_stubs"

    id = Column(Integer, primary_key=True, index=True)
    pay_run_id = Column(
        Integer, ForeignKey("pay_runs.id", ondelete="CASCADE"), nullable=False
    )
    employee_id = Column(
        Integer, ForeignKey("employees.id"), nullable=False, index=True
    )

    # Hours — total kept for backwards compatibility, plus the itemized split
    # that overtime law and pay-stub disclosure rules require.
    hours = Column(Numeric(10, 2), default=0)
    regular_hours = Column(Numeric(10, 2), default=0)
    overtime_hours = Column(Numeric(10, 2), default=0)
    doubletime_hours = Column(Numeric(10, 2), default=0)

    gross_pay = Column(Numeric(12, 2), default=0)

    # Employee-side withholding
    federal_tax = Column(Numeric(12, 2), default=0)
    state_tax = Column(Numeric(12, 2), default=0)  # state income tax
    state_other_employee = Column(
        Numeric(12, 2), default=0
    )  # WA PFML/Cares, SDI, PFL...
    ss_tax = Column(Numeric(12, 2), default=0)  # Social Security 6.2% (employee)
    medicare_tax = Column(Numeric(12, 2), default=0)  # Medicare 1.45% + 0.9% addl
    pretax_deductions = Column(Numeric(12, 2), default=0)
    posttax_deductions = Column(Numeric(12, 2), default=0)
    garnishments = Column(Numeric(12, 2), default=0)
    # Non-taxable accountable-plan reimbursements — added to the check but not
    # part of gross wages and not taxed.
    reimbursements = Column(Numeric(12, 2), default=0)
    net_pay = Column(Numeric(12, 2), default=0)

    # Work-location state for this stub (multi-state employees) — drives SUTA
    # situs and state withholding independently of the employee's home state.
    work_state = Column(String(2), nullable=True)

    # Employer-side taxes (not withheld from the employee — company expense)
    employer_ss_tax = Column(Numeric(12, 2), default=0)
    employer_medicare_tax = Column(Numeric(12, 2), default=0)
    futa_tax = Column(Numeric(12, 2), default=0)
    suta_tax = Column(Numeric(12, 2), default=0)
    state_other_employer = Column(Numeric(12, 2), default=0)  # WA PFML employer, L&I...

    # JSON blob with the fully itemized line-by-line breakdown, used to render
    # pay stubs and tax forms without re-running the calculator.
    detail_json = Column(Text, nullable=True)

    pay_run = relationship("PayRun", back_populates="stubs")
    employee = relationship("Employee", back_populates="pay_stubs")
