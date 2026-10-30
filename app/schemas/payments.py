from datetime import date as dt_date, datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field, StrictBool
from app.schemas.common import StrictModel


class PaymentAllocationCreate(StrictModel):
    invoice_id: int
    amount: Decimal
    # An early-payment discount the customer took on this invoice (#664),
    # on top of `amount`: posted to the payment's discount_account_id.
    # Taken when the payment is received, not when a remainder is applied.
    discount: Decimal = Decimal("0")


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
    # Where the allocations' discounts post (#664): required when one takes
    # a discount, refused when none does.
    discount_account_id: Optional[int] = None
    # Take a discount the invoice's terms don't offer: none at all, after
    # its date, more than its percent, or a second one (#664). Each refusal
    # names this field.
    allow_discount_override: StrictBool = False


class PaymentApply(StrictModel):
    """Apply the unapplied part of an earlier payment to open invoices of
    the same customer."""

    allocations: list[PaymentAllocationCreate] = Field(min_length=1)


class PaymentDiscountResponse(BaseModel):
    """A discount the customer took with this payment: the credit memo
    applied to the invoice (#664)."""

    invoice_id: int
    invoice_number: Optional[str] = None
    amount: Decimal
    credit_memo_id: int
    memo_number: str
    account_id: Optional[int] = None


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
    # On a void: a caution the screen shows, e.g. the payment was already
    # paid out by its provider (#584). The void still went through.
    warning: Optional[str] = None
    # On the single-payment read: an online payment whose processing fee is
    # booked, so the Void confirm can ask whether the provider refunded it.
    has_provider_fee: bool = False
    # The early-payment discounts taken with it (#664); empty when none.
    discounts: list[PaymentDiscountResponse] = []

    model_config = {"from_attributes": True}
