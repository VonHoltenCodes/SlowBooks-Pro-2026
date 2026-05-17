# ============================================================================
# Payroll deductions — pre-tax / post-tax voluntary deductions and garnishments
# Tier 2: each pre-tax deduction reduces federal / state / FICA wages
# differently; garnishments carry CCPA limits and a multi-order priority.
# ============================================================================

import enum

from sqlalchemy import (
    Column, Integer, String, Numeric, DateTime, Enum, Boolean, ForeignKey, func,
)
from sqlalchemy.orm import relationship

from app.database import Base


class DeductionCategory(str, enum.Enum):
    PRETAX = "pretax"
    POSTTAX = "posttax"


class CalcMethod(str, enum.Enum):
    FIXED = "fixed"        # a fixed dollar amount per pay period
    PERCENT = "percent"    # a percentage of gross pay


class DeductionType(Base):
    """A catalogued kind of deduction with its wage-base tax treatment.

    The three `reduces_*` flags are what make pre-tax deductions correct:
    a traditional 401(k) reduces income-tax wages but NOT FICA wages, while a
    Section 125 cafeteria-plan premium or HSA contribution reduces all three.
    """
    __tablename__ = "deduction_types"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(120), nullable=False)
    code = Column(String(30), nullable=True)
    category = Column(Enum(DeductionCategory), default=DeductionCategory.PRETAX)

    reduces_federal = Column(Boolean, default=False)
    reduces_state = Column(Boolean, default=False)
    reduces_fica = Column(Boolean, default=False)

    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class EmployeeDeduction(Base):
    __tablename__ = "employee_deductions"

    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False, index=True)
    deduction_type_id = Column(Integer, ForeignKey("deduction_types.id"), nullable=False)

    calc_method = Column(Enum(CalcMethod), default=CalcMethod.FIXED)
    amount = Column(Numeric(12, 2), default=0)   # dollars (fixed) or percent (percent)
    # Annual cap (e.g. the 401(k) elective-deferral limit). NULL = uncapped.
    annual_limit = Column(Numeric(12, 2), nullable=True)
    is_active = Column(Boolean, default=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    employee = relationship("Employee")
    deduction_type = relationship("DeductionType")


class GarnishmentType(str, enum.Enum):
    CHILD_SUPPORT = "child_support"
    FEDERAL_LEVY = "federal_levy"
    STATE_TAX_LEVY = "state_tax_levy"
    STUDENT_LOAN = "student_loan"
    BANKRUPTCY = "bankruptcy"
    CREDITOR = "creditor"


class GarnishmentMethod(str, enum.Enum):
    FIXED = "fixed"
    PERCENT_DISPOSABLE = "percent_disposable"


class GarnishmentOrder(Base):
    __tablename__ = "garnishment_orders"

    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False, index=True)
    garnishment_type = Column(Enum(GarnishmentType), default=GarnishmentType.CREDITOR)
    calc_method = Column(Enum(GarnishmentMethod), default=GarnishmentMethod.FIXED)
    amount = Column(Numeric(12, 2), default=0)   # dollars (fixed) or percent (percent_disposable)

    priority = Column(Integer, default=0)
    case_number = Column(String(80), nullable=True)
    # Child-support CCPA modifiers.
    supports_secondary_family = Column(Boolean, default=False)
    in_arrears_12_weeks = Column(Boolean, default=False)

    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    employee = relationship("Employee")
