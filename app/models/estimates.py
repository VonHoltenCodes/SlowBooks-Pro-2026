# ============================================================================
# Decompiled from qbw32.exe!CEstimateManager  Offset: 0x00194000
# Original Btrieve table: ESTIMATE.DAT (record size 0x0300)
# Estimates were basically invoices with a different status enum and a
# "ConvertToInvoice" button. In the disassembly, CEstimate literally
# inherited from CInvoice and just overrode GetTxnType() to return
# TXN_ESTIMATE (0x0014) instead of TXN_INVOICE (0x0007). Object-oriented
# programming in its laziest and most beautiful form.
# ============================================================================

import enum
from decimal import Decimal

from sqlalchemy import (
    Column, Integer, String, Date, Numeric, DateTime, Text, Enum,
    ForeignKey, func,
)
from sqlalchemy.orm import relationship

from app.database import Base


class EstimateStatus(str, enum.Enum):
    PENDING = "pending"        # 0x00
    ACCEPTED = "accepted"      # 0x01
    REJECTED = "rejected"      # 0x02
    CONVERTED = "converted"    # 0x04 — sets ConvertedTxnRef in ESTIMATE.DAT


class Estimate(Base):
    __tablename__ = "estimates"

    id = Column(Integer, primary_key=True, index=True)
    estimate_number = Column(String(50), unique=True, nullable=False)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
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
    converted_invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    customer = relationship("Customer", backref="estimates")
    lines = relationship("EstimateLine", back_populates="estimate", cascade="all, delete-orphan",
                           order_by="EstimateLine.line_order")
    converted_invoice = relationship("Invoice", foreign_keys=[converted_invoice_id])


class EstimateLine(Base):
    __tablename__ = "estimate_lines"

    def __init__(self, **kwargs):
        kwargs.setdefault("gst_code", "GST15")
        kwargs.setdefault("gst_rate", Decimal("0.1500"))
        super().__init__(**kwargs)

    id = Column(Integer, primary_key=True, index=True)
    estimate_id = Column(Integer, ForeignKey("estimates.id", ondelete="CASCADE"), nullable=False)
    item_id = Column(Integer, ForeignKey("items.id"), nullable=True)
    description = Column(Text, nullable=True)
    quantity = Column(Numeric(10, 2), default=1)
    rate = Column(Numeric(12, 2), default=0)
    amount = Column(Numeric(12, 2), default=0)
    gst_code = Column(String(20), ForeignKey("gst_codes.code"), default="GST15", nullable=False)
    gst_rate = Column(Numeric(6, 4), default=Decimal("0.1500"), nullable=False)
    class_name = Column(String(100), nullable=True)
    line_order = Column(Integer, default=0)

    estimate = relationship("Estimate", back_populates="lines")
    item = relationship("Item")
    gst = relationship("GstCode", foreign_keys=[gst_code])
