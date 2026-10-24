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
# bases, the 941, 940, W-2/W-3, the state SUI report, the tax liability
# schedule (the old system's share of the switch quarter, named as such)
# and the benefit accumulators.
#
# The amounts are entered by quarter, the way QuickBooks takes year-to-date
# adjustments: the old system filed the 941 for every quarter it finished,
# so each quarter's 941 here carries only that quarter's figures. State
# wages, withholding and SUTA are per state per quarter; the SUTA wage base
# is a state's own.
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

# The federal money columns of a quarter, in the order the employee page
# and the template show them.
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
    "pretax_deductions",
    "posttax_deductions",
    "garnishments",
    "net",
)
# A state's money columns, per quarter.
OPENING_STATE_FIELDS = (
    "wages",
    "withholding",
    "suta_wages",
    "suta_tax",
    "other_employee",
    "other_employer",
)


def _money():
    return Column(Numeric(15, 2), nullable=False, default=0)


class PayrollOpeningBalance(Base):
    """One employee's opening balance for a year: the date it runs to and
    the quarters under it."""

    __tablename__ = "payroll_opening_balances"
    __table_args__ = (
        UniqueConstraint("employee_id", "year", name="uq_payroll_opening_emp_year"),
    )

    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(
        Integer, ForeignKey("employees.id"), nullable=False, index=True
    )
    year = Column(Integer, nullable=False)
    # The day before the first SlowBooks pay run. Its quarter is the last
    # one the old system paid in; no quarter after it can carry amounts.
    as_of = Column(Date, nullable=False)
    note = Column(String(300), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    employee = relationship("Employee")
    quarters = relationship(
        "PayrollOpeningQuarter",
        back_populates="opening",
        cascade="all, delete-orphan",
        order_by="PayrollOpeningQuarter.quarter",
    )
    # A stub has one work state; a quarter before the switch may have several.
    states = relationship(
        "PayrollOpeningStateBalance",
        back_populates="opening",
        cascade="all, delete-orphan",
        order_by=(
            "PayrollOpeningStateBalance.quarter, PayrollOpeningStateBalance.state"
        ),
    )
    benefits = relationship(
        "PayrollOpeningBenefit",
        back_populates="opening",
        cascade="all, delete-orphan",
        order_by="PayrollOpeningBenefit.id",
    )


class PayrollOpeningQuarter(Base):
    """What the old system paid the employee in one calendar quarter."""

    __tablename__ = "payroll_opening_quarters"
    __table_args__ = (
        UniqueConstraint("opening_id", "quarter", name="uq_payroll_opening_quarter"),
    )

    id = Column(Integer, primary_key=True, index=True)
    opening_id = Column(
        Integer,
        ForeignKey("payroll_opening_balances.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    quarter = Column(Integer, nullable=False)

    gross = _money()
    federal_tax = _money()
    ss_wages = _money()
    ss_tax = _money()
    employer_ss_tax = _money()
    medicare_wages = _money()
    medicare_tax = _money()
    employer_medicare_tax = _money()
    futa_wages = _money()
    futa_tax = _money()
    pretax_deductions = _money()
    posttax_deductions = _money()
    garnishments = _money()
    net = _money()

    opening = relationship("PayrollOpeningBalance", back_populates="quarters")


class PayrollOpeningStateBalance(Base):
    """One state's wages, withholding and unemployment for one quarter."""

    __tablename__ = "payroll_opening_state_balances"
    __table_args__ = (
        UniqueConstraint(
            "opening_id", "quarter", "state", name="uq_payroll_opening_state"
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    opening_id = Column(
        Integer,
        ForeignKey("payroll_opening_balances.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    quarter = Column(Integer, nullable=False)
    state = Column(String(2), nullable=False)
    wages = _money()
    withholding = _money()
    suta_wages = _money()
    suta_tax = _money()
    other_employee = _money()
    other_employer = _money()

    opening = relationship("PayrollOpeningBalance", back_populates="states")


class PayrollOpeningBenefit(Base):
    """Per benefit code, for the year: the annual caps count it."""

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
    employee_amount = _money()
    employer_amount = _money()

    opening = relationship("PayrollOpeningBalance", back_populates="benefits")
    benefit_code = relationship("BenefitCode")
