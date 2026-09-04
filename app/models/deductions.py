# ============================================================================
# Garnishment orders — CCPA limits and multi-order priority.
# Voluntary deductions and benefits moved to the benefits engine
# (app/models/benefits.py): a benefit is a code with a rule, applied in
# sequence, with effective-dated rates and posted-run snapshots.
# ============================================================================

import enum

from sqlalchemy import (
    Column,
    Integer,
    String,
    Numeric,
    DateTime,
    Enum,
    Boolean,
    ForeignKey,
    func,
)
from sqlalchemy.orm import relationship

from app.database import Base


class GarnishmentType(str, enum.Enum):
    CHILD_SUPPORT = "child_support"
    FEDERAL_LEVY = "federal_levy"
    STATE_TAX_LEVY = "state_tax_levy"
    STUDENT_LOAN = "student_loan"
    BANKRUPTCY = "bankruptcy"
    CREDITOR = "creditor"


class GarnishmentMethod(str, enum.Enum):
    FIXED = "fixed"
    PERCENT_DISPOSABLE = "percent_disposable"


class GarnishmentOrder(Base):
    __tablename__ = "garnishment_orders"

    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(
        Integer, ForeignKey("employees.id"), nullable=False, index=True
    )
    garnishment_type = Column(Enum(GarnishmentType), default=GarnishmentType.CREDITOR)
    calc_method = Column(Enum(GarnishmentMethod), default=GarnishmentMethod.FIXED)
    amount = Column(
        Numeric(12, 2), default=0
    )  # dollars (fixed) or percent (percent_disposable)

    priority = Column(Integer, default=0)
    case_number = Column(String(80), nullable=True)
    # Child-support CCPA modifiers.
    supports_secondary_family = Column(Boolean, default=False)
    in_arrears_12_weeks = Column(Boolean, default=False)

    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    employee = relationship("Employee")
