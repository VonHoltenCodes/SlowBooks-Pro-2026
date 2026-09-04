# ============================================================================
# Benefits engine — a benefit is a CODE with a rule, not a feature.
#
# BenefitCode          the rule: kind (deduction / benefit / both), calc
#                      method, tax treatment (which wage bases the employee
#                      side reduces), explicit SEQUENCE, GL mapping,
#                      remittance vendor, and how the employer side routes
#                      to job costing (fringe pool or labor burden).
# BenefitRate          effective-dated rates and limits for a code. Resolved
#                      against the pay-period END date; rates change mid-year
#                      and history must not move.
# EmployeeGroup        a template: the set of codes applied to everyone in
#                      the group (Sage/Acumatica "employee class"; named
#                      "group" here so it isn't confused with class tracking).
# EmployeeGroupBenefit one code inside a group, with optional rate overrides.
# EmployeeBenefit      the assignment for one employee. Group provides the
#                      default; an assignment wins. Carries per-employee
#                      overrides, caps and an optional running balance
#                      (company loan, HSA-like arbitrary deductions).
# BenefitYTD           first-class year-to-date accumulators per employee per
#                      code per year — never recomputed by summing history
#                      (a rebuild endpoint exists for repair).
# PayStubBenefit       the posted-run snapshot: the resolved rule AND the
#                      amounts, frozen on the stub so a later config change
#                      cannot rewrite what was withheld.
# ============================================================================

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import relationship

from app.database import Base

BENEFIT_KINDS = ("deduction", "benefit", "both")
BENEFIT_CATEGORIES = ("pretax", "posttax")
# Employee-side calculation methods. percent_of_taxable is the one where
# SEQUENCE matters: it takes the running income-tax base after every
# earlier pre-tax code has reduced it.
CALC_METHODS = (
    "fixed_amount",
    "percent_of_gross",
    "percent_of_taxable",
    "amount_per_hour",
    "tiered",
)
# Employer-side methods: any of the above, or a match on the employee's own
# contribution (401(k) match) capped at a percent of gross.
EMPLOYER_CALC_METHODS = CALC_METHODS + ("match_percent",)
BURDEN_ROUTINGS = ("fringe_pool", "job_burden")


class BenefitCode(Base):
    __tablename__ = "benefit_codes"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(30), nullable=False, unique=True)
    name = Column(String(120), nullable=False)
    kind = Column(String(12), nullable=False, default="deduction")
    category = Column(String(10), nullable=False, default="pretax")
    calc_method = Column(String(24), nullable=False, default="fixed_amount")
    # NULL = same method as the employee side
    employer_calc_method = Column(String(24), nullable=True)

    # Tax treatment of the EMPLOYEE side (pre-tax only): which wage bases it
    # reduces. Three flags, not one — a traditional 401(k) reduces income-tax
    # wages but not FICA; a Section 125 premium reduces all three.
    reduces_federal = Column(Boolean, nullable=False, default=False)
    reduces_state = Column(Boolean, nullable=False, default=False)
    reduces_fica = Column(Boolean, nullable=False, default=False)
    # Employer contributions that are taxable income to the employee (group
    # term life over $50k, some fringe) — reported, not withheld from here.
    employer_taxable = Column(Boolean, nullable=False, default=False)

    # Pre-tax codes apply in this order; each changes the taxable base for
    # the next. Never rely on insertion order.
    sequence = Column(Integer, nullable=False, default=100)

    # GL mapping. Employee withholding and employer cost both credit the
    # liability; the employer cost debits the expense.
    expense_account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)
    liability_account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)
    remittance_vendor_id = Column(Integer, ForeignKey("vendors.id"), nullable=True)
    # Where the employer-paid side goes for job costing: stay in the fringe
    # pool (the expense account), or distribute to jobs as labor burden by
    # hours worked in the pay period.
    burden_routing = Column(String(12), nullable=False, default="fringe_pool")

    # Codes with a running balance (loans): the assignment carries the
    # balance and the deduction stops when it reaches zero.
    tracks_balance = Column(Boolean, nullable=False, default=False)

    effective_from = Column(Date, nullable=True)
    effective_to = Column(Date, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    notes = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    expense_account = relationship("Account", foreign_keys=[expense_account_id])
    liability_account = relationship("Account", foreign_keys=[liability_account_id])
    remittance_vendor = relationship("Vendor")
    rates = relationship(
        "BenefitRate",
        back_populates="benefit_code",
        cascade="all, delete-orphan",
        order_by="BenefitRate.effective_from",
    )


class BenefitRate(Base):
    """One dated row of rates and limits. Limits are plural on purpose: a
    per-period cap, an annual cap and a wage-base ceiling are three
    different rules and a code commonly needs all three."""

    __tablename__ = "benefit_rates"

    id = Column(Integer, primary_key=True, index=True)
    benefit_code_id = Column(
        Integer,
        ForeignKey("benefit_codes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    effective_from = Column(Date, nullable=False)
    effective_to = Column(Date, nullable=True)

    # Dollars (fixed_amount, amount_per_hour) or percent (percent_*)
    employee_rate = Column(Numeric(12, 4), nullable=False, default=0)
    employer_rate = Column(Numeric(12, 4), nullable=False, default=0)
    per_period_cap = Column(Numeric(12, 2), nullable=True)
    annual_cap = Column(Numeric(12, 2), nullable=True)
    # Only wages up to this YTD ceiling count toward a percent-method code
    wage_base_ceiling = Column(Numeric(12, 2), nullable=True)
    employer_annual_cap = Column(Numeric(12, 2), nullable=True)
    # match_percent: employer matches employer_rate % of the employee's
    # contribution, on at most this percent of gross
    employer_match_limit_pct = Column(Numeric(6, 2), nullable=True)
    # tiered: JSON list of {"up_to": <amount or pct>, "rate": <pct>} bands
    tiers_json = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    benefit_code = relationship("BenefitCode", back_populates="rates")


class EmployeeGroup(Base):
    __tablename__ = "employee_groups"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(120), nullable=False, unique=True)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    codes = relationship(
        "EmployeeGroupBenefit",
        back_populates="group",
        cascade="all, delete-orphan",
    )


class EmployeeGroupBenefit(Base):
    __tablename__ = "employee_group_benefits"
    __table_args__ = (
        UniqueConstraint("group_id", "benefit_code_id", name="uq_group_benefit"),
    )

    id = Column(Integer, primary_key=True, index=True)
    group_id = Column(
        Integer,
        ForeignKey("employee_groups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    benefit_code_id = Column(Integer, ForeignKey("benefit_codes.id"), nullable=False)
    employee_rate = Column(Numeric(12, 4), nullable=True)
    employer_rate = Column(Numeric(12, 4), nullable=True)
    per_period_cap = Column(Numeric(12, 2), nullable=True)
    annual_cap = Column(Numeric(12, 2), nullable=True)

    group = relationship("EmployeeGroup", back_populates="codes")
    benefit_code = relationship("BenefitCode")


class EmployeeBenefit(Base):
    __tablename__ = "employee_benefits"

    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(
        Integer, ForeignKey("employees.id"), nullable=False, index=True
    )
    benefit_code_id = Column(
        Integer, ForeignKey("benefit_codes.id"), nullable=False, index=True
    )
    # Overrides; NULL = take the group's value, then the code's dated rate
    employee_rate = Column(Numeric(12, 4), nullable=True)
    employer_rate = Column(Numeric(12, 4), nullable=True)
    per_period_cap = Column(Numeric(12, 2), nullable=True)
    annual_cap = Column(Numeric(12, 2), nullable=True)
    # Running balance for balance-tracking codes (loans). NULL = not tracked.
    balance_remaining = Column(Numeric(12, 2), nullable=True)
    start_date = Column(Date, nullable=True)
    end_date = Column(Date, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    notes = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    employee = relationship("Employee")
    benefit_code = relationship("BenefitCode")


class BenefitYTD(Base):
    __tablename__ = "benefit_ytd"
    __table_args__ = (
        UniqueConstraint(
            "employee_id", "benefit_code_id", "year", name="uq_benefit_ytd"
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(
        Integer, ForeignKey("employees.id"), nullable=False, index=True
    )
    benefit_code_id = Column(Integer, ForeignKey("benefit_codes.id"), nullable=False)
    year = Column(Integer, nullable=False)
    employee_amount = Column(Numeric(12, 2), nullable=False, default=0)
    employer_amount = Column(Numeric(12, 2), nullable=False, default=0)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    benefit_code = relationship("BenefitCode")


class PayStubBenefit(Base):
    __tablename__ = "pay_stub_benefits"

    id = Column(Integer, primary_key=True, index=True)
    pay_stub_id = Column(
        Integer,
        ForeignKey("pay_stubs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    benefit_code_id = Column(Integer, ForeignKey("benefit_codes.id"), nullable=True)
    # Snapshot of the rule as resolved for this stub
    code = Column(String(30), nullable=False)
    name = Column(String(120), nullable=False)
    kind = Column(String(12), nullable=False)
    category = Column(String(10), nullable=False)
    sequence = Column(Integer, nullable=False, default=100)
    calc_method = Column(String(24), nullable=False)
    employee_rate = Column(Numeric(12, 4), nullable=False, default=0)
    employer_rate = Column(Numeric(12, 4), nullable=False, default=0)
    reduces_federal = Column(Boolean, nullable=False, default=False)
    reduces_state = Column(Boolean, nullable=False, default=False)
    reduces_fica = Column(Boolean, nullable=False, default=False)
    expense_account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)
    liability_account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)
    remittance_vendor_id = Column(Integer, ForeignKey("vendors.id"), nullable=True)
    burden_routing = Column(String(12), nullable=False, default="fringe_pool")
    rule_json = Column(Text, nullable=True)
    # The amounts
    employee_amount = Column(Numeric(12, 2), nullable=False, default=0)
    employer_amount = Column(Numeric(12, 2), nullable=False, default=0)

    pay_stub = relationship("PayStub", back_populates="benefits")
    benefit_code = relationship("BenefitCode")
