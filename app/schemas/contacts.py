from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal, Optional

from pydantic import AfterValidator, BaseModel, Field

from app.schemas.common import BlankableEmail, NonBlankName, StrictModel


def _check_credit_limit(value):
    """A credit limit is an amount the customer may owe; below zero it
    means nothing (explore 2.17.3, W-L4: −500 was accepted)."""
    if value is not None and value < 0:
        raise ValueError(
            "A credit limit can't be negative. Leave it blank for no limit."
        )
    return value


CreditLimit = Annotated[Optional[Decimal], AfterValidator(_check_credit_limit)]


# Field lengths below mirror the VARCHAR(n) widths on the Customer model.
# Without them nothing validates length at the edge and the database is the
# only check — which the two supported backends answer differently: Postgres
# raises StringDataRightTruncation (surfacing as an opaque HTTP 500) while
# SQLite ignores VARCHAR(n) and stores the oversized value, so a desktop
# company file can hold data that cannot be imported into a Postgres/Server
# Edition deployment. Validating here makes both backends return one 422.
class CustomerCreate(StrictModel):
    name: NonBlankName
    company: Optional[str] = Field(None, max_length=200)
    email: BlankableEmail = None
    phone: Optional[str] = Field(None, max_length=50)
    mobile: Optional[str] = Field(None, max_length=50)
    fax: Optional[str] = Field(None, max_length=50)
    website: Optional[str] = Field(None, max_length=200)
    bill_address1: Optional[str] = Field(None, max_length=200)
    bill_address2: Optional[str] = Field(None, max_length=200)
    bill_city: Optional[str] = Field(None, max_length=100)
    bill_state: Optional[str] = Field(None, max_length=50)
    bill_zip: Optional[str] = Field(None, max_length=20)
    bill_country: str = Field("US", max_length=100)
    ship_address1: Optional[str] = Field(None, max_length=200)
    ship_address2: Optional[str] = Field(None, max_length=200)
    ship_city: Optional[str] = Field(None, max_length=100)
    ship_state: Optional[str] = Field(None, max_length=50)
    ship_zip: Optional[str] = Field(None, max_length=20)
    ship_country: str = Field("US", max_length=100)
    # None = the company's default terms (Settings); see create_customer.
    terms: Optional[str] = Field(None, max_length=50)
    credit_limit: CreditLimit = None
    tax_id: Optional[str] = Field(None, max_length=50)
    is_taxable: bool = True
    notes: Optional[str] = None
    # Nonprofit donor record
    donor_type: Optional[str] = Field(None, max_length=20)
    salutation: Optional[str] = Field(None, max_length=100)
    send_year_end_statement: bool = True
    # Sub-customer (#638): the customer this one sits under, one level
    # deep; bill_with_parent puts its invoices on the parent's A/R.
    parent_customer_id: Optional[int] = None
    bill_with_parent: bool = False
    invoice_reminders_enabled: bool = True
    monthly_statement: bool = False


class CustomerUpdate(StrictModel):
    name: Optional[NonBlankName] = None
    company: Optional[str] = Field(None, max_length=200)
    email: BlankableEmail = None
    phone: Optional[str] = Field(None, max_length=50)
    mobile: Optional[str] = Field(None, max_length=50)
    fax: Optional[str] = Field(None, max_length=50)
    website: Optional[str] = Field(None, max_length=200)
    bill_address1: Optional[str] = Field(None, max_length=200)
    bill_address2: Optional[str] = Field(None, max_length=200)
    bill_city: Optional[str] = Field(None, max_length=100)
    bill_state: Optional[str] = Field(None, max_length=50)
    bill_zip: Optional[str] = Field(None, max_length=20)
    bill_country: Optional[str] = Field(None, max_length=100)
    ship_address1: Optional[str] = Field(None, max_length=200)
    ship_address2: Optional[str] = Field(None, max_length=200)
    ship_city: Optional[str] = Field(None, max_length=100)
    ship_state: Optional[str] = Field(None, max_length=50)
    ship_zip: Optional[str] = Field(None, max_length=20)
    ship_country: Optional[str] = Field(None, max_length=100)
    terms: Optional[str] = Field(None, max_length=50)
    credit_limit: CreditLimit = None
    tax_id: Optional[str] = Field(None, max_length=50)
    is_taxable: Optional[bool] = None
    notes: Optional[str] = None
    # Nonprofit donor record
    donor_type: Optional[str] = Field(None, max_length=20)
    salutation: Optional[str] = Field(None, max_length=100)
    send_year_end_statement: Optional[bool] = None
    invoice_reminders_enabled: Optional[bool] = None
    monthly_statement: Optional[bool] = None
    is_active: Optional[bool] = None
    parent_customer_id: Optional[int] = None
    bill_with_parent: Optional[bool] = None


class CustomerResponse(BaseModel):
    """A customer as every customer endpoint answers it. Three balances,
    each in home dollars, summed from the open documents (the stored
    Customer.balance column is never written; services/contact_balances):

    * ``balance`` is what the customer's own open documents leave: its
      invoices, net of the credit memos and unapplied payments it holds.
    * ``billed_balance`` is what sits on the customer's A/R: its own
      balance plus that of the sub-customers billed with it, the figure
      its A/R Aging row shows and what a property owes for its tenants.
      The same as ``balance`` for a customer with no sub-customer billed
      with it (2.31.1 gate, W-61).
    * ``rollup_balance`` is set on a parent only: its own balance plus
      every sub-customer's, billed with it or not, what the property has
      open altogether (its page's roll-up). None otherwise.
    """

    id: int
    name: str
    company: Optional[str]
    email: Optional[str]
    phone: Optional[str]
    mobile: Optional[str]
    fax: Optional[str]
    website: Optional[str]
    bill_address1: Optional[str]
    bill_address2: Optional[str]
    bill_city: Optional[str]
    bill_state: Optional[str]
    bill_zip: Optional[str]
    bill_country: Optional[str]
    ship_address1: Optional[str]
    ship_address2: Optional[str]
    ship_city: Optional[str]
    ship_state: Optional[str]
    ship_zip: Optional[str]
    ship_country: Optional[str]
    terms: Optional[str]
    credit_limit: Optional[Decimal]
    tax_id: Optional[str]
    is_taxable: bool
    notes: Optional[str]
    is_active: bool
    balance: Decimal
    created_at: datetime
    updated_at: datetime

    donor_type: Optional[str] = None
    salutation: Optional[str] = None
    send_year_end_statement: bool = True
    invoice_reminders_enabled: bool = True
    monthly_statement: bool = False

    # Sub-customers (#638). full_name is "Parent: Sub" for a sub-customer
    # (the pickers' spelling) and the name itself otherwise; parent_name
    # is set on a sub; sub_count on a parent. The three balances are
    # told apart in the class docstring.
    parent_customer_id: Optional[int] = None
    parent_name: Optional[str] = None
    bill_with_parent: bool = False
    full_name: str = ""
    sub_count: int = 0
    billed_balance: Decimal = Decimal("0")
    rollup_balance: Optional[Decimal] = None

    model_config = {"from_attributes": True}


class VendorCreate(StrictModel):
    name: NonBlankName
    company: Optional[str] = Field(None, max_length=200)
    email: BlankableEmail = None
    phone: Optional[str] = Field(None, max_length=50)
    fax: Optional[str] = Field(None, max_length=50)
    website: Optional[str] = Field(None, max_length=200)
    address1: Optional[str] = Field(None, max_length=200)
    address2: Optional[str] = Field(None, max_length=200)
    city: Optional[str] = Field(None, max_length=100)
    state: Optional[str] = Field(None, max_length=50)
    zip: Optional[str] = Field(None, max_length=20)
    country: str = Field("US", max_length=100)
    terms: str = Field("Net 30", max_length=50)
    tax_id: Optional[str] = Field(None, max_length=50)
    account_number: Optional[str] = Field(None, max_length=50)
    default_expense_account_id: Optional[int] = None
    is_1099_vendor: bool = False
    vendor_1099_type: Optional[str] = Field(None, max_length=10)
    # The W-9 the 1099-NEC prints "on file: Yes/No" for; nothing could set
    # it until 2.22.2 (#257), so every form said No.
    w9_on_file: bool = False
    # Construction mode (#431): "sub" marks a subcontractor, with its trade
    # license and certificate of insurance. Blank on an ordinary vendor;
    # "sub" is the only value for now.
    vendor_type: Optional[Literal["sub"]] = None
    license_number: Optional[str] = Field(None, max_length=100)
    coi_carrier: Optional[str] = Field(None, max_length=200)
    coi_policy_number: Optional[str] = Field(None, max_length=100)
    coi_expires: Optional[date] = None
    notes: Optional[str] = None
    # The vendor form's "Bank feed: lines from this payee post to <the
    # default expense account>" (#493): true makes or updates the vendor's
    # bank rule, false turns it off, left out leaves it as it is. Not a
    # vendor column, so never part of model_dump().
    bank_feed_rule: Optional[bool] = Field(None, exclude=True)


class VendorUpdate(StrictModel):
    name: Optional[NonBlankName] = None
    company: Optional[str] = Field(None, max_length=200)
    email: BlankableEmail = None
    phone: Optional[str] = Field(None, max_length=50)
    fax: Optional[str] = Field(None, max_length=50)
    website: Optional[str] = Field(None, max_length=200)
    address1: Optional[str] = Field(None, max_length=200)
    address2: Optional[str] = Field(None, max_length=200)
    city: Optional[str] = Field(None, max_length=100)
    state: Optional[str] = Field(None, max_length=50)
    zip: Optional[str] = Field(None, max_length=20)
    country: Optional[str] = Field(None, max_length=100)
    terms: Optional[str] = Field(None, max_length=50)
    tax_id: Optional[str] = Field(None, max_length=50)
    account_number: Optional[str] = Field(None, max_length=50)
    default_expense_account_id: Optional[int] = None
    is_1099_vendor: Optional[bool] = None
    vendor_1099_type: Optional[str] = Field(None, max_length=10)
    w9_on_file: Optional[bool] = None
    vendor_type: Optional[Literal["sub"]] = None
    license_number: Optional[str] = Field(None, max_length=100)
    coi_carrier: Optional[str] = Field(None, max_length=200)
    coi_policy_number: Optional[str] = Field(None, max_length=100)
    coi_expires: Optional[date] = None
    notes: Optional[str] = None
    is_active: Optional[bool] = None
    # The vendor form's "Bank feed: lines from this payee post to <the
    # default expense account>" (#493): true makes or updates the vendor's
    # bank rule, false turns it off, left out leaves it as it is. Not a
    # vendor column, so never part of model_dump().
    bank_feed_rule: Optional[bool] = Field(None, exclude=True)


class VendorResponse(BaseModel):
    id: int
    name: str
    company: Optional[str]
    email: Optional[str]
    phone: Optional[str]
    fax: Optional[str]
    website: Optional[str]
    address1: Optional[str]
    address2: Optional[str]
    city: Optional[str]
    state: Optional[str]
    zip: Optional[str]
    country: Optional[str]
    terms: Optional[str]
    tax_id: Optional[str]
    account_number: Optional[str]
    default_expense_account_id: Optional[int] = None
    is_1099_vendor: bool = False
    vendor_1099_type: Optional[str] = None
    w9_on_file: bool = False
    vendor_type: Optional[str] = None
    license_number: Optional[str] = None
    coi_carrier: Optional[str] = None
    coi_policy_number: Optional[str] = None
    coi_expires: Optional[date] = None
    notes: Optional[str]
    is_active: bool
    balance: Decimal
    created_at: datetime
    updated_at: datetime
    # the vendor's active bank rule, if it has one (#493)
    bank_feed_rule: bool = False
    bank_rule_id: Optional[int] = None

    model_config = {"from_attributes": True}
