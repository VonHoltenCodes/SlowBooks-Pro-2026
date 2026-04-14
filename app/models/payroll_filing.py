import enum

from sqlalchemy import Column, Integer, String, DateTime, Text, Enum, ForeignKey, func
from sqlalchemy.orm import relationship

from app.database import Base


class PayrollFilingStatus(str, enum.Enum):
    GENERATED = "generated"
    FILED = "filed"
    AMENDED = "amended"
    SUPERSEDED = "superseded"


class PayrollFilingAudit(Base):
    __tablename__ = "payroll_filing_audits"

    id = Column(Integer, primary_key=True, index=True)
    filing_type = Column(String(50), nullable=False, index=True)
    status = Column(Enum(PayrollFilingStatus), default=PayrollFilingStatus.GENERATED, nullable=False)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=True)
    pay_run_id = Column(Integer, ForeignKey("pay_runs.id"), nullable=True)
    source_hash = Column(String(64), nullable=False)
    source_snapshot = Column(Text, nullable=False)
    export_filename = Column(String(255), nullable=False)
    export_reference = Column(String(255), nullable=True)
    notes = Column(Text, nullable=True)
    generated_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    status_updated_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    generated_at = Column(DateTime(timezone=True), server_default=func.now())
    status_updated_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    employee = relationship("Employee")
    pay_run = relationship("PayRun")
    generated_by_user = relationship("User", foreign_keys=[generated_by_user_id])
    status_updated_by_user = relationship("User", foreign_keys=[status_updated_by_user_id])
