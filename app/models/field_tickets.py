# ============================================================================
# Field tickets — the paper that comes back from the job site (construction
# mode). Three kinds in one table, numbered per job and kind:
#
#   tm        a T&M ticket: labor, equipment and material for extra work, at
#             cost and bill rates, signed by the owner's rep. It posts no
#             cost (time entries, job-cost entries and bills already do); it
#             becomes a change order or invoice lines.
#   material  a material slip: stock taken from the contractor's own
#             inventory onto the job (negative quantities return it). Posting
#             writes a Job Cost Entry at the item's average cost.
#   delivery  a delivery ticket against an open purchase order: the quantity
#             received per PO line.
#
# Status: draft → signed → closed (billed, posted, or its change order
# approved), and void. See app/services/field_tickets.py and
# docs/features/field-tickets/PLAN.md.
# ============================================================================

from sqlalchemy import (
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import relationship

from app.database import Base

FT_KINDS = ("tm", "material", "delivery")
FT_STATUSES = ("draft", "signed", "closed", "void")
FT_LINE_TYPES = ("labor", "equipment", "material", "other")


class FieldTicket(Base):
    __tablename__ = "field_tickets"

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(
        Integer, ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind = Column(String(10), nullable=False)  # tm | material | delivery
    # <job number>-TM-001 / -MS-001 / -DT-001, each job and kind counting its own
    number = Column(String(80), unique=True, nullable=False)
    date = Column(Date, nullable=False)
    status = Column(String(10), nullable=False, default="draft")
    # The default cost code for its lines
    cost_code_id = Column(
        Integer, ForeignKey("cost_codes.id", ondelete="SET NULL"), nullable=True
    )
    description = Column(Text, nullable=True)
    submitted_by = Column(String(120), nullable=True)
    # v1 signature: the signer's typed name and the date, with a photo of the
    # signed paper as an attachment (entity_type "field_ticket")
    signed_by_name = Column(String(120), nullable=True)
    signed_at = Column(Date, nullable=True)

    # A sub's T&M ticket names the commitment it is priced against
    commitment_id = Column(
        Integer, ForeignKey("purchase_orders.id", ondelete="SET NULL"), nullable=True
    )
    # A delivery ticket's purchase order, and the supplier's own ticket number
    purchase_order_id = Column(
        Integer, ForeignKey("purchase_orders.id", ondelete="SET NULL"), nullable=True
    )
    vendor_ticket_ref = Column(String(60), nullable=True)

    # What it turned into
    change_order_id = Column(
        Integer, ForeignKey("change_orders.id", ondelete="SET NULL"), nullable=True
    )
    sub_change_order_id = Column(
        Integer, ForeignKey("change_orders.id", ondelete="SET NULL"), nullable=True
    )
    invoice_id = Column(
        Integer, ForeignKey("invoices.id", ondelete="SET NULL"), nullable=True
    )
    job_cost_id = Column(
        Integer, ForeignKey("job_costs.id", ondelete="SET NULL"), nullable=True
    )

    voided_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    job = relationship("Job")
    cost_code = relationship("CostCode")
    commitment = relationship("PurchaseOrder", foreign_keys=[commitment_id])
    purchase_order = relationship("PurchaseOrder", foreign_keys=[purchase_order_id])
    change_order = relationship("ChangeOrder", foreign_keys=[change_order_id])
    sub_change_order = relationship("ChangeOrder", foreign_keys=[sub_change_order_id])
    invoice = relationship("Invoice")
    job_cost = relationship("JobCost")
    lines = relationship(
        "FieldTicketLine",
        back_populates="ticket",
        cascade="all, delete-orphan",
        order_by="FieldTicketLine.line_order",
    )


class FieldTicketLine(Base):
    """One line of a ticket: labor hours by person, equipment hours, material
    or other, with a cost rate (and, on a T&M ticket, a bill rate). Amounts
    are quantity × rate, computed by the service, never stored."""

    __tablename__ = "field_ticket_lines"

    id = Column(Integer, primary_key=True, index=True)
    field_ticket_id = Column(
        Integer,
        ForeignKey("field_tickets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    line_order = Column(Integer, nullable=False, default=0)
    line_type = Column(String(10), nullable=False, default="other")
    employee_id = Column(
        Integer, ForeignKey("employees.id", ondelete="SET NULL"), nullable=True
    )
    equipment_id = Column(
        Integer, ForeignKey("equipment.id", ondelete="SET NULL"), nullable=True
    )
    item_id = Column(
        Integer, ForeignKey("items.id", ondelete="SET NULL"), nullable=True
    )
    # A delivery ticket's line names the PO line it received against
    po_line_id = Column(
        Integer,
        ForeignKey("purchase_order_lines.id", ondelete="SET NULL"),
        nullable=True,
    )
    description = Column(Text, nullable=True)
    quantity = Column(Numeric(15, 4), nullable=False, default=0)
    unit = Column(String(20), nullable=True)
    cost_rate = Column(Numeric(15, 4), nullable=False, default=0)
    bill_rate = Column(Numeric(15, 4), nullable=True)  # T&M only
    cost_code_id = Column(
        Integer, ForeignKey("cost_codes.id", ondelete="SET NULL"), nullable=True
    )
    cost_type = Column(String(20), nullable=True)

    ticket = relationship("FieldTicket", back_populates="lines")
    employee = relationship("Employee")
    equipment = relationship("Equipment")
    item = relationship("Item")
    po_line = relationship("PurchaseOrderLine")
    cost_code = relationship("CostCode")
