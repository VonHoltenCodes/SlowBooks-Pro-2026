# ============================================================================
# Credit Memos / Refunds — issue credits against customers, apply to invoices
# Feature 5: Journal entry reverses invoice (DR Income, DR Tax, CR AR)
# ============================================================================

import enum

from sqlalchemy import (
    Boolean,
    Column,
    Integer,
    String,
    Date,
    Numeric,
    DateTime,
    Text,
    Enum,
    ForeignKey,
    false,
    func,
)
from sqlalchemy.orm import relationship

from app.database import Base


class CreditMemoStatus(str, enum.Enum):
    DRAFT = "draft"
    ISSUED = "issued"
    APPLIED = "applied"
    VOID = "void"


class CreditMemo(Base):
    __tablename__ = "credit_memos"

    id = Column(Integer, primary_key=True, index=True)
    memo_number = Column(String(50), unique=True, nullable=False)
    customer_id = Column(
        Integer, ForeignKey("customers.id"), nullable=False, index=True
    )
    status = Column(Enum(CreditMemoStatus), default=CreditMemoStatus.DRAFT)
    original_invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=True)

    date = Column(Date, nullable=False)
    subtotal = Column(Numeric(15, 2), default=0)
    tax_rate = Column(Numeric(7, 6), default=0)  # a fraction: 8.875% is 0.08875
    tax_amount = Column(Numeric(15, 2), default=0)
    total = Column(Numeric(15, 2), default=0)
    amount_applied = Column(Numeric(15, 2), default=0)
    balance_remaining = Column(Numeric(15, 2), default=0)

    notes = Column(Text, nullable=True)
    transaction_id = Column(Integer, ForeignKey("transactions.id"), nullable=True)

    # Class tracking dimension (QB-style); NULL groups with Uncategorized
    class_id = Column(Integer, ForeignKey("classes.id"), nullable=True)
    # A write-off: the credit posts to Bad Debt Expense instead of income
    is_write_off = Column(Boolean, nullable=False, default=False)
    # Job-costing dimension (QB "Customer:Job"); NULL = no job
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=True)
    # An early-payment discount the customer took (#664): the payment it was
    # taken with. Posts to the discount account the payment named; voiding
    # the payment voids it.
    discount_payment_id = Column(Integer, ForeignKey("payments.id"), nullable=True)
    # Taken against the terms with allow_discount_override (#664).
    discount_override = Column(
        Boolean, nullable=False, default=False, server_default=false()
    )

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    customer = relationship("Customer", backref="credit_memos")
    original_invoice = relationship("Invoice", foreign_keys=[original_invoice_id])
    lines = relationship(
        "CreditMemoLine",
        back_populates="credit_memo",
        cascade="all, delete-orphan",
        order_by="CreditMemoLine.line_order",
    )
    transaction = relationship("Transaction", foreign_keys=[transaction_id])
    applications = relationship(
        "CreditApplication", back_populates="credit_memo", cascade="all, delete-orphan"
    )


class CreditMemoLine(Base):
    __tablename__ = "credit_memo_lines"

    id = Column(Integer, primary_key=True, index=True)
    credit_memo_id = Column(
        Integer, ForeignKey("credit_memos.id", ondelete="CASCADE"), nullable=False
    )
    item_id = Column(Integer, ForeignKey("items.id"), nullable=True)
    description = Column(Text, nullable=True)
    quantity = Column(Numeric(12, 4), default=1)
    # Unit price to four places: bulk goods are priced like $0.045 a box.
    # The line amount is still rounded to the cent (accounting._q).
    rate = Column(Numeric(17, 4), default=0)
    amount = Column(Numeric(15, 2), default=0)
    # The line's own class and job (#640); NULL falls back to the memo's
    # header, as an invoice line's does, so a memo that credits work in two
    # divisions reverses each division's income on the P&L by Class.
    class_id = Column(Integer, ForeignKey("classes.id"), nullable=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=True)
    line_order = Column(Integer, default=0)

    credit_memo = relationship("CreditMemo", back_populates="lines")
    item = relationship("Item")


class CreditApplication(Base):
    __tablename__ = "credit_applications"

    id = Column(Integer, primary_key=True, index=True)
    credit_memo_id = Column(
        Integer, ForeignKey("credit_memos.id", ondelete="CASCADE"), nullable=False
    )
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=False)
    amount = Column(Numeric(15, 2), nullable=False)

    credit_memo = relationship("CreditMemo", back_populates="applications")
    invoice = relationship("Invoice", backref="credit_applications")
