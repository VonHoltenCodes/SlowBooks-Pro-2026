# ============================================================================
# Invoices — header + line items.
# Every invoice generates a balanced journal entry (see transactions.py).
# ============================================================================
# Fun fact: QB2003 hardcoded a max of 14 characters for invoice numbers.
# We lifted that to 50 because it's not 2003 anymore. Mostly.
# ============================================================================

import enum
import uuid

from sqlalchemy import (
    Column,
    Integer,
    String,
    Date,
    Numeric,
    DateTime,
    Text,
    Enum,
    ForeignKey,
    func,
)
from sqlalchemy.orm import relationship

from app.database import Base


class InvoiceStatus(str, enum.Enum):
    DRAFT = "draft"  # "Pending" in the QB2003 UI
    SENT = "sent"
    PARTIAL = "partial"
    PAID = "paid"
    VOID = "void"


class Invoice(Base):
    __tablename__ = "invoices"

    id = Column(Integer, primary_key=True, index=True)
    invoice_number = Column(String(50), unique=True, nullable=False)
    customer_id = Column(
        Integer, ForeignKey("customers.id"), nullable=False, index=True
    )
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

    # Online payments (Stripe / any registered provider)
    payment_token = Column(
        String(36),
        unique=True,
        nullable=True,
        index=True,
        default=lambda: str(uuid.uuid4()),
    )
    stripe_checkout_session_id = Column(String(255), nullable=True)
    # Most recent checkout attempt: which provider, and its session/order id.
    # checkout_external_id is indexed because providers without metadata
    # passthrough (Square) resolve the invoice by this id on webhook/poll.
    checkout_provider = Column(String(20), nullable=True)
    checkout_external_id = Column(String(255), nullable=True, index=True)

    # Class tracking dimension (QB-style); NULL groups with Uncategorized
    class_id = Column(Integer, ForeignKey("classes.id"), nullable=True)

    # Multi-currency: document currency + rate it was booked at (GL is home)
    currency = Column(String(3), nullable=True)
    exchange_rate = Column(Numeric(18, 8), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    customer = relationship("Customer", backref="invoices")
    lines = relationship(
        "InvoiceLine",
        back_populates="invoice",
        cascade="all, delete-orphan",
        order_by="InvoiceLine.line_order",
    )
    transaction = relationship("Transaction", foreign_keys=[transaction_id])


class InvoiceLine(Base):
    __tablename__ = "invoice_lines"

    id = Column(Integer, primary_key=True, index=True)
    invoice_id = Column(
        Integer, ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False
    )
    item_id = Column(Integer, ForeignKey("items.id"), nullable=True)
    description = Column(Text, nullable=True)
    quantity = Column(Numeric(10, 2), default=1)
    rate = Column(Numeric(12, 2), default=0)
    amount = Column(Numeric(12, 2), default=0)
    class_name = Column(String(100), nullable=True)
    line_order = Column(Integer, default=0)

    invoice = relationship("Invoice", back_populates="lines")
    item = relationship("Item")
