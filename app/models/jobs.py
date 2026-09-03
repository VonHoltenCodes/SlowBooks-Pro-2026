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
    func,
)
from sqlalchemy.orm import relationship

from app.database import Base

JOB_STATUSES = ("pending", "awarded", "in_progress", "closed", "not_awarded")


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
    contract_amount = Column(Numeric(12, 2), nullable=True)
    notes = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    customer = relationship("Customer", backref="jobs")

    @property
    def full_name(self) -> str:
        """ "Customer: Job" — how QuickBooks migrants expect to see it."""
        cust = self.customer.name if self.customer else ""
        return f"{cust}: {self.name}" if cust else self.name
