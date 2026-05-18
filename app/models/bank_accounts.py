# ============================================================================
# Employee bank accounts — direct deposit destinations for NACHA ACH export
# Tier 1.8: routing/account numbers stored encrypted; clear last-4 for display.
# ============================================================================

import enum

from sqlalchemy import (
    Column,
    Integer,
    String,
    Numeric,
    DateTime,
    Enum,
    Boolean,
    Date,
    ForeignKey,
    func,
)
from sqlalchemy.orm import relationship

from app.database import Base


class BankAccountKind(str, enum.Enum):
    CHECKING = "checking"
    SAVINGS = "savings"


class DepositType(str, enum.Enum):
    FULL = "full"  # entire net pay to this account
    PERCENT = "percent"  # a percentage of net pay
    FIXED = "fixed"  # a fixed dollar amount
    REMAINDER = "remainder"  # whatever is left after FIXED/PERCENT splits


class PrenoteStatus(str, enum.Enum):
    NOT_SENT = "not_sent"
    PENDING = "pending"  # zero-dollar test transaction sent, in waiting period
    CONFIRMED = "confirmed"  # cleared the prenote window, safe to credit


class EmployeeBankAccount(Base):
    __tablename__ = "employee_bank_accounts"

    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(
        Integer, ForeignKey("employees.id"), nullable=False, index=True
    )

    nickname = Column(String(100), nullable=True)
    account_kind = Column(Enum(BankAccountKind), default=BankAccountKind.CHECKING)

    # Encrypted at rest (see app.services.encryption). Never expose raw.
    routing_number_enc = Column(String(255), nullable=True)
    account_number_enc = Column(String(255), nullable=True)
    # Clear last-4 of the account number, safe to display.
    account_last_four = Column(String(4), nullable=True)

    # Split-deposit configuration. priority orders multiple accounts; the
    # REMAINDER account is always applied last.
    deposit_type = Column(Enum(DepositType), default=DepositType.FULL)
    deposit_value = Column(
        Numeric(12, 2), default=0
    )  # percent (0-100) or dollar amount
    priority = Column(Integer, default=0)

    prenote_status = Column(Enum(PrenoteStatus), default=PrenoteStatus.NOT_SENT)
    prenote_sent_date = Column(Date, nullable=True)
    is_active = Column(Boolean, default=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    employee = relationship("Employee", back_populates="bank_accounts")
