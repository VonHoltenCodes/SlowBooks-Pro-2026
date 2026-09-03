# ============================================================================
# Cost codes — the job-costing chart. A cost code is what a contractor
# budgets and tracks a job by ("03 Concrete", "26 Electrical", "L-100
# Rough carpentry labor"), independent of the G/L account the cost posts
# to: the account says *what kind of expense*, the cost code says *which
# part of the job*. Each code carries a cost type (labor / material /
# subcontract / equipment / other) so job cost reports can roll up both
# ways, and an optional default posting account.
#
# Codes referenced by any posted line are archived, never deleted. The
# standard list (CSI MasterFormat divisions) is loaded on request, not
# forced on companies that use their own numbering.
# ============================================================================

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import relationship

from app.database import Base

COST_TYPES = ("labor", "material", "subcontract", "equipment", "other")


class CostCode(Base):
    __tablename__ = "cost_codes"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(20), nullable=False, unique=True)
    name = Column(String(200), nullable=False)
    cost_type = Column(String(20), nullable=False, default="other")
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)
    notes = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    account = relationship("Account")

    @property
    def label(self) -> str:
        return f"{self.code} {self.name}".strip()


# CSI MasterFormat divisions — the numbering most US contractors, and every
# estimating package, already speak. Loaded via POST /api/cost-codes/standard.
STANDARD_COST_CODES = (
    ("01", "General Requirements", "other"),
    ("02", "Existing Conditions", "subcontract"),
    ("03", "Concrete", "subcontract"),
    ("04", "Masonry", "subcontract"),
    ("05", "Metals", "material"),
    ("06", "Wood, Plastics, and Composites", "material"),
    ("07", "Thermal and Moisture Protection", "subcontract"),
    ("08", "Openings", "material"),
    ("09", "Finishes", "subcontract"),
    ("10", "Specialties", "material"),
    ("11", "Equipment", "equipment"),
    ("12", "Furnishings", "material"),
    ("13", "Special Construction", "subcontract"),
    ("14", "Conveying Equipment", "subcontract"),
    ("21", "Fire Suppression", "subcontract"),
    ("22", "Plumbing", "subcontract"),
    ("23", "Heating, Ventilating, and Air Conditioning", "subcontract"),
    ("25", "Integrated Automation", "subcontract"),
    ("26", "Electrical", "subcontract"),
    ("27", "Communications", "subcontract"),
    ("28", "Electronic Safety and Security", "subcontract"),
    ("31", "Earthwork", "subcontract"),
    ("32", "Exterior Improvements", "subcontract"),
    ("33", "Utilities", "subcontract"),
    ("L", "Labor", "labor"),
    ("EQ", "Equipment Rental", "equipment"),
)
