# ============================================================================
# Budget vs Actual — monthly budget amounts per account
# Phase 10: Quick Wins + Medium Effort Features
# ============================================================================

from sqlalchemy import Column, Integer, Numeric, ForeignKey, DateTime, UniqueConstraint, func

from app.database import Base


class Budget(Base):
    __tablename__ = "budgets"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    year = Column(Integer, nullable=False)
    month = Column(Integer, nullable=False)
    amount = Column(Numeric(12, 2), default=0)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("account_id", "year", "month", name="uq_budget_account_year_month"),
    )
