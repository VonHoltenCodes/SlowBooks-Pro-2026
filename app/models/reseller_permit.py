# ============================================================================
# Reseller permit tracking — the thing businesses always miss.
#
# When a customer buys from you for resale (e.g. a retailer buying inventory
# from your wholesale business), they hand you their reseller permit and you
# don't charge sales tax on that line. The state expects you to:
#   1. Keep a copy of every permit you accepted
#   2. Verify it was valid AT THE TIME OF SALE
#   3. Not honor an expired permit
#
# Reseller permits expire — usually 4 years in Washington, varies by state.
# The expiry is the thing every small business forgets. This table makes the
# expiry queryable so we can:
#   - Refuse to apply a permit-based exemption if it's expired
#   - Surface "X permits expire in the next 30 days" in the dashboard
#   - Print an audit trail when a state auditor asks
#
# There is no real-time API for most states (WA has a manual lookup page;
# CA has CDTFA's lookup; both are web-form-based). Verification is the
# operator clicking a button that opens the official state page in a new
# tab with the permit number pre-filled, then marking the permit verified.
# ============================================================================

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Integer,
    String,
    Text,
    func,
)

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ResellerPermit(Base):
    __tablename__ = "reseller_permits"

    id = Column(Integer, primary_key=True)

    # Polymorphic attachment — most permits belong to a customer (their
    # permit, presented to us). Some businesses also store their own
    # permit (presented to vendors) — those use entity_type="company"
    # with entity_id=NULL.
    entity_type = Column(
        String(20), nullable=False, index=True
    )  # customer / vendor / company
    entity_id = Column(Integer, nullable=True, index=True)

    # Two-letter state code. WA, CA, OR, NY, etc. Don't validate against a
    # list — operators with multi-state customers will need flexibility,
    # and bad codes here just won't match any verification URL.
    jurisdiction = Column(String(20), nullable=False, index=True)
    permit_number = Column(String(50), nullable=False, index=True)

    # Dates as Date (not DateTime) — permits are valid for whole days.
    issued_at = Column(Date, nullable=True)
    expires_at = Column(Date, nullable=True, index=True)

    # Manual verification trail. last_verified_at is when an operator
    # clicked "I just checked this on the state lookup and it's valid"
    # button. verified_by is a free-form note (operator name, ticket #,
    # whatever they want to record).
    last_verified_at = Column(DateTime(timezone=True), nullable=True)
    verified_by = Column(String(100), nullable=True)

    notes = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True, index=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
