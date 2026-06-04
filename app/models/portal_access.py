# ============================================================================
# Portal access audit log.
#
# Every authenticated portal page-view writes a row here. Token-last-used
# already tells us "this employee is active," but it doesn't help with
# incident response: "did someone access employee 42's portal from an IP
# in a country we've never seen before?"
#
# Mirrors the LoginAttempt pattern — IP, UA (truncated), success flag,
# timestamp — with employee_id + path so the operator can answer "who
# touched my pay-stub page last week?"
# ============================================================================

from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PortalAccess(Base):
    __tablename__ = "portal_accesses"

    id = Column(Integer, primary_key=True)
    # Nullable because a failed lookup (404 / 410) won't resolve an employee.
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, index=True)
    # IPv6 maxes at 45 characters; matches the LoginAttempt schema.
    ip = Column(String(45))
    user_agent = Column(String(255))
    # The URL path the employee hit, e.g. "/portal/paystubs". Capped at 200.
    path = Column(String(200))
    # False when the token / cookie was missing or invalid.
    success = Column(Boolean, default=False, index=True)
