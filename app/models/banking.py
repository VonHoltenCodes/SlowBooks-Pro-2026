# ============================================================================
# Bank accounts, bank-feed transactions, and reconciliations.
# ============================================================================

import enum

from sqlalchemy import (
    Column,
    Integer,
    String,
    Date,
    Numeric,
    DateTime,
    Boolean,
    Enum,
    ForeignKey,
    func,
)
from sqlalchemy.orm import relationship

from app.database import Base


class ReconciliationStatus(str, enum.Enum):
    IN_PROGRESS = "in_progress"  # RECON.DAT status byte 0x00
    COMPLETED = "completed"  # status byte 0x01


class BankAccount(Base):
    __tablename__ = "bank_accounts"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    account_id = Column(
        Integer, ForeignKey("accounts.id"), nullable=True
    )  # linked COA account
    bank_name = Column(String(200), nullable=True)
    last_four = Column(String(4), nullable=True)
    # Pre-2.10 the register kept its own balance here. The balance is now
    # the linked GL account's; this holds the old number until the user
    # posts it as an opening balance or dismisses it (Banking page banner).
    legacy_balance = Column(Numeric(15, 2), nullable=True)
    is_active = Column(Boolean, default=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    account = relationship("Account", foreign_keys=[account_id])
    transactions = relationship("BankTransaction", back_populates="bank_account")


class BankTransaction(Base):
    __tablename__ = "bank_transactions"

    id = Column(Integer, primary_key=True, index=True)
    bank_account_id = Column(
        Integer, ForeignKey("bank_accounts.id"), nullable=False, index=True
    )
    date = Column(Date, nullable=False, index=True)
    amount = Column(
        Numeric(15, 2), nullable=False
    )  # positive=deposit, negative=withdrawal
    payee = Column(String(200), nullable=True)
    description = Column(String(500), nullable=True)
    check_number = Column(String(50), nullable=True)
    category_account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)
    reconciled = Column(Boolean, default=False)  # pre-2.10 side-ledger tick
    # The posting this statement line is matched to (or was added as): the
    # journal entry and the specific line on the bank account (a transfer
    # has two bank lines, so the line matters).
    transaction_id = Column(Integer, ForeignKey("transactions.id"), nullable=True)
    transaction_line_id = Column(
        Integer, ForeignKey("transaction_lines.id"), nullable=True
    )

    # OFX/QFX import fields (Feature 18)
    import_id = Column(String(100), nullable=True)  # OFX FITID for dedup
    import_source = Column(String(50), nullable=True)  # e.g. "ofx", "qfx"
    # "unmatched" | "auto" | "manual" | "added" | "excluded" | "in_books"
    # (auto/manual = linked to an existing posting; added = posted from here;
    # in_books = excluded by an import as already in the books, W-116)
    match_status = Column(String(20), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    bank_account = relationship("BankAccount", back_populates="transactions")
    category_account = relationship("Account", foreign_keys=[category_account_id])
    transaction = relationship("Transaction", foreign_keys=[transaction_id])
    transaction_line = relationship(
        "TransactionLine", foreign_keys=[transaction_line_id]
    )


class Reconciliation(Base):
    __tablename__ = "reconciliations"

    id = Column(Integer, primary_key=True, index=True)
    # Reconciliation is over the GL account's lines (2.10); bank_account_id
    # is the pre-2.10 key, kept for old rows.
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)
    bank_account_id = Column(Integer, ForeignKey("bank_accounts.id"), nullable=True)
    statement_date = Column(Date, nullable=False)
    # The latest statement date this session has ever had (its high-water
    # mark). Moving the date earlier leaves lines that were in reach before
    # ticked but off the session; this remembers the old window so we can
    # count only those lines, not every cleared line after the date (#400
    # review). Set to statement_date at start; only ever raised.
    max_statement_date = Column(Date, nullable=True)
    statement_balance = Column(Numeric(15, 2), nullable=False)
    # The prior completed statement's balance; cleared_total is stamped on
    # completion so the history reads without recomputing.
    beginning_balance = Column(Numeric(15, 2), nullable=False, default=0)
    cleared_total = Column(Numeric(15, 2), nullable=True)
    status = Column(
        Enum(ReconciliationStatus), default=ReconciliationStatus.IN_PROGRESS
    )

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)

    bank_account = relationship("BankAccount")
    account = relationship("Account", foreign_keys=[account_id])

    @property
    def account_name(self):
        """The ledger account's name, for a list across accounts (#395)."""
        return self.account.name if self.account else None
