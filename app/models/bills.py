# ============================================================================
# Bills & Bill Payments — full AP mirror of the AR system
# Feature 1: Enter bills, track payables, pay bills
# Journal: Bill create → DR Expense, CR AP (2000)
#          Bill payment → DR AP (2000), CR Bank
# ============================================================================

import enum

from sqlalchemy import (
    Column, Integer, String, Date, Numeric, DateTime, Text, Enum,
    ForeignKey, func,
)
from sqlalchemy.orm import relationship

from app.database import Base


class BillStatus(str, enum.Enum):
    DRAFT = "draft"
    UNPAID = "unpaid"
    PARTIAL = "partial"
    PAID = "paid"
    VOID = "void"


class Bill(Base):
    __tablename__ = "bills"

    id = Column(Integer, primary_key=True, index=True)
    bill_number = Column(String(100), nullable=False)
    vendor_id = Column(Integer, ForeignKey("vendors.id"), nullable=False, index=True)
    status = Column(Enum(BillStatus), default=BillStatus.UNPAID, index=True)
    po_id = Column(Integer, ForeignKey("purchase_orders.id", ondelete="SET NULL"), nullable=True)

    date = Column(Date, nullable=False)
    due_date = Column(Date, nullable=True)
    terms = Column(String(50), default="Net 30")
    ref_number = Column(String(100), nullable=True)

    subtotal = Column(Numeric(12, 2), default=0)
    tax_rate = Column(Numeric(5, 4), default=0)
    tax_amount = Column(Numeric(12, 2), default=0)
    total = Column(Numeric(12, 2), default=0)
    amount_paid = Column(Numeric(12, 2), default=0)
    balance_due = Column(Numeric(12, 2), default=0)

    notes = Column(Text, nullable=True)
    transaction_id = Column(Integer, ForeignKey("transactions.id"), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    vendor = relationship("Vendor", backref="bills")
    purchase_order = relationship("PurchaseOrder", foreign_keys=[po_id])
    lines = relationship("BillLine", back_populates="bill", cascade="all, delete-orphan",
                         order_by="BillLine.line_order")
    transaction = relationship("Transaction", foreign_keys=[transaction_id])


class BillLine(Base):
    __tablename__ = "bill_lines"

    id = Column(Integer, primary_key=True, index=True)
    bill_id = Column(Integer, ForeignKey("bills.id", ondelete="CASCADE"), nullable=False, index=True)
    item_id = Column(Integer, ForeignKey("items.id"), nullable=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)  # expense account
    description = Column(Text, nullable=True)
    quantity = Column(Numeric(10, 2), default=1)
    rate = Column(Numeric(12, 2), default=0)
    amount = Column(Numeric(12, 2), default=0)
    line_order = Column(Integer, default=0)

    bill = relationship("Bill", back_populates="lines")
    item = relationship("Item")
    account = relationship("Account")


class BillPayment(Base):
    __tablename__ = "bill_payments"

    id = Column(Integer, primary_key=True, index=True)
    vendor_id = Column(Integer, ForeignKey("vendors.id"), nullable=False)
    date = Column(Date, nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    method = Column(String(50), nullable=True)
    check_number = Column(String(50), nullable=True)
    pay_from_account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)
    notes = Column(Text, nullable=True)
    transaction_id = Column(Integer, ForeignKey("transactions.id"), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    vendor = relationship("Vendor", backref="bill_payments")
    pay_from_account = relationship("Account", foreign_keys=[pay_from_account_id])
    transaction = relationship("Transaction", foreign_keys=[transaction_id])
    allocations = relationship("BillPaymentAllocation", back_populates="bill_payment",
                               cascade="all, delete-orphan")


class BillPaymentAllocation(Base):
    __tablename__ = "bill_payment_allocations"

    id = Column(Integer, primary_key=True, index=True)
    bill_payment_id = Column(Integer, ForeignKey("bill_payments.id", ondelete="CASCADE"), nullable=False)
    bill_id = Column(Integer, ForeignKey("bills.id", ondelete="RESTRICT"), nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)

    bill_payment = relationship("BillPayment", back_populates="allocations")
    bill = relationship("Bill", backref="payment_allocations")
