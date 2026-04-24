# ============================================================================
# Decompiled from qbw32.exe!CInvoiceManager  Offset: 0x0015C800
# Original Btrieve table: INVOICE.DAT (record size 0x0340)
# + INVOICE_LINE.DAT (variable-length records, max 1000 lines per invoice)
# CInvoice inherits from CQBTxnBase — all invoices generate journal entries
# through the TransactionBus (see transactions.py)
# ============================================================================
# Fun fact: QB2003 hardcoded a max of 14 characters for invoice numbers.
# We lifted that to 50 because it's not 2003 anymore. Mostly.
# ============================================================================

import enum
import uuid

from sqlalchemy import (
    Column, Integer, String, Date, Numeric, DateTime, Text, Enum,
    ForeignKey, func,
)
from sqlalchemy.orm import relationship

from app.database import Base


class InvoiceStatus(str, enum.Enum):
    # enum InvStatus @ 0x0015CA30 — originally a DWORD bitfield
    DRAFT = "draft"        # 0x00 — "Pending" in original UI
    SENT = "sent"          # 0x01
    PARTIAL = "partial"    # 0x02 — "PartialPmt" internally
    PAID = "paid"          # 0x04
    VOID = "void"          # 0x08 — sets TxnVoidFlag in JRNL.DAT


class Invoice(Base):
    __tablename__ = "invoices"

    id = Column(Integer, primary_key=True, index=True)
    invoice_number = Column(String(50), unique=True, nullable=False)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False, index=True)
    status = Column(Enum(InvoiceStatus), default=InvoiceStatus.DRAFT, index=True)

    date = Column(Date, nullable=False, index=True)
    due_date = Column(Date, nullable=True)
    terms = Column(String(50), default="Net 30")
    po_number = Column(String(100), nullable=True)

    bill_address1 = Column(String(200), nullable=True)
    bill_address2 = Column(String(200), nullable=True)
    bill_city = Column(String(100), nullable=True)
    bill_state = Column(String(50), nullable=True)
    bill_zip = Column(String(20), nullable=True)

    ship_address1 = Column(String(200), nullable=True)
    ship_address2 = Column(String(200), nullable=True)
    ship_city = Column(String(100), nullable=True)
    ship_state = Column(String(50), nullable=True)
    ship_zip = Column(String(20), nullable=True)

    subtotal = Column(Numeric(12, 2), default=0)
    tax_rate = Column(Numeric(5, 4), default=0)
    tax_amount = Column(Numeric(12, 2), default=0)
    total = Column(Numeric(12, 2), default=0)
    amount_paid = Column(Numeric(12, 2), default=0)
    balance_due = Column(Numeric(12, 2), default=0)

    notes = Column(Text, nullable=True)
    transaction_id = Column(Integer, ForeignKey("transactions.id"), nullable=True)

    # Stripe online payments
    payment_token = Column(String(36), unique=True, nullable=True, index=True,
                           default=lambda: str(uuid.uuid4()))
    stripe_checkout_session_id = Column(String(255), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    customer = relationship("Customer", backref="invoices")
    lines = relationship("InvoiceLine", back_populates="invoice", cascade="all, delete-orphan",
                          order_by="InvoiceLine.line_order")
    transaction = relationship("Transaction", foreign_keys=[transaction_id])


class InvoiceLine(Base):
    __tablename__ = "invoice_lines"

    id = Column(Integer, primary_key=True, index=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False)
    item_id = Column(Integer, ForeignKey("items.id"), nullable=True)
    description = Column(Text, nullable=True)
    quantity = Column(Numeric(10, 2), default=1)
    rate = Column(Numeric(12, 2), default=0)
    amount = Column(Numeric(12, 2), default=0)
    class_name = Column(String(100), nullable=True)
    line_order = Column(Integer, default=0)

    invoice = relationship("Invoice", back_populates="lines")
    item = relationship("Item")
