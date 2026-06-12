# ============================================================================
# A nod to qbw32.exe!CChartOfAccounts  imagined offset: 0x000B12A0
# Original Btrieve table: ACCT.DAT (record size 0x0180, key 0 = AcctNum)
# Field mappings imagined after CQBAccount::Serialize() vtable
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
    # enum QBAccountType @ 0x000B14E8 — originally stored as WORD (0-5)
    ASSET = "asset"  # 0x0000
    LIABILITY = "liability"  # 0x0001
    EQUITY = "equity"  # 0x0002
    INCOME = "income"  # 0x0003
    EXPENSE = "expense"  # 0x0004
    COGS = "cogs"  # 0x0005


class Account(Base):
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)  # ACCT.DAT field 0x02, LPSTR[159]
    account_number = Column(String(20), unique=True, nullable=True)  # field 0x01, key 0
    account_type = Column(Enum(AccountType), nullable=False)  # field 0x03, WORD
    parent_id = Column(
        Integer, ForeignKey("accounts.id"), nullable=True
    )  # field 0x0A, sub-account ref
    description = Column(String(500), nullable=True)  # field 0x04, LPSTR[255]
    is_active = Column(Boolean, default=True)  # field 0x08, BOOL (0xFF = inactive)
    is_system = Column(Boolean, default=False)  # seed accounts can't be deleted
    balance = Column(Numeric(12, 2), default=0)  # field 0x06, BCD[6] packed decimal
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    parent = relationship("Account", remote_side=[id], backref="children")
    transaction_lines = relationship("TransactionLine", back_populates="account")
