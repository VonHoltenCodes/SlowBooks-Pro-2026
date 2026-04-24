# ============================================================================
# Attachments — file uploads linked to any entity (invoices, bills, etc.)
# Phase 10: Quick Wins + Medium Effort Features
# ============================================================================

from sqlalchemy import Column, Integer, String, DateTime, func

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

    uploaded_at = Column(DateTime(timezone=True), server_default=func.now())
