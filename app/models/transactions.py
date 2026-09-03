# ============================================================================
# Journal models — balanced transactions + debit/credit lines.
# The core double-entry engine — every financial event passes through here.
# ============================================================================
# IMPORTANT: The CHECK constraint below rejects split lines with both debit
# AND credit nonzero — it guards against a real class of corruption bugs.
# Do not remove.
# ============================================================================

from sqlalchemy import (
    Boolean,
    Column,
    Integer,
    String,
    Date,
    Numeric,
    DateTime,
    Text,
    ForeignKey,
    CheckConstraint,
    func,
)
from sqlalchemy.orm import relationship

from app.database import Base


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)
    date = Column(Date, nullable=False, index=True)
    reference = Column(String(100), nullable=True)
    description = Column(Text, nullable=True)  # memo line
    source_type = Column(String(50), nullable=True)
    source_id = Column(Integer, nullable=True)  # FK to the source record

    # Class tracking dimension (QB-style); NULL groups with Uncategorized
    class_id = Column(Integer, ForeignKey("classes.id"), nullable=True)
    # Job-costing dimension (QB "Customer:Job"); NULL = no job
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    lines = relationship(
        "TransactionLine", back_populates="transaction", cascade="all, delete-orphan"
    )


class TransactionLine(Base):
    __tablename__ = "transaction_lines"
    __table_args__ = (
        # A line must be exactly one of debit-only or credit-only.
        CheckConstraint(
            "(debit >= 0 AND credit = 0 AND debit > 0) OR (debit = 0 AND credit >= 0 AND credit > 0)",
            name="ck_debit_or_credit",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    transaction_id = Column(
        Integer,
        ForeignKey("transactions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    account_id = Column(
        Integer,
        ForeignKey("accounts.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    debit = Column(Numeric(12, 2), default=0, nullable=False)  # BCD[6] at offset 0x0C
    credit = Column(Numeric(12, 2), default=0, nullable=False)  # BCD[6] at offset 0x12
    description = Column(String(300), nullable=True)  # split memo, 0x18
    # Per-line job / class; NULL falls back to the transaction header
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=True)
    class_id = Column(Integer, ForeignKey("classes.id"), nullable=True)
    cost_code_id = Column(Integer, ForeignKey("cost_codes.id"), nullable=True)
    # Cost billable to the job's customer; set when it is pulled onto an invoice
    is_billable = Column(Boolean, nullable=False, default=False)
    billed_invoice_line_id = Column(
        Integer, ForeignKey("invoice_lines.id"), nullable=True
    )

    transaction = relationship("Transaction", back_populates="lines")
    account = relationship("Account", back_populates="transaction_lines")
