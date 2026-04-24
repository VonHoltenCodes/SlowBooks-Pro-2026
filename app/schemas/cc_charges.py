from datetime import date
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel


class CCChargeCreate(BaseModel):
    date: date
    payee: Optional[str] = None
    account_id: int
    amount: Decimal
    memo: Optional[str] = None
    reference: Optional[str] = None


class CCChargeResponse(BaseModel):
    id: int
    date: date
    payee: str = ""
    account_name: str = ""
    amount: float
    memo: str = ""
    reference: str = ""
