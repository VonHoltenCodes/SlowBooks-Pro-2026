# ============================================================================
# Phase 11: Saved report parameter sets.
#
# A SavedReport is just (report_type, name, parameters-as-JSON). When the
# user opens it, the SPA re-runs the appropriate /api/reports endpoint with
# the stored params. Nothing is cached — reports stay live.
# ============================================================================

from sqlalchemy import Column, Integer, String, DateTime, JSON, func

from app.database import Base


class SavedReport(Base):
    __tablename__ = "saved_reports"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    # e.g. "profit_loss", "balance_sheet", "ar_aging", "account_transactions"
    report_type = Column(String(50), nullable=False, index=True)
    # Free-form JSON blob: {"start_date": "2026-01-01", "account_id": 5, ...}
    parameters = Column(JSON, nullable=False, default=dict)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
