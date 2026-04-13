# ============================================================================
# Credit Memos / Refunds — issue credits against customers, apply to invoices
# Feature 5: Journal entry reverses invoice (DR Income, DR Tax, CR AR)
# ============================================================================

import enum
from decimal import Decimal

from sqlalchemy import (
    Column, Integer, String, Date, Numeric, DateTime, Text, Enum,
    ForeignKey, func,
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
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    status = Column(Enum(CreditMemoStatus), default=CreditMemoStatus.DRAFT)
    original_invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=True)

    date = Column(Date, nullable=False)
    subtotal = Column(Numeric(12, 2), default=0)
    tax_rate = Column(Numeric(5, 4), default=0)
    tax_amount = Column(Numeric(12, 2), default=0)
    total = Column(Numeric(12, 2), default=0)
    amount_applied = Column(Numeric(12, 2), default=0)
    balance_remaining = Column(Numeric(12, 2), default=0)

    notes = Column(Text, nullable=True)
    transaction_id = Column(Integer, ForeignKey("transactions.id"), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    customer = relationship("Customer", backref="credit_memos")
    original_invoice = relationship("Invoice", foreign_keys=[original_invoice_id])
    lines = relationship("CreditMemoLine", back_populates="credit_memo",
                         cascade="all, delete-orphan", order_by="CreditMemoLine.line_order")
    transaction = relationship("Transaction", foreign_keys=[transaction_id])
    applications = relationship("CreditApplication", back_populates="credit_memo",
                                cascade="all, delete-orphan")


class CreditMemoLine(Base):
    __tablename__ = "credit_memo_lines"

    def __init__(self, **kwargs):
        kwargs.setdefault("gst_code", "GST15")
        kwargs.setdefault("gst_rate", Decimal("0.1500"))
        super().__init__(**kwargs)

    id = Column(Integer, primary_key=True, index=True)
    credit_memo_id = Column(Integer, ForeignKey("credit_memos.id", ondelete="CASCADE"), nullable=False)
    item_id = Column(Integer, ForeignKey("items.id"), nullable=True)
    description = Column(Text, nullable=True)
    quantity = Column(Numeric(10, 2), default=1)
    rate = Column(Numeric(12, 2), default=0)
    amount = Column(Numeric(12, 2), default=0)
    gst_code = Column(String(20), ForeignKey("gst_codes.code"), default="GST15", nullable=False)
    gst_rate = Column(Numeric(6, 4), default=Decimal("0.1500"), nullable=False)
    line_order = Column(Integer, default=0)

    credit_memo = relationship("CreditMemo", back_populates="lines")
    item = relationship("Item")
    gst = relationship("GstCode", foreign_keys=[gst_code])


class CreditApplication(Base):
    __tablename__ = "credit_applications"

    id = Column(Integer, primary_key=True, index=True)
    credit_memo_id = Column(Integer, ForeignKey("credit_memos.id", ondelete="CASCADE"), nullable=False)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)

    credit_memo = relationship("CreditMemo", back_populates="applications")
    invoice = relationship("Invoice", backref="credit_applications")
