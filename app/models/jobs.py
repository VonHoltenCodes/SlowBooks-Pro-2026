# ============================================================================
# Jobs — QuickBooks-style "Customer:Job" / Online "Project".
#
# A job belongs to one customer and is the unit of job costing: every posted
# line can carry a job_id, and the job-profitability reports group on it.
# The job itself holds what a contractor tracks about the engagement —
# status, type, dates, site, contract amount — and nothing financial: the
# money lives in the ledger lines that reference it.
#
# Jobs referenced by any posted line are never deleted (archive instead), so
# historical reports stay stable. Names are unique per customer, case-
# insensitively, enforced in the API rather than the schema so the check is
# consistent across SQLite collations.
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
    UniqueConstraint,
    func,
    true,
)
from sqlalchemy.orm import relationship

from app.database import Base

JOB_STATUSES = ("pending", "awarded", "in_progress", "closed", "not_awarded")

# The contract on the job (#552). A construction job can carry a contract — a
# lump sum, a GMP (guaranteed maximum price) or cost-plus — with a fee, a cap,
# contingency and retainage. NULL contract_type is today's behaviour: a job
# with a plain contract amount and no construction terms.
CONTRACT_TYPES = ("lump_sum", "gmp", "cost_plus")

# Who pays the sales tax on a construction job. "charge_owner" (and NULL) is
# sales tax on the owner's pay applications; "contractor_pays" is the
# lump-sum real-property rule (Florida and many states): the contractor is
# the consumer of the materials, so the pay applications bill no sales tax
# and material bills on the job accrue use tax (purchase_posting.use_tax_for).
SALES_TAX_TREATMENTS = ("charge_owner", "contractor_pays")

# A schedule-of-values line kind. "work" is the ordinary scope line (one per
# trade package); allowance / contingency and the four estimate markups —
# general_conditions / insurance / fee / bond — are the carried items an owner
# reads on a pay application. "change_order" is a line an approved owner
# change order appended (#554) — its value is owned by the CO, so the SOV
# grid shows it read-only.
SOV_KINDS = (
    "work",
    "allowance",
    "contingency",
    "general_conditions",
    "insurance",
    "fee",
    "bond",
    "change_order",
)


class Job(Base):
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(
        Integer, ForeignKey("customers.id"), nullable=False, index=True
    )
    name = Column(String(200), nullable=False)
    job_number = Column(String(50), nullable=True)
    status = Column(String(20), nullable=False, default="in_progress")
    job_type = Column(String(100), nullable=True)
    description = Column(Text, nullable=True)
    site_address = Column(Text, nullable=True)
    start_date = Column(Date, nullable=True)
    projected_end_date = Column(Date, nullable=True)
    end_date = Column(Date, nullable=True)
    contract_amount = Column(Numeric(15, 2), nullable=True)
    notes = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)

    # The contract on the job (#552). All nullable: a job with no contract
    # type behaves exactly as before. contract_amount (above) stays the
    # current/revised sum; original_contract_amount is the sum when the
    # contract was first set, so #554 can show original + changes = revised.
    contract_type = Column(String(12), nullable=True)
    original_contract_amount = Column(Numeric(15, 2), nullable=True)
    fee_pct = Column(Numeric(7, 4), nullable=True)
    fee_amount = Column(Numeric(15, 2), nullable=True)  # fixed fee; wins over pct
    cap_amount = Column(Numeric(15, 2), nullable=True)  # GMP max / cost-plus NTE
    contingency_amount = Column(Numeric(15, 2), nullable=True)  # owner contingency
    retainage_pct = Column(Numeric(5, 2), nullable=True)  # held per pay app (#555)
    # The default markup on owner change orders (#554), a contract term. Null
    # means no default; a CO or a CO line can still carry its own.
    co_markup_pct = Column(Numeric(7, 4), nullable=True)
    contract_date = Column(Date, nullable=True)
    sales_tax_treatment = Column(String(20), nullable=True)  # SALES_TAX_TREATMENTS
    # Sales tax charged on the job's sales, or the job exempt (#864): a
    # public owner's or nonprofit's job. Exempt, a new estimate, invoice,
    # sales receipt, pay application or change order for the job starts
    # with every line untaxed, and so does a new schedule-of-values line;
    # what is already made keeps its flags when this changes. Not the same
    # as sales_tax_treatment, which says who pays the tax on a taxed job.
    is_taxable = Column(Boolean, nullable=False, default=True, server_default=true())
    # The exemption certificate an exempt job stands on (#864): it lives and
    # dies with the job, so every exempt job takes its own. Number, state
    # (or jurisdiction) and kind are required while the job is exempt; the
    # expiry is optional. Switching the job back to charged clears them. A
    # document made exempt keeps its own copy (exempt_certificate_* on
    # estimates and invoices), so a later edit here never changes it.
    exempt_certificate_number = Column(String(50), nullable=True)
    exempt_certificate_state = Column(String(20), nullable=True)
    exempt_certificate_kind = Column(String(20), nullable=True)
    exempt_certificate_expires = Column(Date, nullable=True)
    contract_estimate_id = Column(
        Integer, ForeignKey("estimates.id", ondelete="SET NULL"), nullable=True
    )
    # The day the job went to Closed (cleared when it reopens), so a report
    # run back-dated to before it reads the job as it was then: still open.
    closed_on = Column(Date, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    customer = relationship("Customer", backref="jobs")
    sov_lines = relationship(
        "SovLine",
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="SovLine.line_no",
    )

    @property
    def full_name(self) -> str:
        """ "Customer: Job" — how QuickBooks migrants expect to see it."""
        cust = self.customer.name if self.customer else ""
        return f"{cust}: {self.name}" if cust else self.name


class SovLine(Base):
    """A line of the owner's schedule of values (#552) — what the contract
    sum is broken into for billing. #555 adds the billed/stored/retainage
    columns a pay application fills; for now a line is just a scheduled
    value with a kind."""

    __tablename__ = "sov_lines"
    __table_args__ = (UniqueConstraint("job_id", "line_no", name="uq_sov_line_no"),)

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(
        Integer, ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    line_no = Column(Integer, nullable=False)  # 1-based order
    item_no = Column(String(10), nullable=True)  # printed number, editable
    description = Column(String(200), nullable=True)
    trade_package_id = Column(
        Integer, ForeignKey("trade_packages.id", ondelete="SET NULL"), nullable=True
    )
    cost_code_id = Column(
        Integer, ForeignKey("cost_codes.id", ondelete="SET NULL"), nullable=True
    )
    kind = Column(String(24), nullable=False, default="work")
    scheduled_value = Column(Numeric(15, 2), nullable=False, default=0)
    # The income account the line's billing credits; null posts to 4050
    # Contract Revenue. is_taxable: sales tax is charged on its billing.
    revenue_account_id = Column(
        Integer, ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True
    )
    is_taxable = Column(Boolean, nullable=False, default=True, server_default=true())
    # The exemption certificate an exempt job stands on (#864): it lives and
    # dies with the job, so every exempt job takes its own. Number, state
    # (or jurisdiction) and kind are required while the job is exempt; the
    # expiry is optional. Switching the job back to charged clears them. A
    # document made exempt keeps its own copy (exempt_certificate_* on
    # estimates and invoices), so a later edit here never changes it.
    exempt_certificate_number = Column(String(50), nullable=True)
    exempt_certificate_state = Column(String(20), nullable=True)
    exempt_certificate_kind = Column(String(20), nullable=True)
    exempt_certificate_expires = Column(Date, nullable=True)
    source_estimate_id = Column(
        Integer, ForeignKey("estimates.id", ondelete="SET NULL"), nullable=True
    )
    # The owner change order that owns this line (#554), when kind ==
    # "change_order". SET NULL so removing the line's CO leaves the row's FK
    # empty rather than cascading; void removes the line itself.
    change_order_id = Column(
        Integer, ForeignKey("change_orders.id", ondelete="SET NULL"), nullable=True
    )

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    job = relationship("Job", back_populates="sov_lines")
    trade_package = relationship("TradePackage")
    cost_code = relationship("CostCode")
    revenue_account = relationship("Account")
