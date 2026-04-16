from datetime import date
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel


class CCChargeCreate(BaseModel):
    date: date
    payee: Optional[str] = None
    account_id: int
    credit_card_account_id: int
    amount: Decimal
    reference: Optional[str] = None
    memo: Optional[str] = None


class CCChargeResponse(BaseModel):
    id: int
    date: date
    payee: Optional[str] = None
    account_id: int
    account_name: Optional[str] = None
    credit_card_account_id: int
    credit_card_account_name: Optional[str] = None
    amount: Decimal
    reference: Optional[str] = None
    memo: Optional[str] = None
