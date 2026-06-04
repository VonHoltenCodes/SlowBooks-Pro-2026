from datetime import date as dt_date
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel


class CCChargeCreate(BaseModel):
    date: dt_date
    payee: Optional[str] = None
    account_id: int
    amount: Decimal
    memo: Optional[str] = None
    reference: Optional[str] = None


class CCChargeResponse(BaseModel):
    id: int
    date: dt_date
    payee: str = ""
    account_name: str = ""
    amount: float
    memo: str = ""
    reference: str = ""
