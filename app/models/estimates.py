# ============================================================================
# Estimates — header + line items; convertible to invoices.
# Estimates are basically invoices with a different status enum and a
# "Convert to Invoice" button.
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
    func,
)
from sqlalchemy.orm import relationship

from app.database import Base


class EstimateStatus(str, enum.Enum):
    PENDING = "pending"  # 0x00
    ACCEPTED = "accepted"  # 0x01
    REJECTED = "rejected"  # 0x02
    CONVERTED = "converted"  # 0x04 — sets ConvertedTxnRef in ESTIMATE.DAT


class Estimate(Base):
    __tablename__ = "estimates"

    id = Column(Integer, primary_key=True, index=True)
    estimate_number = Column(String(50), unique=True, nullable=False)
    customer_id = Column(
        Integer, ForeignKey("customers.id"), nullable=False, index=True
    )
    status = Column(Enum(EstimateStatus), default=EstimateStatus.PENDING)

    date = Column(Date, nullable=False)
    expiration_date = Column(Date, nullable=True)

    bill_address1 = Column(String(200), nullable=True)
    bill_address2 = Column(String(200), nullable=True)
    bill_city = Column(String(100), nullable=True)
    bill_state = Column(String(50), nullable=True)
    bill_zip = Column(String(20), nullable=True)

    subtotal = Column(Numeric(12, 2), default=0)
    tax_rate = Column(Numeric(5, 4), default=0)
    tax_amount = Column(Numeric(12, 2), default=0)
    total = Column(Numeric(12, 2), default=0)

    notes = Column(Text, nullable=True)
    converted_invoice_id = Column(
        Integer, ForeignKey("invoices.id", ondelete="SET NULL"), nullable=True
    )

    # Class tracking dimension (QB-style); NULL groups with Uncategorized
    class_id = Column(Integer, ForeignKey("classes.id"), nullable=True)
    # Job-costing dimension (QB "Customer:Job"); NULL = no job
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    customer = relationship("Customer", backref="estimates")
    lines = relationship(
        "EstimateLine",
        back_populates="estimate",
        cascade="all, delete-orphan",
        order_by="EstimateLine.line_order",
    )
    converted_invoice = relationship("Invoice", foreign_keys=[converted_invoice_id])


class EstimateLine(Base):
    __tablename__ = "estimate_lines"

    id = Column(Integer, primary_key=True, index=True)
    estimate_id = Column(
        Integer, ForeignKey("estimates.id", ondelete="CASCADE"), nullable=False
    )
    item_id = Column(Integer, ForeignKey("items.id"), nullable=True)
    description = Column(Text, nullable=True)
    quantity = Column(Numeric(10, 2), default=1)
    rate = Column(Numeric(12, 2), default=0)
    amount = Column(Numeric(12, 2), default=0)
    class_name = Column(String(100), nullable=True)
    # Per-line job; NULL falls back to the document header
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=True)
    cost_code_id = Column(Integer, ForeignKey("cost_codes.id"), nullable=True)
    unit_cost = Column(Numeric(12, 4), nullable=True)  # cost side; rate is revenue
    # Per-line sales tax (default: the item's flag, or taxable). A customer-
    # owned-device repair is labor with no tax; the part on the same invoice
    # is taxed.
    is_taxable = Column(Boolean, nullable=False, default=True)
    line_order = Column(Integer, default=0)

    estimate = relationship("Estimate", back_populates="lines")
    item = relationship("Item")
