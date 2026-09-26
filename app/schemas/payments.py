from datetime import date as dt_date, datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field
from app.schemas.common import StrictModel


class PaymentAllocationCreate(StrictModel):
    invoice_id: int
    amount: Decimal


class PaymentAllocationResponse(BaseModel):
    id: int
    invoice_id: int
    amount: Decimal
    # The invoice's own number, for a person to read ("1001", not the id)
    invoice_number: Optional[str] = None

    model_config = {"from_attributes": True}


class PaymentCreate(StrictModel):
    customer_id: int
    date: dt_date
    amount: Decimal
    method: Optional[str] = None
    check_number: Optional[str] = None
    reference: Optional[str] = None
    deposit_to_account_id: Optional[int] = None
    notes: Optional[str] = None
    currency: Optional[str] = None
    exchange_rate: Optional[Decimal] = None
    allocations: list[PaymentAllocationCreate] = []


class PaymentApply(StrictModel):
    """Apply the unapplied part of an earlier payment to open invoices of
    the same customer."""

    allocations: list[PaymentAllocationCreate] = Field(min_length=1)


class PaymentResponse(BaseModel):
    id: int
    customer_id: int
    date: dt_date
    amount: Decimal
    method: Optional[str]
    check_number: Optional[str]
    reference: Optional[str]
    deposit_to_account_id: Optional[int]
    notes: Optional[str]
    allocations: list[PaymentAllocationResponse] = []
    customer_name: Optional[str] = None
    is_voided: bool = False
    created_at: datetime
    currency: Optional[str] = None
    # The part of the payment not applied to any invoice: a credit the
    # customer holds, which can be applied later (0 once voided).
    unapplied: Decimal = Decimal("0")
    # On the single-payment read: the deposit it is in, as a sentence.
    deposited_in: Optional[str] = None

    model_config = {"from_attributes": True}
