# ============================================================================
# Bills & Bill Payments — full AP mirror of the AR system
# Feature 1: Enter bills, track payables, pay bills
# Journal: Bill create → DR Expense, CR AP (2000)
#          Bill payment → DR AP (2000), CR Bank
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
    Index,
    false,
    func,
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
    # One bill per vendor invoice number (#501): the route's check is a
    # read, which two sign-ins on PostgreSQL can both pass; the index is
    # what refuses the second.
    __table_args__ = (
        Index("uq_bills_vendor_bill_number", "vendor_id", "bill_number", unique=True),
    )

    id = Column(Integer, primary_key=True, index=True)
    bill_number = Column(String(100), nullable=False)
    vendor_id = Column(Integer, ForeignKey("vendors.id"), nullable=False, index=True)
    status = Column(Enum(BillStatus), default=BillStatus.UNPAID, index=True)
    po_id = Column(
        Integer, ForeignKey("purchase_orders.id", ondelete="SET NULL"), nullable=True
    )

    date = Column(Date, nullable=False)
    due_date = Column(Date, nullable=True)
    terms = Column(String(50), default="Net 30")
    # The list entry these terms named when the bill was saved (#664),
    # whose rule gives its discount; NULL for text that named none then
    # (free text, or a bill from before the list), which keeps the old
    # reading of "Net N" and nothing more, however the list changes later.
    payment_term_id = Column(Integer, ForeignKey("payment_terms.id"), nullable=True)
    ref_number = Column(String(100), nullable=True)

    subtotal = Column(Numeric(15, 2), default=0)
    tax_rate = Column(Numeric(7, 6), default=0)  # a fraction: 8.875% is 0.08875
    tax_amount = Column(Numeric(15, 2), default=0)
    total = Column(Numeric(15, 2), default=0)
    amount_paid = Column(Numeric(15, 2), default=0)
    balance_due = Column(Numeric(15, 2), default=0)

    # Construction (#553): retainage held back on this bill (credited to
    # Retainage Payable, 2050, instead of A/P), and the sub's lien-waiver
    # status for the period. Retainage release is #555.
    retainage_held = Column(Numeric(15, 2), nullable=False, default=0)
    # Retainage released on this bill (#555): a release bill debits Retainage
    # Payable (2050) and credits A/P for this amount, payable through Pay Bills.
    retainage_released = Column(Numeric(15, 2), nullable=False, default=0)
    # True once a request named retainage_held (a figure set by hand); a
    # converted bill whose figure is still the commitment's rate on its
    # subtotal stays False and follows the rate on every re-post.
    retainage_manual = Column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    lien_waiver_status = Column(String(16), nullable=True)

    notes = Column(Text, nullable=True)
    transaction_id = Column(Integer, ForeignKey("transactions.id"), nullable=True)

    # Class tracking dimension (QB-style); NULL groups with Uncategorized
    class_id = Column(Integer, ForeignKey("classes.id"), nullable=True)
    # Job-costing dimension (QB "Customer:Job"); NULL = no job
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=True)

    # Multi-currency: document currency + rate it was booked at (GL is home)
    currency = Column(String(3), nullable=True)
    exchange_rate = Column(Numeric(18, 8), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    vendor = relationship("Vendor", backref="bills")
    purchase_order = relationship("PurchaseOrder", foreign_keys=[po_id])
    lines = relationship(
        "BillLine",
        back_populates="bill",
        cascade="all, delete-orphan",
        order_by="BillLine.line_order",
    )
    transaction = relationship("Transaction", foreign_keys=[transaction_id])


class BillLine(Base):
    __tablename__ = "bill_lines"

    id = Column(Integer, primary_key=True, index=True)
    bill_id = Column(
        Integer, ForeignKey("bills.id", ondelete="CASCADE"), nullable=False, index=True
    )
    item_id = Column(Integer, ForeignKey("items.id"), nullable=True)
    account_id = Column(
        Integer, ForeignKey("accounts.id"), nullable=True
    )  # expense account
    description = Column(Text, nullable=True)
    quantity = Column(Numeric(12, 4), default=1)
    rate = Column(Numeric(17, 4), default=0)  # unit price, to 4 places
    amount = Column(Numeric(15, 2), default=0)
    # Per-line job / class; NULL falls back to the transaction header
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=True)
    class_id = Column(Integer, ForeignKey("classes.id"), nullable=True)
    cost_code_id = Column(Integer, ForeignKey("cost_codes.id"), nullable=True)
    # Per-line cost type; NULL falls back to the cost code's default.
    cost_type = Column(String(20), nullable=True)
    # Construction (#553): the commitment line this bill line draws on, so
    # partial billing can tally billed-to-date per schedule-of-values line.
    purchase_order_line_id = Column(
        Integer,
        ForeignKey("purchase_order_lines.id", ondelete="SET NULL"),
        nullable=True,
    )
    function = Column(String(20), nullable=True)  # nonprofit function
    # Cost billable to the job's customer; set when it is pulled onto an invoice
    is_billable = Column(Boolean, nullable=False, default=False)
    billed_invoice_line_id = Column(
        Integer, ForeignKey("invoice_lines.id"), nullable=True
    )
    line_order = Column(Integer, default=0)

    bill = relationship("Bill", back_populates="lines")
    item = relationship("Item")
    account = relationship("Account")


class BillPayment(Base):
    __tablename__ = "bill_payments"

    id = Column(Integer, primary_key=True, index=True)
    vendor_id = Column(Integer, ForeignKey("vendors.id"), nullable=False)
    date = Column(Date, nullable=False)
    amount = Column(Numeric(15, 2), nullable=False)
    method = Column(String(50), nullable=True)
    check_number = Column(String(50), nullable=True)
    pay_from_account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)
    notes = Column(Text, nullable=True)
    # Multi-currency: payment currency + rate it settled at (GL is home)
    currency = Column(String(3), nullable=True)
    exchange_rate = Column(Numeric(18, 8), nullable=True)
    transaction_id = Column(Integer, ForeignKey("transactions.id"), nullable=True)
    is_voided = Column(Boolean, default=False, nullable=False)
    # Construction (#553): paid to a Sub whose certificate of insurance had
    # expired, by explicit override; the note says which certificate.
    coi_override = Column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    coi_override_note = Column(String(200), nullable=True)
    # An early-payment discount taken against the terms (#664): the payment
    # said allow_discount_override, and the note says who and on what.
    discount_override = Column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    discount_override_note = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    vendor = relationship("Vendor", backref="bill_payments")
    pay_from_account = relationship("Account", foreign_keys=[pay_from_account_id])
    transaction = relationship("Transaction", foreign_keys=[transaction_id])
    allocations = relationship(
        "BillPaymentAllocation",
        back_populates="bill_payment",
        cascade="all, delete-orphan",
    )


class BillPaymentAllocation(Base):
    __tablename__ = "bill_payment_allocations"

    id = Column(Integer, primary_key=True, index=True)
    bill_payment_id = Column(
        Integer, ForeignKey("bill_payments.id", ondelete="CASCADE"), nullable=False
    )
    bill_id = Column(
        Integer, ForeignKey("bills.id", ondelete="RESTRICT"), nullable=False
    )
    amount = Column(Numeric(15, 2), nullable=False)

    bill_payment = relationship("BillPayment", back_populates="allocations")
    bill = relationship("Bill", backref="payment_allocations")
