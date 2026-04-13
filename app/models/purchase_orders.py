# ============================================================================
# Purchase Orders — non-posting documents to vendors, convertible to bills
# Feature 6: Structurally similar to Estimates but vendor-facing
# ============================================================================

import enum
from decimal import Decimal

from sqlalchemy import (
    Column, Integer, String, Date, Numeric, DateTime, Text, Enum,
    ForeignKey, func,
)
from sqlalchemy.orm import relationship

from app.database import Base


class POStatus(str, enum.Enum):
    DRAFT = "draft"
    SENT = "sent"
    PARTIAL = "partial"
    RECEIVED = "received"
    CLOSED = "closed"


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"

    id = Column(Integer, primary_key=True, index=True)
    po_number = Column(String(50), unique=True, nullable=False)
    vendor_id = Column(Integer, ForeignKey("vendors.id"), nullable=False)
    status = Column(Enum(POStatus), default=POStatus.DRAFT)

    date = Column(Date, nullable=False)
    expected_date = Column(Date, nullable=True)
    ship_to = Column(Text, nullable=True)

    subtotal = Column(Numeric(12, 2), default=0)
    tax_rate = Column(Numeric(5, 4), default=0)
    tax_amount = Column(Numeric(12, 2), default=0)
    total = Column(Numeric(12, 2), default=0)

    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    vendor = relationship("Vendor", backref="purchase_orders")
    lines = relationship("PurchaseOrderLine", back_populates="purchase_order",
                         cascade="all, delete-orphan", order_by="PurchaseOrderLine.line_order")


class PurchaseOrderLine(Base):
    __tablename__ = "purchase_order_lines"

    def __init__(self, **kwargs):
        kwargs.setdefault("gst_code", "GST15")
        kwargs.setdefault("gst_rate", Decimal("0.1500"))
        super().__init__(**kwargs)

    id = Column(Integer, primary_key=True, index=True)
    purchase_order_id = Column(Integer, ForeignKey("purchase_orders.id", ondelete="CASCADE"), nullable=False)
    item_id = Column(Integer, ForeignKey("items.id"), nullable=True)
    description = Column(Text, nullable=True)
    quantity = Column(Numeric(10, 2), default=1)
    rate = Column(Numeric(12, 2), default=0)
    amount = Column(Numeric(12, 2), default=0)
    gst_code = Column(String(20), ForeignKey("gst_codes.code"), default="GST15", nullable=False)
    gst_rate = Column(Numeric(6, 4), default=Decimal("0.1500"), nullable=False)
    received_qty = Column(Numeric(10, 2), default=0)
    line_order = Column(Integer, default=0)

    purchase_order = relationship("PurchaseOrder", back_populates="lines")
    item = relationship("Item")
    gst = relationship("GstCode", foreign_keys=[gst_code])
