# ============================================================================
# Recurring Invoices — schedule automatic invoice generation
# Feature 2: Weekly/monthly/quarterly/yearly recurring invoice templates
# ============================================================================

from decimal import Decimal

from sqlalchemy import (
    Column, Integer, String, Date, Numeric, DateTime, Boolean, Text,
    ForeignKey, func,
)
from sqlalchemy.orm import relationship

from app.database import Base


class RecurringInvoice(Base):
    __tablename__ = "recurring_invoices"

    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    frequency = Column(String(20), nullable=False)  # weekly, monthly, quarterly, yearly
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=True)
    next_due = Column(Date, nullable=False)
    is_active = Column(Boolean, default=True)

    terms = Column(String(50), default="Net 30")
    tax_rate = Column(Numeric(5, 4), default=0)
    notes = Column(Text, nullable=True)
    invoices_created = Column(Integer, default=0)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    customer = relationship("Customer", backref="recurring_invoices")
    lines = relationship("RecurringInvoiceLine", back_populates="recurring_invoice",
                         cascade="all, delete-orphan", order_by="RecurringInvoiceLine.line_order")


class RecurringInvoiceLine(Base):
    __tablename__ = "recurring_invoice_lines"

    def __init__(self, **kwargs):
        kwargs.setdefault("gst_code", "GST15")
        kwargs.setdefault("gst_rate", Decimal("0.1500"))
        super().__init__(**kwargs)

    id = Column(Integer, primary_key=True, index=True)
    recurring_invoice_id = Column(Integer, ForeignKey("recurring_invoices.id", ondelete="CASCADE"), nullable=False)
    item_id = Column(Integer, ForeignKey("items.id"), nullable=True)
    description = Column(Text, nullable=True)
    quantity = Column(Numeric(10, 2), default=1)
    rate = Column(Numeric(12, 2), default=0)
    gst_code = Column(String(20), ForeignKey("gst_codes.code"), default="GST15", nullable=False)
    gst_rate = Column(Numeric(6, 4), default=Decimal("0.1500"), nullable=False)
    line_order = Column(Integer, default=0)

    recurring_invoice = relationship("RecurringInvoice", back_populates="lines")
    item = relationship("Item")
    gst = relationship("GstCode", foreign_keys=[gst_code])
