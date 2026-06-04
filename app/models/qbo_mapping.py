# ============================================================================
# QBO Entity Mapping — tracks QuickBooks Online ID <-> Slowbooks ID
#
# When importing from or exporting to QBO, we need to know which Slowbooks
# entity corresponds to which QBO entity. This table provides that mapping
# plus the QBO SyncToken (required for updates) and last sync timestamp.
# ============================================================================

from sqlalchemy import Column, Integer, String, DateTime, func

from app.database import Base


class QBOMapping(Base):
    __tablename__ = "qbo_mappings"

    id = Column(Integer, primary_key=True, index=True)
    entity_type = Column(
        String(50), nullable=False
    )  # account, customer, vendor, item, invoice, payment
    slowbooks_id = Column(Integer, nullable=False)
    qbo_id = Column(String(100), nullable=False)
    qbo_sync_token = Column(String(50), nullable=True)
    last_synced_at = Column(DateTime(timezone=True), server_default=func.now())
