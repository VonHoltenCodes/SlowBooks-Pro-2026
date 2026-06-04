# ============================================================================
# Attachments — file uploads linked to any entity (invoices, bills, etc.)
# Phase 10: Quick Wins + Medium Effort Features
# Tier 3: doubles as the per-employee HR document vault via employee_id.
# ============================================================================

from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, func

from app.database import Base


class Attachment(Base):
    __tablename__ = "attachments"

    id = Column(Integer, primary_key=True, index=True)
    entity_type = Column(String(50), nullable=False)
    entity_id = Column(Integer, nullable=False)
    filename = Column(String(255), nullable=False)
    file_path = Column(String(500), nullable=False)
    mime_type = Column(String(100), nullable=True)
    file_size = Column(Integer, nullable=True)

    # Per-employee HR document vault (Tier 3). When set, the attachment is an
    # employee document; doc_category classifies it (w4, i9, offer_letter...).
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=True, index=True)
    doc_category = Column(String(50), nullable=True)

    uploaded_at = Column(DateTime(timezone=True), server_default=func.now())
