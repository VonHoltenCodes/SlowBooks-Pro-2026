# ============================================================================
# Opening YTD balances — what an employee was paid this year before the
# first SlowBooks pay run (#433). A company that switches mid-year enters
# the old system's year-to-date figures once per employee per year.
#
# Payroll-only, like a stub with no run: no journal entry (the old system
# booked that payroll, and the opening balance sheet carries what it still
# owes) and no PayRun, so the readers that move money (deposits, benefit
# remittances, the pay-run list, NACHA, the payroll journal) never see it.
# The readers that need it read it on purpose: employee_ytd and the wage
# bases, the 941, 940, W-2/W-3 and the benefit accumulators.
# ============================================================================

from sqlalchemy import (
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import relationship

from app.database import Base

# The money columns, in the order the employee page and the template show
# them.
OPENING_AMOUNT_FIELDS = (
    "gross",
    "federal_tax",
    "ss_wages",
    "ss_tax",
    "employer_ss_tax",
    "medicare_wages",
    "medicare_tax",
    "employer_medicare_tax",
    "futa_wages",
    "futa_tax",
    "suta_wages",
    "suta_tax",
    "pretax_deductions",
    "posttax_deductions",
    "garnishments",
    "net",
)


class PayrollOpeningBalance(Base):
    __tablename__ = "payroll_opening_balances"
    __table_args__ = (
        UniqueConstraint("employee_id", "year", name="uq_payroll_opening_emp_year"),
    )

    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(
        Integer, ForeignKey("employees.id"), nullable=False, index=True
    )
    year = Column(Integer, nullable=False)
    # The day before the first SlowBooks pay run: the 941 quarter it falls
    # in is the one that reports it.
    as_of = Column(Date, nullable=False)

    gross = Column(Numeric(15, 2), nullable=False, default=0)
    federal_tax = Column(Numeric(15, 2), nullable=False, default=0)
    ss_wages = Column(Numeric(15, 2), nullable=False, default=0)
    ss_tax = Column(Numeric(15, 2), nullable=False, default=0)
    employer_ss_tax = Column(Numeric(15, 2), nullable=False, default=0)
    medicare_wages = Column(Numeric(15, 2), nullable=False, default=0)
    medicare_tax = Column(Numeric(15, 2), nullable=False, default=0)
    employer_medicare_tax = Column(Numeric(15, 2), nullable=False, default=0)
    futa_wages = Column(Numeric(15, 2), nullable=False, default=0)
    futa_tax = Column(Numeric(15, 2), nullable=False, default=0)
    suta_wages = Column(Numeric(15, 2), nullable=False, default=0)
    suta_tax = Column(Numeric(15, 2), nullable=False, default=0)
    pretax_deductions = Column(Numeric(15, 2), nullable=False, default=0)
    posttax_deductions = Column(Numeric(15, 2), nullable=False, default=0)
    garnishments = Column(Numeric(15, 2), nullable=False, default=0)
    net = Column(Numeric(15, 2), nullable=False, default=0)
    note = Column(String(300), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    employee = relationship("Employee")
    # A stub has one work state; an opening balance may carry several.
    states = relationship(
        "PayrollOpeningStateBalance",
        back_populates="opening",
        cascade="all, delete-orphan",
        order_by="PayrollOpeningStateBalance.state",
    )
    benefits = relationship(
        "PayrollOpeningBenefit",
        back_populates="opening",
        cascade="all, delete-orphan",
        order_by="PayrollOpeningBenefit.id",
    )


class PayrollOpeningStateBalance(Base):
    __tablename__ = "payroll_opening_state_balances"
    __table_args__ = (
        UniqueConstraint("opening_id", "state", name="uq_payroll_opening_state"),
    )

    id = Column(Integer, primary_key=True, index=True)
    opening_id = Column(
        Integer,
        ForeignKey("payroll_opening_balances.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    state = Column(String(2), nullable=False)
    wages = Column(Numeric(15, 2), nullable=False, default=0)
    withholding = Column(Numeric(15, 2), nullable=False, default=0)
    other_employee = Column(Numeric(15, 2), nullable=False, default=0)
    other_employer = Column(Numeric(15, 2), nullable=False, default=0)

    opening = relationship("PayrollOpeningBalance", back_populates="states")


class PayrollOpeningBenefit(Base):
    __tablename__ = "payroll_opening_benefits"
    __table_args__ = (
        UniqueConstraint(
            "opening_id", "benefit_code_id", name="uq_payroll_opening_benefit"
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    opening_id = Column(
        Integer,
        ForeignKey("payroll_opening_balances.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    benefit_code_id = Column(Integer, ForeignKey("benefit_codes.id"), nullable=False)
    employee_amount = Column(Numeric(15, 2), nullable=False, default=0)
    employer_amount = Column(Numeric(15, 2), nullable=False, default=0)

    opening = relationship("PayrollOpeningBalance", back_populates="benefits")
    benefit_code = relationship("BenefitCode")
