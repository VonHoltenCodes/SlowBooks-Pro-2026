from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel


class PaymentAllocationCreate(BaseModel):
    invoice_id: int
    amount: Decimal


class PaymentAllocationResponse(BaseModel):
    id: int
    invoice_id: int
    amount: Decimal

    model_config = {"from_attributes": True}


class PaymentCreate(BaseModel):
    customer_id: int
    date: date
    amount: Decimal
    method: Optional[str] = None
    check_number: Optional[str] = None
    reference: Optional[str] = None
    deposit_to_account_id: Optional[int] = None
    notes: Optional[str] = None
    allocations: list[PaymentAllocationCreate] = []


class PaymentResponse(BaseModel):
    id: int
    customer_id: int
    date: date
    amount: Decimal
    method: Optional[str]
    check_number: Optional[str]
    reference: Optional[str]
    deposit_to_account_id: Optional[int]
    notes: Optional[str]
    deposit_transaction_id: Optional[int] = None
    is_voided: bool = False
    allocations: list[PaymentAllocationResponse] = []
    customer_name: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}
