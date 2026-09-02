# ============================================================================
# Merchant OCR templates — v3 of the receipt intake plan.
# One row per merchant: where its receipt fields live, learned from operator
# corrections on the box-to-fix canvas. Anchor-relative encodings only
# (see app/services/ocr_templates.py); never absolute pixels.
# ============================================================================

from sqlalchemy import Column, DateTime, Integer, String, Text, func

from app.database import Base


class OcrTemplate(Base):
    __tablename__ = "ocr_templates"

    id = Column(Integer, primary_key=True, index=True)
    # Normalized merchant key (services.ocr_templates.merchant_key)
    merchant_key = Column(String(120), unique=True, nullable=False, index=True)
    # As-scanned merchant display name (for the UI)
    merchant_name = Column(String(200), nullable=True)
    # JSON: {field_key: {anchor_text, dx, dy, w, h}}
    fields_json = Column(Text, nullable=False, default="{}")
    use_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
