# ============================================================================
# Login attempt audit trail.
#
# Rate limiting (5/min per IP) blocks fast brute-force, but a patient attacker
# who paces requests under the limit stays invisible. Recording every login
# attempt — success or failure — gives the operator a way to notice that
# pattern after the fact.
# ============================================================================

from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Integer, String

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class LoginAttempt(Base):
    __tablename__ = "login_attempts"

    id = Column(Integer, primary_key=True)
    # Indexed because forensic queries are nearly always "show me the last N"
    # or "show me the last hour" — both want a fast scan back from now().
    created_at = Column(DateTime(timezone=True), default=_utcnow, index=True)
    # IPv6 maxes out at 45 characters (8 groups of 4 hex + 7 colons).
    ip = Column(String(45))
    # Truncated UA — full strings can be hundreds of bytes and we don't need them.
    user_agent = Column(String(255))
    # Indexed so "show me failures since X" is cheap.
    success = Column(Boolean, default=False, index=True)
