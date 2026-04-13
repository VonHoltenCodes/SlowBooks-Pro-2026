from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel


class CustomerCreate(BaseModel):
    name: str
    company: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    mobile: Optional[str] = None
    fax: Optional[str] = None
    website: Optional[str] = None
    bill_address1: Optional[str] = None
    bill_address2: Optional[str] = None
    bill_city: Optional[str] = None
    bill_state: Optional[str] = None
    bill_zip: Optional[str] = None
    bill_country: str = "NZ"
    ship_address1: Optional[str] = None
    ship_address2: Optional[str] = None
    ship_city: Optional[str] = None
    ship_state: Optional[str] = None
    ship_zip: Optional[str] = None
    ship_country: str = "NZ"
    terms: str = "Net 30"
    credit_limit: Optional[Decimal] = None
    tax_id: Optional[str] = None
    is_taxable: bool = True
    notes: Optional[str] = None


class CustomerUpdate(BaseModel):
    name: Optional[str] = None
    company: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    mobile: Optional[str] = None
    fax: Optional[str] = None
    website: Optional[str] = None
    bill_address1: Optional[str] = None
    bill_address2: Optional[str] = None
    bill_city: Optional[str] = None
    bill_state: Optional[str] = None
    bill_zip: Optional[str] = None
    bill_country: Optional[str] = None
    ship_address1: Optional[str] = None
    ship_address2: Optional[str] = None
    ship_city: Optional[str] = None
    ship_state: Optional[str] = None
    ship_zip: Optional[str] = None
    ship_country: Optional[str] = None
    terms: Optional[str] = None
    credit_limit: Optional[Decimal] = None
    tax_id: Optional[str] = None
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
    name: str
    company: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    fax: Optional[str] = None
    website: Optional[str] = None
    address1: Optional[str] = None
    address2: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip: Optional[str] = None
    country: str = "NZ"
    terms: str = "Net 30"
    tax_id: Optional[str] = None
    account_number: Optional[str] = None
    notes: Optional[str] = None


class VendorUpdate(BaseModel):
    name: Optional[str] = None
    company: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    fax: Optional[str] = None
    website: Optional[str] = None
    address1: Optional[str] = None
    address2: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip: Optional[str] = None
    country: Optional[str] = None
    terms: Optional[str] = None
    tax_id: Optional[str] = None
    account_number: Optional[str] = None
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
    notes: Optional[str]
    is_active: bool
    balance: Decimal
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
