from datetime import date
from typing import Optional

from pydantic import BaseModel


class PendingDepositResponse(BaseModel):
    payment_id: int
    transaction_id: Optional[int] = None
    date: date
    customer_name: str
    reference: Optional[str] = None
    amount: float


class DepositCreate(BaseModel):
    date: date
    deposit_to_account_id: int
    payment_ids: list[int]
    reference: Optional[str] = None
    memo: Optional[str] = None
