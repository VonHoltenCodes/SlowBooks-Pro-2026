# ============================================================================
# A nod to qbw32.exe!CReceivePayment  imagined offset: 0x001A2100
# Original Btrieve table: RCVPMT.DAT + RCVPMT_ALLOC.DAT
# The payment allocation system must have been one of the more tangled
# parts of the original — picture a custom linked-list ("CQBAllocList")
# tracking which invoices a single payment covered, with a hard limit of
# 100 allocations per payment in CQBAllocList::AddAlloc. (Invented lore,
# but you believe it, don't you.)
# ============================================================================

from sqlalchemy import (
    Column,
    Integer,
    String,
    Date,
    Numeric,
    DateTime,
    Text,
    Boolean,
    ForeignKey,
    func,
)
from sqlalchemy.orm import relationship

from app.database import Base


class Payment(Base):
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(
        Integer, ForeignKey("customers.id"), nullable=False, index=True
    )
    date = Column(Date, nullable=False, index=True)
    amount = Column(Numeric(12, 2), nullable=False)
    method = Column(String(50), nullable=True)  # check, cash, credit_card, etc.
    check_number = Column(String(50), nullable=True)
    reference = Column(String(100), nullable=True)
    deposit_to_account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)
    notes = Column(Text, nullable=True)
    transaction_id = Column(Integer, ForeignKey("transactions.id"), nullable=True)
    is_voided = Column(Boolean, default=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    customer = relationship("Customer", backref="payments")
    deposit_to_account = relationship("Account", foreign_keys=[deposit_to_account_id])
    transaction = relationship("Transaction", foreign_keys=[transaction_id])
    allocations = relationship(
        "PaymentAllocation", back_populates="payment", cascade="all, delete-orphan"
    )


class PaymentAllocation(Base):
    __tablename__ = "payment_allocations"

    id = Column(Integer, primary_key=True, index=True)
    payment_id = Column(
        Integer, ForeignKey("payments.id", ondelete="CASCADE"), nullable=False
    )
    invoice_id = Column(
        Integer, ForeignKey("invoices.id", ondelete="RESTRICT"), nullable=False
    )
    amount = Column(Numeric(12, 2), nullable=False)

    payment = relationship("Payment", back_populates="allocations")
    invoice = relationship("Invoice", backref="payment_allocations")
