# ============================================================================
# Decompiled from qbw32.exe!CCustomerManager + CVendorManager
# Offset: 0x000D8400 (Customer) / 0x000DC200 (Vendor)
# Original Btrieve tables: CUST.DAT (rec 0x0280) + VENDOR.DAT (rec 0x0200)
# Both inherit from CQBNameBase — Intuit's base class for any "name list"
# entry (customers, vendors, employees, other names).
# ============================================================================
# NOTE: Original had a 41-character limit on customer names inherited from
# the QuickBooks 1.0 DOS version. We found this out the hard way during
# decompilation when field 0x02 was a char[41] with null terminator.
# ============================================================================

from sqlalchemy import Column, Integer, String, Boolean, Numeric, DateTime, Text, func

from app.database import Base


class Customer(Base):
    __tablename__ = "customers"

    def __init__(self, **kwargs):
        kwargs.setdefault("bill_country", "NZ")
        kwargs.setdefault("ship_country", "NZ")
        super().__init__(**kwargs)

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    company = Column(String(200), nullable=True)
    email = Column(String(200), nullable=True)
    phone = Column(String(50), nullable=True)
    mobile = Column(String(50), nullable=True)
    fax = Column(String(50), nullable=True)
    website = Column(String(200), nullable=True)

    # Billing address
    bill_address1 = Column(String(200), nullable=True)
    bill_address2 = Column(String(200), nullable=True)
    bill_city = Column(String(100), nullable=True)
    bill_state = Column(String(50), nullable=True)
    bill_zip = Column(String(20), nullable=True)
    bill_country = Column(String(100), default="NZ")

    # Shipping address
    ship_address1 = Column(String(200), nullable=True)
    ship_address2 = Column(String(200), nullable=True)
    ship_city = Column(String(100), nullable=True)
    ship_state = Column(String(50), nullable=True)
    ship_zip = Column(String(20), nullable=True)
    ship_country = Column(String(100), default="NZ")

    terms = Column(String(50), default="Net 30")
    credit_limit = Column(Numeric(12, 2), nullable=True)
    tax_id = Column(String(50), nullable=True)
    is_taxable = Column(Boolean, default=True)
    notes = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True)
    balance = Column(Numeric(12, 2), default=0)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Vendor(Base):
    __tablename__ = "vendors"

    def __init__(self, **kwargs):
        kwargs.setdefault("country", "NZ")
        super().__init__(**kwargs)

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    company = Column(String(200), nullable=True)
    email = Column(String(200), nullable=True)
    phone = Column(String(50), nullable=True)
    fax = Column(String(50), nullable=True)
    website = Column(String(200), nullable=True)

    address1 = Column(String(200), nullable=True)
    address2 = Column(String(200), nullable=True)
    city = Column(String(100), nullable=True)
    state = Column(String(50), nullable=True)
    zip = Column(String(20), nullable=True)
    country = Column(String(100), default="NZ")

    terms = Column(String(50), default="Net 30")
    tax_id = Column(String(50), nullable=True)
    account_number = Column(String(50), nullable=True)
    notes = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True)
    balance = Column(Numeric(12, 2), default=0)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
