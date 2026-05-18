# ============================================================================
# Time tracking — daily time entries that feed pay runs
# Tier 1.4: regular / overtime / doubletime capture with an approval workflow.
# ============================================================================

import enum

from sqlalchemy import (
    Column,
    Integer,
    String,
    Date,
    Numeric,
    DateTime,
    Text,
    Enum,
    ForeignKey,
    func,
)
from sqlalchemy.orm import relationship

from app.database import Base


class TimeEntryStatus(str, enum.Enum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    APPROVED = "approved"
    REJECTED = "rejected"


class TimeEntry(Base):
    __tablename__ = "time_entries"

    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(
        Integer, ForeignKey("employees.id"), nullable=False, index=True
    )
    date = Column(Date, nullable=False, index=True)

    hours_regular = Column(Numeric(10, 2), default=0)
    hours_overtime = Column(Numeric(10, 2), default=0)
    hours_doubletime = Column(Numeric(10, 2), default=0)

    # Optional job-costing link. Items double as the project list in this app.
    project_id = Column(Integer, ForeignKey("items.id"), nullable=True)
    notes = Column(Text, nullable=True)

    status = Column(Enum(TimeEntryStatus), default=TimeEntryStatus.DRAFT)
    approved_by = Column(String(200), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)

    # Set once the entry has been rolled into a processed pay run, so it is
    # never double-paid.
    pay_run_id = Column(Integer, ForeignKey("pay_runs.id"), nullable=True, index=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    employee = relationship("Employee")
