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
)
from sqlalchemy.orm import relationship

from app.database import Base

JOB_COST_SOURCES = ("manual", "time_entry", "allocation")
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
    hourly_rate = Column(Numeric(12, 2), nullable=False, default=0)
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
    total = Column(Numeric(12, 2), nullable=False, default=0)

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
    quantity = Column(Numeric(12, 2), nullable=False, default=1)  # hours, miles, units
    rate = Column(Numeric(12, 4), nullable=False, default=0)
    amount = Column(Numeric(12, 2), nullable=False, default=0)
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
    __table_args__ = (
        UniqueConstraint("job_id", "cost_code_id", "cost_type", name="uq_job_budget"),
    )

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=False, index=True)
    # Either a cost code, or a cost type (whole-type budget), or neither
    # (whole-job budget). Never both.
    cost_code_id = Column(Integer, ForeignKey("cost_codes.id"), nullable=True)
    cost_type = Column(String(20), nullable=True)
    amount = Column(Numeric(12, 2), nullable=False, default=0)
    revenue_amount = Column(Numeric(12, 2), nullable=False, default=0)
    source = Column(String(20), nullable=False, default="manual")
    estimate_id = Column(Integer, ForeignKey("estimates.id"), nullable=True)
    notes = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    job = relationship("Job")
    cost_code = relationship("CostCode")
