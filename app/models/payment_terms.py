# ============================================================================
# Payment Terms — the terms list (#664), QuickBooks' shape
#
# A term is a name and a rule. A standard term is due N days after the
# document's date; a date-driven term is due on a day of the month. Either
# may offer an early-payment discount: P% if paid within D days (standard)
# or by day D of the month (date-driven). Documents keep the term's NAME in
# their own `terms` column, so free text from before the list, or from an
# import, still reads; app/services/payment_terms.py is the one parser.
# ============================================================================

from sqlalchemy import Boolean, Column, DateTime, Integer, Numeric, String, func

from app.database import Base

STANDARD = "standard"
DATE_DRIVEN = "date_driven"
KINDS = (STANDARD, DATE_DRIVEN)


class PaymentTerm(Base):
    __tablename__ = "payment_terms"

    id = Column(Integer, primary_key=True, index=True)
    # The documents' terms column is String(50); a name never outgrows it.
    name = Column(String(50), nullable=False, unique=True)
    kind = Column(String(12), nullable=False, default=STANDARD)
    # Standard: due this many days after the document's date (0 = on receipt).
    net_days = Column(Integer, nullable=True)
    # Date-driven: due on this day of the month (past a short month's end,
    # its last day), moved to the next month when the document is dated
    # fewer than cutoff_days before it.
    due_day = Column(Integer, nullable=True)
    cutoff_days = Column(Integer, nullable=False, default=0)
    # The early-payment discount, optional: the percent, and by when —
    # days after the date (standard) or a day of the due date's month
    # (date-driven).
    discount_percent = Column(Numeric(5, 2), nullable=True)
    discount_days = Column(Integer, nullable=True)
    discount_day = Column(Integer, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
