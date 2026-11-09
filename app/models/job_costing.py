# ============================================================================
# Job costing model (Projects milestone 3): the pieces that turn "a job and a
# code on a ledger line" into a cost model a contractor recognises.
#
# CostType     user-editable list (labor, material, subcontract, equipment,
#              other, and whatever else the company tracks) with a burden
#              rule and the accounts job-cost entries post through.
# Equipment    owned machines charged to jobs by the hour.
# JobCost      the "Job Cost Entry" document: any cost that is not a vendor
#              bill — internal labor at a loaded rate, equipment hours,
#              mileage, burden, overhead allocations, corrections. Posts
#              DR job cost / CR an offset (payroll clearing, applied
#              overhead...). Lines may each name a job (allocations spread
#              one amount across many jobs in one entry).
# JobBudget    budget per job × cost code (or cost type, or whole job) for
#              the budget / actual / committed / variance drill-down.
# WipEntryLock one row per date a WIP adjusting entry stands for, so two
#              posts of the same date cannot both land.
# ============================================================================

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import relationship

from app.database import Base

# material_slip: posted by a field ticket's material slip (construction)
JOB_COST_SOURCES = ("manual", "time_entry", "allocation", "material_slip")
JOB_COST_STATUSES = ("posted", "void")
ALLOCATION_METHODS = ("percent", "hours", "revenue", "costs", "equal")

# The starting set every company gets. Codes are the stable key the
# cost_codes.cost_type column and every report use; names are editable.
DEFAULT_COST_TYPES = (
    ("labor", "Labor", True, 1),
    ("material", "Material", False, 2),
    ("subcontract", "Subcontract", False, 3),
    ("equipment", "Equipment", False, 4),
    ("other", "Other", False, 5),
)


class CostType(Base):
    __tablename__ = "cost_types"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(20), nullable=False, unique=True)
    name = Column(String(100), nullable=False)
    # Burden applies to labor-like types: a percent added on top of the
    # base cost as its own line (employer taxes, benefits, insurance).
    is_labor = Column(Boolean, nullable=False, default=False)
    burden_pct = Column(Numeric(6, 2), nullable=True)
    # How labor burden reaches the job:
    #   flat     the burden_pct above, posted with each time entry
    #   payroll  actual employer taxes + job-routed benefit codes, distributed
    #            by hours when the pay run is processed (time entries post
    #            base labor only)
    burden_method = Column(String(10), nullable=False, default="flat")
    # Where a job-cost line of this type lands when the code has no account
    default_account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)
    # The credit side of a job-cost entry of this type (payroll clearing,
    # applied equipment, applied overhead)
    offset_account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)
    # The credit side of the burden line (applied labor burden)
    burden_offset_account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)
    sort_order = Column(Integer, nullable=False, default=0)
    is_active = Column(Boolean, nullable=False, default=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    default_account = relationship("Account", foreign_keys=[default_account_id])
    offset_account = relationship("Account", foreign_keys=[offset_account_id])
    burden_offset_account = relationship(
        "Account", foreign_keys=[burden_offset_account_id]
    )


class Equipment(Base):
    __tablename__ = "equipment"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(30), nullable=True)
    name = Column(String(200), nullable=False)
    hourly_rate = Column(Numeric(15, 2), nullable=False, default=0)
    cost_code_id = Column(Integer, ForeignKey("cost_codes.id"), nullable=True)
    # Credit side when hours are charged to a job (applied equipment cost)
    recovery_account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)
    notes = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    cost_code = relationship("CostCode")
    recovery_account = relationship("Account")


class JobCost(Base):
    __tablename__ = "job_costs"

    id = Column(Integer, primary_key=True, index=True)
    number = Column(String(30), nullable=False, unique=True)
    date = Column(Date, nullable=False, index=True)
    # NULL for an allocation entry whose lines each name their job
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=True, index=True)
    memo = Column(Text, nullable=True)
    source = Column(String(20), nullable=False, default="manual")
    status = Column(String(10), nullable=False, default="posted")
    transaction_id = Column(Integer, ForeignKey("transactions.id"), nullable=True)
    total = Column(Numeric(15, 2), nullable=False, default=0)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    job = relationship("Job")
    transaction = relationship("Transaction", foreign_keys=[transaction_id])
    lines = relationship(
        "JobCostLine",
        back_populates="job_cost",
        cascade="all, delete-orphan",
        order_by="JobCostLine.line_order",
    )


class JobCostLine(Base):
    __tablename__ = "job_cost_lines"

    id = Column(Integer, primary_key=True, index=True)
    job_cost_id = Column(
        Integer,
        ForeignKey("job_costs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=True)
    cost_code_id = Column(Integer, ForeignKey("cost_codes.id"), nullable=True)
    cost_type = Column(String(20), nullable=True)
    description = Column(Text, nullable=True)
    quantity = Column(Numeric(15, 2), nullable=False, default=1)  # hours, miles, units
    rate = Column(Numeric(12, 4), nullable=False, default=0)
    amount = Column(Numeric(15, 2), nullable=False, default=0)
    debit_account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    credit_account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=True)
    equipment_id = Column(Integer, ForeignKey("equipment.id"), nullable=True)
    time_entry_id = Column(Integer, ForeignKey("time_entries.id"), nullable=True)
    is_burden = Column(Boolean, nullable=False, default=False)
    is_billable = Column(Boolean, nullable=False, default=False)
    line_order = Column(Integer, nullable=False, default=0)

    job_cost = relationship("JobCost", back_populates="lines")
    job = relationship("Job")
    cost_code = relationship("CostCode")
    debit_account = relationship("Account", foreign_keys=[debit_account_id])
    credit_account = relationship("Account", foreign_keys=[credit_account_id])
    employee = relationship("Employee")
    equipment = relationship("Equipment")


class JobBudget(Base):
    __tablename__ = "job_budgets"
    # The unique key includes source (#554): a change-order row may share a
    # (job, code, type) with an estimate-locked or manual row — the tree shows
    # one under "original" and the other under "changes". Each change order
    # writes rows of its own (change_order_id), one per code and type, so the
    # (job, code, type, source) key holds only for the rows no change order
    # owns; partial indexes, which SQLite and PostgreSQL both enforce.
    __table_args__ = (
        Index(
            "uq_job_budget",
            "job_id",
            "cost_code_id",
            "cost_type",
            "source",
            unique=True,
            sqlite_where=text("change_order_id IS NULL"),
            postgresql_where=text("change_order_id IS NULL"),
        ),
        Index(
            "uq_job_budget_change_order",
            "change_order_id",
            "cost_code_id",
            "cost_type",
            unique=True,
            sqlite_where=text("change_order_id IS NOT NULL"),
            postgresql_where=text("change_order_id IS NOT NULL"),
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=False, index=True)
    # A cost code, a cost type (whole-type budget), both (a coded line
    # budgeted at a specific type), or neither (whole-job budget). The unique
    # constraint keys on (job, code, type), so both together is allowed.
    cost_code_id = Column(Integer, ForeignKey("cost_codes.id"), nullable=True)
    cost_type = Column(String(20), nullable=True)
    amount = Column(Numeric(15, 2), nullable=False, default=0)
    revenue_amount = Column(Numeric(15, 2), nullable=False, default=0)
    source = Column(String(20), nullable=False, default="manual")
    estimate_id = Column(Integer, ForeignKey("estimates.id"), nullable=True)
    # An accepted estimate locks the rows it seeded (#551): they are the
    # contract budget and only a change order moves them. Manual rows stay
    # editable.
    locked = Column(Boolean, nullable=False, default=False)
    # The change order that wrote this row (source "change"): the row is in
    # effect from the day the change order is (co_effective_date), so a
    # report as of an earlier date leaves it out. Null on a change row from
    # before the link, which counts on every date.
    change_order_id = Column(
        Integer, ForeignKey("change_orders.id"), nullable=True, index=True
    )
    notes = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    job = relationship("Job")
    cost_code = relationship("CostCode")


class JobCostToComplete(Base):
    """A user-entered estimate of the cost still to come on a job, per section
    and cost type (#556). It replaces the default (budget − actual, floored at
    zero) for that cell, and the WIP schedule reads actual + cost to complete as
    the job's estimated total cost. cost_code_id is the section (or the code
    itself when it has none); NULL with cost_type "" is the job's unassigned
    cell (budget or cost with no code). The service upserts, so one cell is one
    row even where a NULL keeps the unique key from refusing a duplicate."""

    __tablename__ = "job_cost_to_complete"
    __table_args__ = (
        UniqueConstraint(
            "job_id", "cost_code_id", "cost_type", name="uq_job_cost_to_complete"
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(
        Integer, ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    cost_code_id = Column(Integer, ForeignKey("cost_codes.id"), nullable=True)
    cost_type = Column(String(20), nullable=False, default="")
    amount = Column(Numeric(15, 2), nullable=False, default=0)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class WipEntryLock(Base):
    """The date a WIP adjusting entry stands for (wip_posting). Post writes
    the row before the entry, so of two posts of one date the second meets
    the unique key and is refused; Void deletes it so the date can be posted
    again."""

    __tablename__ = "wip_entry_locks"

    as_of = Column(Date, primary_key=True)
    transaction_id = Column(Integer, ForeignKey("transactions.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
