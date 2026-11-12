# ============================================================================
# Estimates — header + line items; convertible to invoices.
# Estimates are basically invoices with a different status enum and a
# "Convert to Invoice" button.
# ============================================================================

import enum
from datetime import datetime, timezone

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
    event,
    false,
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

    subtotal = Column(Numeric(15, 2), default=0)
    tax_rate = Column(Numeric(7, 6), default=0)  # a fraction: 8.875% is 0.08875
    tax_amount = Column(Numeric(15, 2), default=0)
    total = Column(Numeric(15, 2), default=0)

    # Construction markups (#551), as percent values — 5.5 means 5.5%. They
    # compound in order on the base cost: GC, then insurance, then fee, then
    # bond. Zero on an ordinary estimate, so the bid equals the base cost.
    gc_pct = Column(Numeric(7, 4), nullable=False, default=0)
    insurance_pct = Column(Numeric(7, 4), nullable=False, default=0)
    fee_pct = Column(Numeric(7, 4), nullable=False, default=0)
    bond_pct = Column(Numeric(7, 4), nullable=False, default=0)
    # The GC decides whether the markups also build on allowance lines; off by
    # default (an allowance is a budgeted figure, not priced scope).
    markup_allowances = Column(Boolean, nullable=False, default=False)
    # "Show markup rows" on the owner's PDF (default on, the look it always
    # had). Off, the PDF spreads the markups into each package's price and
    # prints one subtotal; the stored math is untouched.
    show_markup_rows = Column(Boolean, nullable=False, default=True)
    # The estimate's terms / inclusions / exclusions text, printed above the
    # acceptance block. A new construction estimate starts with the company's
    # default (Settings); NULL on an older or non-construction estimate.
    terms = Column(Text, nullable=True)
    # When the estimate was accepted and its job budget locked (#551).
    accepted_at = Column(DateTime(timezone=True), nullable=True)
    # When the bid was decided, won or lost (#795): set by the status change
    # itself (see _stamp_decision), read by the Estimate Closing Rate report.
    decided_at = Column(DateTime(timezone=True), nullable=True)

    notes = Column(Text, nullable=True)
    converted_invoice_id = Column(
        Integer, ForeignKey("invoices.id", ondelete="SET NULL"), nullable=True
    )

    # Class tracking dimension (QB-style); NULL groups with Uncategorized
    class_id = Column(Integer, ForeignKey("classes.id"), nullable=True)
    # Job-costing dimension (QB "Customer:Job"); NULL = no job
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=True)
    # The document's "Tax exempt" switch (#864): on, no line is taxed and the
    # PDF says so under the totals. A new one starts as its job is, else its
    # customer (services/tax_exemption.default_tax_exempt).
    tax_exempt = Column(Boolean, nullable=False, server_default=false())

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
    job = relationship("Job", foreign_keys=[job_id])


@event.listens_for(Estimate, "before_insert")
def _default_tax_exempt(_mapper, connection, target):
    """A new estimate made without saying (an import, a copy) starts exempt
    as its job or customer is (#864); see tax_exemption.fill_tax_exempt."""
    from app.services.tax_exemption import fill_tax_exempt

    fill_tax_exempt(connection, target)


WON_STATUSES = (EstimateStatus.ACCEPTED, EstimateStatus.CONVERTED)


def estimate_outcome(status):
    """'won', 'lost' or None (still open) for a status."""
    if status in WON_STATUSES:
        return "won"
    if status == EstimateStatus.REJECTED:
        return "lost"
    return None


@event.listens_for(Estimate.status, "set")
def _stamp_decision(target, value, oldvalue, initiator):
    """Every path that moves an estimate's status (accept, mark lost,
    convert, a PUT, an import) dates the decision here. Accepted then
    converted is one win and keeps its first date; back to pending clears it;
    won to lost, or lost to won, is a new decision."""
    new = estimate_outcome(value)
    if new is None:
        target.decided_at = None
    elif target.decided_at is None or estimate_outcome(oldvalue) != new:
        target.decided_at = datetime.now(timezone.utc)


class EstimateLine(Base):
    __tablename__ = "estimate_lines"

    id = Column(Integer, primary_key=True, index=True)
    estimate_id = Column(
        Integer, ForeignKey("estimates.id", ondelete="CASCADE"), nullable=False
    )
    item_id = Column(Integer, ForeignKey("items.id"), nullable=True)
    description = Column(Text, nullable=True)
    quantity = Column(Numeric(12, 4), default=1)
    # Unit price to four places: bulk goods are priced like $0.045 a box.
    # The line amount is still rounded to the cent (accounting._q).
    rate = Column(Numeric(17, 4), default=0)
    amount = Column(Numeric(15, 2), default=0)
    class_name = Column(String(100), nullable=True)
    # Per-line job; NULL falls back to the document header
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=True)
    cost_code_id = Column(Integer, ForeignKey("cost_codes.id"), nullable=True)
    # Per-line cost type; NULL falls back to the cost code's default.
    cost_type = Column(String(20), nullable=True)
    unit_cost = Column(Numeric(12, 4), nullable=True)  # cost side; rate is revenue
    # A construction line's own markup on its unit cost, a percent (25 means
    # 25%): rate = unit cost × (1 + markup_pct / 100) (#774). NULL is a line
    # with no markup of its own, priced at cost as a commercial bid's is.
    markup_pct = Column(Numeric(9, 4), nullable=True)
    # Construction bid shape (#551). The bid package this line belongs to
    # (NULL groups under "Unassigned"); the line kind — a plain base line, an
    # allowance, an add/deduct alternate, or a unit price (a rate with no
    # amount in the bid); the unit of measure (SF, LF, EA, CY…); and whether
    # an alternate was accepted into the scope. Blank/base on an ordinary
    # estimate, so nothing changes for a non-construction company.
    trade_package_id = Column(
        Integer, ForeignKey("trade_packages.id", ondelete="SET NULL"), nullable=True
    )
    line_kind = Column(String(12), nullable=False, default="base")
    unit = Column(String(20), nullable=True)
    alternate_accepted = Column(Boolean, nullable=False, default=False)
    # Per-line sales tax (default: the item's flag, or taxable). A customer-
    # owned-device repair is labor with no tax; the part on the same invoice
    # is taxed.
    is_taxable = Column(Boolean, nullable=False, default=True)
    line_order = Column(Integer, default=0)

    estimate = relationship("Estimate", back_populates="lines")
    item = relationship("Item")
    cost_code = relationship("CostCode")
    trade_package = relationship("TradePackage")
