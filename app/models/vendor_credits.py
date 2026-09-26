# ============================================================================
# Vendor Credits — the AP-side counterpart of a customer credit memo
#
# Issue #129 (CimarronSiteServices): a supplier issuing a credit for returned
# or short-shipped materials had nowhere correct to go. Bills and expenses
# both reject a negative amount and both point the operator at credit memos,
# which only accept a customer. The remaining option was a manual journal
# entry against Accounts Payable, which moves the general ledger but not the
# vendor sub-ledger — so A/P aging and the vendor's balance would disagree
# with account 2000. That is the same split #119 was about, and it is why
# this is a document rather than a shortcut.
#
# A bill posts DR Expense / CR A/P. A vendor credit is its mirror:
# DR A/P / CR Expense. Applying one to a bill moves no money and posts
# nothing — the credit already reduced A/P when it was issued; applying it
# only decides WHICH bill it settles, exactly as a credit memo does on the
# receivable side.
# ============================================================================

import enum

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


class VendorCreditStatus(str, enum.Enum):
    DRAFT = "draft"
    ISSUED = "issued"
    APPLIED = "applied"
    VOID = "void"


class VendorCredit(Base):
    __tablename__ = "vendor_credits"

    id = Column(Integer, primary_key=True, index=True)
    credit_number = Column(String(50), unique=True, nullable=False)
    vendor_id = Column(Integer, ForeignKey("vendors.id"), nullable=False, index=True)
    status = Column(Enum(VendorCreditStatus), default=VendorCreditStatus.DRAFT)
    # The bill this credit came from, when the vendor named one. Advisory:
    # a credit can be applied to any of the vendor's open bills.
    original_bill_id = Column(Integer, ForeignKey("bills.id"), nullable=True)
    # The vendor's own credit-note number, when they issued one.
    ref_number = Column(String(100), nullable=True)

    date = Column(Date, nullable=False)
    subtotal = Column(Numeric(15, 2), default=0)
    tax_rate = Column(Numeric(5, 4), default=0)
    tax_amount = Column(Numeric(15, 2), default=0)
    total = Column(Numeric(15, 2), default=0)
    amount_applied = Column(Numeric(15, 2), default=0)
    balance_remaining = Column(Numeric(15, 2), default=0)

    notes = Column(Text, nullable=True)
    transaction_id = Column(Integer, ForeignKey("transactions.id"), nullable=True)

    # Class tracking dimension (QB-style); NULL groups with Uncategorized
    class_id = Column(Integer, ForeignKey("classes.id"), nullable=True)
    # Job-costing dimension (QB "Customer:Job"); NULL = no job
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    vendor = relationship("Vendor", backref="vendor_credits")
    original_bill = relationship("Bill", foreign_keys=[original_bill_id])
    lines = relationship(
        "VendorCreditLine",
        back_populates="vendor_credit",
        cascade="all, delete-orphan",
        order_by="VendorCreditLine.line_order",
    )
    transaction = relationship("Transaction", foreign_keys=[transaction_id])
    applications = relationship(
        "VendorCreditApplication",
        back_populates="vendor_credit",
        cascade="all, delete-orphan",
    )


class VendorCreditLine(Base):
    __tablename__ = "vendor_credit_lines"

    id = Column(Integer, primary_key=True, index=True)
    vendor_credit_id = Column(
        Integer,
        ForeignKey("vendor_credits.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    item_id = Column(Integer, ForeignKey("items.id"), nullable=True)
    # The expense account being credited back. Mirrors BillLine.account_id —
    # a credit memo line takes its account from the item's INCOME account
    # because an invoice does; this takes it from the expense side because
    # a bill does.
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)
    description = Column(Text, nullable=True)
    quantity = Column(Numeric(10, 2), default=1)
    rate = Column(Numeric(17, 4), default=0)  # unit price, to 4 places
    amount = Column(Numeric(15, 2), default=0)
    # Per-line job / class; NULL falls back to the header
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=True)
    class_id = Column(Integer, ForeignKey("classes.id"), nullable=True)
    cost_code_id = Column(Integer, ForeignKey("cost_codes.id"), nullable=True)
    line_order = Column(Integer, default=0)

    vendor_credit = relationship("VendorCredit", back_populates="lines")
    item = relationship("Item")
    account = relationship("Account")


class VendorCreditApplication(Base):
    __tablename__ = "vendor_credit_applications"

    id = Column(Integer, primary_key=True, index=True)
    vendor_credit_id = Column(
        Integer,
        ForeignKey("vendor_credits.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    bill_id = Column(Integer, ForeignKey("bills.id"), nullable=False, index=True)
    amount = Column(Numeric(15, 2), nullable=False)

    vendor_credit = relationship("VendorCredit", back_populates="applications")
    bill = relationship("Bill", backref="vendor_credit_applications")
