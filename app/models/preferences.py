# ============================================================================
# Per-user preferences — small JSON blobs keyed by name (dashboard layout,
# later: default report periods, column choices...).
#
# user_id is NULL for the single-password ("operator") session, so a company
# that never created users still gets one remembered layout; once users
# exist each gets their own row per key. API-token principals never write
# preferences (they have no screen).
# ============================================================================

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)

from app.database import Base


class UserPreference(Base):
    __tablename__ = "user_preferences"
    __table_args__ = (UniqueConstraint("user_id", "key", name="uq_user_preference"),)

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    key = Column(String(50), nullable=False)
    value = Column(Text, nullable=False, default="{}")  # JSON

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
