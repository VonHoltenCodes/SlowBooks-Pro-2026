# ============================================================================
# Email Templates — customizable email templates stored in database
# Phase 10: Quick Wins + Medium Effort Features
# ============================================================================

from sqlalchemy import Column, Integer, String, Text, DateTime, func

from app.database import Base


class EmailTemplate(Base):
    __tablename__ = "email_templates"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, nullable=False)
    subject_template = Column(String(500), nullable=False)
    body_template = Column(Text, nullable=False)
    template_type = Column(String(50), nullable=False)  # invoice, payment_receipt, past_due, collection

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
