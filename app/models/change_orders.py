# ============================================================================
# Change orders (#554) — how a locked construction budget, its contract and
# its commitments move, with an audit trail.
#
# A change order is one of three kinds:
#   - pco    — a potential change order (pricing exercise, promotes to an owner
#              CO when the owner says yes);
#   - owner  — the owner change order that revises the contract and the budget;
#   - sub    — a sub change order that revises a commitment (a purchase order).
#
# Material and labor are priced separately, each carrying its own markup —
# except allowance- and contingency-funded work, which carries none unless the
# change order turns markup on. Every effect on the
# contract, the SOV, the budget and the commitment happens on approve, in one
# transaction, and reverses on void. See app/services/change_orders.py.
# ============================================================================

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    false,
    func,
)
from sqlalchemy.orm import relationship

from app.database import Base

# The three kinds (see the module docstring).
CO_KINDS = ("pco", "owner", "sub")

# Lifecycle. "promoted" is only ever a PCO's — the state it lands in once it
# has been promoted to an owner CO.
# "superseded" is only ever a PCO's — replaced by a newer revision.
CO_STATUSES = (
    "draft",
    "pending",
    "approved",
    "rejected",
    "void",
    "promoted",
    "superseded",
)

# What pays for an owner change order. "contract" adds to the contract sum;
# "contingency" draws the owner's contingency down; "allowance" spends an
# allowance SOV line. Markup applies to contract-funded work always; on an
# allowance- or contingency-funded change only when apply_markup is on (off by
# default).
CO_FUNDING = ("contract", "allowance", "contingency")


class ChangeOrder(Base):
    __tablename__ = "change_orders"

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(
        Integer, ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Per job and kind: <job number>-CO-001 / -PCO-001 / -SCO-001 (a PCO
    # revision adds -R2); see numbering.next_change_order_number. Older rows
    # keep their company-wide CO-0001 numbers.
    number = Column(String(80), unique=True, nullable=False)
    kind = Column(String(8), nullable=False)  # pco | owner | sub
    status = Column(String(10), nullable=False, default="draft")
    title = Column(String(200), nullable=True)
    description = Column(Text, nullable=True)
    date = Column(Date, nullable=True)

    # Meaningful for owner COs: what pays for the change.
    funding = Column(String(12), nullable=False, default="contract")
    # Required when funding = allowance: the SOV allowance line it spends.
    allowance_sov_line_id = Column(
        Integer, ForeignKey("sov_lines.id", ondelete="SET NULL"), nullable=True
    )
    # The CO's own default markup; null falls back to the job's co_markup_pct.
    markup_pct = Column(Numeric(7, 4), nullable=True)
    # Whether markup is applied. True for contract-funded work; defaults off
    # for allowance- and contingency-funded work (the GC may turn it on).
    apply_markup = Column(Boolean, nullable=False, default=True)

    # Required for a sub CO: the commitment (purchase order) it revises.
    commitment_id = Column(
        Integer, ForeignKey("purchase_orders.id", ondelete="SET NULL"), nullable=True
    )
    # A sub CO may be backed by an owner CO (the owner change it carries out).
    owner_change_order_id = Column(
        Integer, ForeignKey("change_orders.id", ondelete="SET NULL"), nullable=True
    )
    # An owner CO promoted from a PCO names the PCO it came from.
    source_pco_id = Column(
        Integer, ForeignKey("change_orders.id", ondelete="SET NULL"), nullable=True
    )

    # PCO revisions: revision counts up from 1; a revision names the one it
    # replaced, which is kept (status "superseded") as history.
    revision = Column(Integer, nullable=False, default=1)
    supersedes_id = Column(
        Integer, ForeignKey("change_orders.id", ondelete="SET NULL"), nullable=True
    )
    # A promoted PCO names the owner CO it rolled into (several PCOs may share
    # one); the owner CO's source_pco_id names the first.
    promoted_to_id = Column(
        Integer, ForeignKey("change_orders.id", ondelete="SET NULL"), nullable=True
    )

    # approved_at is the audit stamp of the Approve click. approved_on is the
    # day the change order takes effect: every as-of reader (WIP, the log, a
    # pay application's contract sum) reads it, not the click.
    approved_at = Column(DateTime(timezone=True), nullable=True)
    approved_on = Column(Date, nullable=True)
    # True for the owner CO that returns an allowance's unspent amount: it
    # names the allowance in allowance_sov_line_id, is contract-funded and
    # carries no markup (see apply_change_order).
    allowance_return = Column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    # True once Approve has written an allowance-funded CO's draw out of its
    # allowance's budget cell. A CO approved before that was written has it
    # false, and its void reverses its lines alone.
    allowance_draw_written = Column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    voided_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    job = relationship("Job")
    allowance_sov_line = relationship("SovLine", foreign_keys=[allowance_sov_line_id])
    commitment = relationship("PurchaseOrder")
    lines = relationship(
        "ChangeOrderLine",
        back_populates="change_order",
        cascade="all, delete-orphan",
        order_by="ChangeOrderLine.line_order",
    )


class ChangeOrderLine(Base):
    """A priced line on a change order. Cost is quantity × unit_cost (server-
    computed); sell carries the effective markup (none when the CO is funded
    by an allowance or contingency without markup turned on, and none at all on a sub CO — a commitment moves by
    cost)."""

    __tablename__ = "change_order_lines"

    id = Column(Integer, primary_key=True, index=True)
    change_order_id = Column(
        Integer,
        ForeignKey("change_orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    line_order = Column(Integer, default=0)
    description = Column(Text, nullable=True)
    # labor | material | subcontract | equipment | other (validated against
    # the cost_types table), so labor and material price separately.
    cost_type = Column(String(20), nullable=False)
    cost_code_id = Column(
        Integer, ForeignKey("cost_codes.id", ondelete="SET NULL"), nullable=True
    )
    trade_package_id = Column(
        Integer, ForeignKey("trade_packages.id", ondelete="SET NULL"), nullable=True
    )
    quantity = Column(Numeric(15, 4), default=1)
    unit = Column(String(20), nullable=True)
    unit_cost = Column(Numeric(15, 4), default=0)
    amount = Column(Numeric(15, 2), default=0)  # cost = qty × unit_cost
    markup_pct = Column(Numeric(7, 4), nullable=True)  # per-line override
    sell_amount = Column(Numeric(15, 2), default=0)  # cost × (1 + markup/100)

    change_order = relationship("ChangeOrder", back_populates="lines")
    cost_code = relationship("CostCode")
    trade_package = relationship("TradePackage")
