from decimal import Decimal

from sqlalchemy import Boolean, Column, DateTime, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Session

from app.database import Base


DEFAULT_GST_CODES = [
    {
        "code": "GST15",
        "name": "GST 15%",
        "description": "Standard-rated New Zealand GST at 15%.",
        "rate": Decimal("0.1500"),
        "category": "taxable",
        "sort_order": 10,
    },
    {
        "code": "ZERO",
        "name": "Zero-rated",
        "description": "Zero-rated taxable supplies.",
        "rate": Decimal("0.0000"),
        "category": "zero_rated",
        "sort_order": 20,
    },
    {
        "code": "EXEMPT",
        "name": "Exempt",
        "description": "GST-exempt supplies.",
        "rate": Decimal("0.0000"),
        "category": "exempt",
        "sort_order": 30,
    },
    {
        "code": "NO_GST",
        "name": "No GST",
        "description": "Out-of-scope or non-GST transactions.",
        "rate": Decimal("0.0000"),
        "category": "no_gst",
        "sort_order": 40,
    },
]


class GstCode(Base):
    __tablename__ = "gst_codes"

    def __init__(self, **kwargs):
        kwargs.setdefault("rate", Decimal("0"))
        kwargs.setdefault("is_active", True)
        kwargs.setdefault("is_system", False)
        kwargs.setdefault("sort_order", 0)
        super().__init__(**kwargs)

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(20), unique=True, nullable=False, index=True)
    name = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    rate = Column(Numeric(6, 4), default=0, nullable=False)
    category = Column(String(50), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    is_system = Column(Boolean, default=False, nullable=False)
    sort_order = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


def ensure_default_gst_codes(db: Session, commit: bool = True) -> None:
    existing = {
        row.code: row
        for row in db.query(GstCode).filter(GstCode.code.in_([item["code"] for item in DEFAULT_GST_CODES])).all()
    }
    for item in DEFAULT_GST_CODES:
        row = existing.get(item["code"])
        if row:
            for key, value in item.items():
                setattr(row, key, value)
            row.is_system = True
        else:
            db.add(GstCode(**item, is_system=True, is_active=True))
    if commit:
        db.commit()
    else:
        db.flush()
