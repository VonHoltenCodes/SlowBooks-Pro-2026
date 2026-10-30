# ============================================================================
# Customers + Vendors — QuickBooks treated every contact as a "name list"
# entry (customers, vendors, employees, other names); same idea here.
# ============================================================================

from sqlalchemy import (
    Column,
    Integer,
    String,
    Boolean,
    Numeric,
    Date,
    DateTime,
    Text,
    ForeignKey,
    false,
    func,
    true,
)
from sqlalchemy.orm import backref, relationship

from app.database import Base


class Customer(Base):
    __tablename__ = "customers"

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
    bill_country = Column(String(100), default="US")

    # Shipping address
    ship_address1 = Column(String(200), nullable=True)
    ship_address2 = Column(String(200), nullable=True)
    ship_city = Column(String(100), nullable=True)
    ship_state = Column(String(50), nullable=True)
    ship_zip = Column(String(20), nullable=True)
    ship_country = Column(String(100), default="US")

    terms = Column(String(50), default="Net 30")
    credit_limit = Column(Numeric(15, 2), nullable=True)
    tax_id = Column(String(50), nullable=True)
    is_taxable = Column(Boolean, default=True)
    notes = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True)
    balance = Column(Numeric(15, 2), default=0)
    # Nonprofit: the donor record. individual | organization; the
    # salutation opens the acknowledgment letter; the year-end statement
    # batch skips donors who opted out.
    donor_type = Column(String(20), nullable=True)
    salutation = Column(String(100), nullable=True)
    send_year_end_statement = Column(Boolean, nullable=False, default=True)
    # Sub-customers (#638): QuickBooks' "Parent:Sub". A property is a
    # customer and each tenant a sub-customer under it, one level deep (a
    # sub has no subs of its own). bill_with_parent puts the sub's invoices
    # on the parent's A/R: the parent's statement and aging row carry them,
    # the parent's payments apply to them, and the printed invoice bills
    # the parent naming the sub. Off, the sub is billed on its own.
    parent_customer_id = Column(
        Integer, ForeignKey("customers.id"), nullable=True, index=True
    )
    bill_with_parent = Column(Boolean, nullable=False, default=False)
    # Collections (#653): payment reminders go out unless the customer is
    # opted out; the monthly statement only goes to customers who opt in.
    invoice_reminders_enabled = Column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    monthly_statement = Column(
        Boolean, nullable=False, default=False, server_default=false()
    )

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    parent = relationship(
        "Customer",
        remote_side="Customer.id",
        foreign_keys="Customer.parent_customer_id",
        backref=backref("subs", order_by="Customer.name"),
    )

    @property
    def full_name(self) -> str:
        """ "Parent: Sub" for a sub-customer, as the pickers show it; a
        customer with no parent is its own name."""
        parent = self.parent if self.parent_customer_id else None
        return f"{parent.name}: {self.name}" if parent else self.name

    @property
    def qb_name(self) -> str:
        """QuickBooks' own spelling, "Parent:Sub", for the IIF export."""
        parent = self.parent if self.parent_customer_id else None
        return f"{parent.name}:{self.name}" if parent else self.name

    @property
    def billed_to_id(self) -> int:
        """The customer whose A/R carries this one's documents: the parent
        of a sub billed with it, else the customer itself."""
        if self.parent_customer_id and self.bill_with_parent:
            return self.parent_customer_id
        return self.id


class Vendor(Base):
    __tablename__ = "vendors"

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
    country = Column(String(100), default="US")

    terms = Column(String(50), default="Net 30")
    tax_id = Column(String(50), nullable=True)
    account_number = Column(String(50), nullable=True)
    default_expense_account_id = Column(
        Integer, ForeignKey("accounts.id"), nullable=True
    )
    is_1099_vendor = Column(Boolean, default=False)
    vendor_1099_type = Column(String(10), nullable=True)
    # 1099-NEC workflow (Tier 1.6)
    is_1099_eligible = Column(Boolean, default=False)
    w9_on_file = Column(Boolean, default=False)
    w9_document_id = Column(Integer, ForeignKey("attachments.id"), nullable=True)
    # Construction mode (#431): "sub" marks a subcontractor; the trade
    # license and the certificate of insurance a GC collects before the job.
    # All nullable — an ordinary vendor and a non-construction company leave
    # them blank. "sub" is vendor_type's only value for now.
    vendor_type = Column(String(20), nullable=True)
    license_number = Column(String(100), nullable=True)
    coi_carrier = Column(String(200), nullable=True)
    coi_policy_number = Column(String(100), nullable=True)
    coi_expires = Column(Date, nullable=True)
    notes = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True)
    balance = Column(Numeric(15, 2), default=0)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
