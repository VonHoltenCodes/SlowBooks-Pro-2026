# ============================================================================
# Paid time off — policies, per-employee balances, and time-off requests
# Tier 1.4: includes WA's paid-sick-leave mandate (1 hr per 40 hrs worked).
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
    ForeignKey,
    Boolean,
    func,
)
from sqlalchemy.orm import relationship

from app.database import Base


class PTOType(str, enum.Enum):
    VACATION = "vacation"
    SICK = "sick"
    PERSONAL = "personal"


class AccrualMethod(str, enum.Enum):
    PER_HOUR_WORKED = "per_hour_worked"  # e.g. WA sick: 1 hr per 40 worked
    PER_PAY_PERIOD = "per_pay_period"  # fixed hours each pay run
    ANNUAL_GRANT = "annual_grant"  # lump grant once a year


class PTORequestStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"


class PTOPolicy(Base):
    __tablename__ = "pto_policies"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(120), nullable=False)
    pto_type = Column(Enum(PTOType), default=PTOType.VACATION)
    accrual_method = Column(Enum(AccrualMethod), default=AccrualMethod.PER_PAY_PERIOD)

    # Interpreted per accrual_method:
    #  PER_HOUR_WORKED -> hours accrued per hour worked (WA sick = 1/40 = 0.025)
    #  PER_PAY_PERIOD  -> hours accrued each pay period
    #  ANNUAL_GRANT    -> hours granted per year
    accrual_rate = Column(Numeric(10, 4), default=0)

    # Carryover cap. 0 / NULL means unlimited. WA paid sick caps carryover at 40.
    max_carryover = Column(Numeric(10, 2), nullable=True)
    # Hard ceiling on the running balance (0 / NULL = no cap).
    max_balance = Column(Numeric(10, 2), nullable=True)
    is_active = Column(Boolean, default=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    accruals = relationship("PTOAccrual", back_populates="policy")


class PTOAccrual(Base):
    __tablename__ = "pto_accruals"

    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(
        Integer, ForeignKey("employees.id"), nullable=False, index=True
    )
    policy_id = Column(Integer, ForeignKey("pto_policies.id"), nullable=False)

    balance = Column(Numeric(10, 2), default=0)
    accrued_ytd = Column(Numeric(10, 2), default=0)
    used_ytd = Column(Numeric(10, 2), default=0)

    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    employee = relationship("Employee")
    policy = relationship("PTOPolicy", back_populates="accruals")


class PTORequest(Base):
    __tablename__ = "pto_requests"

    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(
        Integer, ForeignKey("employees.id"), nullable=False, index=True
    )
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    hours = Column(Numeric(10, 2), default=0)
    pto_type = Column(Enum(PTOType), default=PTOType.VACATION)

    status = Column(Enum(PTORequestStatus), default=PTORequestStatus.PENDING)
    approver_id = Column(Integer, ForeignKey("employees.id"), nullable=True)
    notes = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    employee = relationship("Employee", foreign_keys=[employee_id])
