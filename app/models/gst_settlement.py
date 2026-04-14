import enum

from sqlalchemy import Column, Integer, String, Date, DateTime, Numeric, Enum, ForeignKey, func
from sqlalchemy.orm import relationship

from app.database import Base


class GstSettlementStatus(str, enum.Enum):
    CONFIRMED = "confirmed"
    VOIDED = "voided"


class GstSettlement(Base):
    __tablename__ = "gst_settlements"

    id = Column(Integer, primary_key=True, index=True)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    settlement_date = Column(Date, nullable=False)
    net_position = Column(String(20), nullable=False)
    net_gst = Column(Numeric(12, 2), nullable=False)
    box9_adjustments = Column(Numeric(12, 2), default=0)
    box13_adjustments = Column(Numeric(12, 2), default=0)
    status = Column(Enum(GstSettlementStatus), default=GstSettlementStatus.CONFIRMED, nullable=False)
    bank_transaction_id = Column(Integer, ForeignKey("bank_transactions.id"), nullable=False, unique=True)
    transaction_id = Column(Integer, ForeignKey("transactions.id"), nullable=False, unique=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    bank_transaction = relationship("BankTransaction", foreign_keys=[bank_transaction_id])
    transaction = relationship("Transaction", foreign_keys=[transaction_id])
