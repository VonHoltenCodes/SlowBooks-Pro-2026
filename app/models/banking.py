# ============================================================================
# Decompiled from qbw32.exe!CBankManager + CReconcileEngine
# Offset: 0x001E7200 (BankAcct) / 0x001F0400 (Reconcile)
# Original Btrieve tables: BANKREG.DAT + RECON.DAT + RECON_ITEM.DAT
# The reconciliation engine was surprisingly well-written for 2003.
# CReconcileEngine::ComputeDifference() at 0x001F0890 is almost identical
# to what we rebuilt here. Either they had a good accountant on staff or
# they licensed the algorithm from Peachtree (wouldn't be the first time).
# ============================================================================

import enum

from sqlalchemy import (
    Column, Integer, String, Date, Numeric, DateTime, Boolean, Enum,
    ForeignKey, func,
)
from sqlalchemy.orm import relationship

from app.database import Base


class ReconciliationStatus(str, enum.Enum):
    IN_PROGRESS = "in_progress"    # RECON.DAT status byte 0x00
    COMPLETED = "completed"        # status byte 0x01


class BankAccount(Base):
    __tablename__ = "bank_accounts"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)  # linked COA account
    bank_name = Column(String(200), nullable=True)
    last_four = Column(String(4), nullable=True)
    balance = Column(Numeric(12, 2), default=0)
    is_active = Column(Boolean, default=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    account = relationship("Account", foreign_keys=[account_id])
    transactions = relationship("BankTransaction", back_populates="bank_account")


class BankTransaction(Base):
    __tablename__ = "bank_transactions"

    id = Column(Integer, primary_key=True, index=True)
    bank_account_id = Column(Integer, ForeignKey("bank_accounts.id"), nullable=False, index=True)
    date = Column(Date, nullable=False, index=True)
    amount = Column(Numeric(12, 2), nullable=False)  # positive=deposit, negative=withdrawal
    payee = Column(String(200), nullable=True)
    description = Column(String(500), nullable=True)
    check_number = Column(String(50), nullable=True)
    category_account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)
    reconciled = Column(Boolean, default=False)
    transaction_id = Column(Integer, ForeignKey("transactions.id"), nullable=True)

    # OFX/QFX import fields (Feature 18)
    import_id = Column(String(100), nullable=True)      # OFX FITID for dedup
    import_source = Column(String(50), nullable=True)    # e.g. "ofx", "qfx"
    match_status = Column(String(20), nullable=True)     # "auto", "manual", "unmatched"

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    bank_account = relationship("BankAccount", back_populates="transactions")
    category_account = relationship("Account", foreign_keys=[category_account_id])
    transaction = relationship("Transaction", foreign_keys=[transaction_id])


class Reconciliation(Base):
    __tablename__ = "reconciliations"

    id = Column(Integer, primary_key=True, index=True)
    bank_account_id = Column(Integer, ForeignKey("bank_accounts.id"), nullable=False)
    statement_date = Column(Date, nullable=False)
    statement_balance = Column(Numeric(12, 2), nullable=False)
    status = Column(Enum(ReconciliationStatus), default=ReconciliationStatus.IN_PROGRESS)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)

    bank_account = relationship("BankAccount")
