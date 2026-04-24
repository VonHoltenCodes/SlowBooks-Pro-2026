from datetime import date
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel


class PendingDepositResponse(BaseModel):
    transaction_line_id: int
    transaction_id: int
    date: date
    description: str
    reference: str = ""
    source_type: str = ""
    amount: float


class DepositCreate(BaseModel):
    deposit_to_account_id: int
    date: date
    total: Decimal
    reference: Optional[str] = None
    line_ids: list[int] = []
