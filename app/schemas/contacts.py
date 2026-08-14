from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field

from app.schemas.common import NonBlankName, OptionalEmail


# Field lengths below mirror the VARCHAR(n) widths on the Customer model.
# Without them nothing validates length at the edge and the database is the
# only check — which the two supported backends answer differently: Postgres
# raises StringDataRightTruncation (surfacing as an opaque HTTP 500) while
# SQLite ignores VARCHAR(n) and stores the oversized value, so a desktop
# company file can hold data that cannot be imported into a Postgres/Server
# Edition deployment. Validating here makes both backends return one 422.
class CustomerCreate(BaseModel):
    name: NonBlankName
    company: Optional[str] = Field(None, max_length=200)
    email: Optional[OptionalEmail] = None
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
    terms: str = Field("Net 30", max_length=50)
    credit_limit: Optional[Decimal] = None
    tax_id: Optional[str] = Field(None, max_length=50)
    is_taxable: bool = True
    notes: Optional[str] = None


class CustomerUpdate(BaseModel):
    name: Optional[NonBlankName] = None
    company: Optional[str] = Field(None, max_length=200)
    email: Optional[OptionalEmail] = None
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
    credit_limit: Optional[Decimal] = None
    tax_id: Optional[str] = Field(None, max_length=50)
    is_taxable: Optional[bool] = None
    notes: Optional[str] = None
    is_active: Optional[bool] = None


class CustomerResponse(BaseModel):
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

    model_config = {"from_attributes": True}


class VendorCreate(BaseModel):
    name: NonBlankName
    company: Optional[str] = Field(None, max_length=200)
    email: Optional[OptionalEmail] = None
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
    notes: Optional[str] = None


class VendorUpdate(BaseModel):
    name: Optional[NonBlankName] = None
    company: Optional[str] = Field(None, max_length=200)
    email: Optional[OptionalEmail] = None
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
    notes: Optional[str] = None
    is_active: Optional[bool] = None


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
    notes: Optional[str]
    is_active: bool
    balance: Decimal
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
