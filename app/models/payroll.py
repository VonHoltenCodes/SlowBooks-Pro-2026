# ============================================================================
# Payroll — employee records, pay runs, withholding calculations
# Feature 17: Simplified payroll with federal/state/SS/Medicare
# ============================================================================

import enum

from sqlalchemy import (
    Column, Integer, String, Date, Numeric, DateTime, Text, Enum, Boolean,
    ForeignKey, func,
)
from sqlalchemy.orm import relationship

from app.database import Base


class PayType(str, enum.Enum):
    SALARY = "salary"
    HOURLY = "hourly"


class FilingStatus(str, enum.Enum):
    SINGLE = "single"
    MARRIED = "married"
    HEAD_OF_HOUSEHOLD = "head_of_household"


class PayRunStatus(str, enum.Enum):
    DRAFT = "draft"
    PROCESSED = "processed"
    VOID = "void"


class Employee(Base):
    __tablename__ = "employees"

    id = Column(Integer, primary_key=True, index=True)
    first_name = Column(String(100), nullable=False)
    last_name = Column(String(100), nullable=False)
    ssn_last_four = Column(String(4), nullable=True)
    pay_type = Column(Enum(PayType), default=PayType.HOURLY)
    pay_rate = Column(Numeric(12, 2), default=0)  # hourly rate or salary amount
    filing_status = Column(Enum(FilingStatus), default=FilingStatus.SINGLE)
    allowances = Column(Integer, default=0)

    address1 = Column(String(200), nullable=True)
    address2 = Column(String(200), nullable=True)
    city = Column(String(100), nullable=True)
    state = Column(String(50), nullable=True)
    zip = Column(String(20), nullable=True)

    hire_date = Column(Date, nullable=True)
    is_active = Column(Boolean, default=True)
    notes = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    pay_stubs = relationship("PayStub", back_populates="employee")


# Fix missing Boolean import
from sqlalchemy import Boolean


class PayRun(Base):
    __tablename__ = "pay_runs"

    id = Column(Integer, primary_key=True, index=True)
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)
    pay_date = Column(Date, nullable=False)
    status = Column(Enum(PayRunStatus), default=PayRunStatus.DRAFT)

    total_gross = Column(Numeric(12, 2), default=0)
    total_net = Column(Numeric(12, 2), default=0)
    total_taxes = Column(Numeric(12, 2), default=0)
    transaction_id = Column(Integer, ForeignKey("transactions.id"), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    transaction = relationship("Transaction", foreign_keys=[transaction_id])
    stubs = relationship("PayStub", back_populates="pay_run", cascade="all, delete-orphan")


class PayStub(Base):
    __tablename__ = "pay_stubs"

    id = Column(Integer, primary_key=True, index=True)
    pay_run_id = Column(Integer, ForeignKey("pay_runs.id", ondelete="CASCADE"), nullable=False)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False, index=True)

    hours = Column(Numeric(10, 2), default=0)
    gross_pay = Column(Numeric(12, 2), default=0)
    federal_tax = Column(Numeric(12, 2), default=0)
    state_tax = Column(Numeric(12, 2), default=0)
    ss_tax = Column(Numeric(12, 2), default=0)       # Social Security 6.2%
    medicare_tax = Column(Numeric(12, 2), default=0)  # Medicare 1.45%
    net_pay = Column(Numeric(12, 2), default=0)

    pay_run = relationship("PayRun", back_populates="stubs")
    employee = relationship("Employee", back_populates="pay_stubs")
