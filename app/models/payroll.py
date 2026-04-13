# ============================================================================
# Payroll — NZ employee records, pay runs, and future PAYE workflows
# Rebuilt for SlowBooks NZ. PAYE calculations and payday filing follow in
# later slices; this file defines the NZ setup shape only.
# ============================================================================

import enum
from decimal import Decimal

from sqlalchemy import (
    Column, Integer, String, Date, Numeric, DateTime, Text, Enum, Boolean,
    ForeignKey, func,
)
from sqlalchemy.orm import relationship

from app.database import Base


class PayType(str, enum.Enum):
    SALARY = "salary"
    HOURLY = "hourly"


class PayRunStatus(str, enum.Enum):
    DRAFT = "draft"
    PROCESSED = "processed"
    VOID = "void"


class Employee(Base):
    __tablename__ = "employees"

    id = Column(Integer, primary_key=True, index=True)
    first_name = Column(String(100), nullable=False)
    last_name = Column(String(100), nullable=False)
    ird_number = Column(String(20), nullable=True)
    pay_type = Column(Enum(PayType), default=PayType.HOURLY)
    pay_rate = Column(Numeric(12, 2), default=0)
    tax_code = Column(String(20), default="M")
    kiwisaver_enrolled = Column(Boolean, default=False)
    kiwisaver_rate = Column(Numeric(6, 4), default=Decimal("0.0300"))
    student_loan = Column(Boolean, default=False)
    child_support = Column(Boolean, default=False)
    esct_rate = Column(Numeric(6, 4), default=Decimal("0.0000"))
    pay_frequency = Column(String(20), default="fortnightly")

    address1 = Column(String(200), nullable=True)
    address2 = Column(String(200), nullable=True)
    city = Column(String(100), nullable=True)
    state = Column(String(50), nullable=True)
    zip = Column(String(20), nullable=True)

    start_date = Column(Date, nullable=True)
    end_date = Column(Date, nullable=True)
    is_active = Column(Boolean, default=True)
    notes = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    pay_stubs = relationship("PayStub", back_populates="employee")


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
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False)

    hours = Column(Numeric(10, 2), default=0)
    gross_pay = Column(Numeric(12, 2), default=0)
    federal_tax = Column(Numeric(12, 2), default=0)
    state_tax = Column(Numeric(12, 2), default=0)
    ss_tax = Column(Numeric(12, 2), default=0)
    medicare_tax = Column(Numeric(12, 2), default=0)
    net_pay = Column(Numeric(12, 2), default=0)

    pay_run = relationship("PayRun", back_populates="stubs")
    employee = relationship("Employee", back_populates="pay_stubs")
