# ============================================================================
# Chart of Accounts.
# ============================================================================

import enum

from sqlalchemy import (
    Column,
    Integer,
    String,
    Enum,
    Boolean,
    ForeignKey,
    Numeric,
    DateTime,
    func,
)
from sqlalchemy.orm import relationship

from app.database import Base


class AccountType(str, enum.Enum):
    ASSET = "asset"
    LIABILITY = "liability"
    EQUITY = "equity"
    INCOME = "income"
    EXPENSE = "expense"
    COGS = "cogs"


class Account(Base):
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    account_number = Column(String(20), unique=True, nullable=True)
    account_type = Column(Enum(AccountType), nullable=False)
    parent_id = Column(
        Integer, ForeignKey("accounts.id"), nullable=True
    )  # sub-account ref
    description = Column(String(500), nullable=True)
    is_active = Column(Boolean, default=True)
    is_system = Column(Boolean, default=False)  # seed accounts can't be deleted
    balance = Column(Numeric(12, 2), default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    parent = relationship("Account", remote_side=[id], backref="children")
    transaction_lines = relationship("TransactionLine", back_populates="account")
