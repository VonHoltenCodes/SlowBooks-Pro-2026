from datetime import date as dt_date
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel

from app.schemas.common import StrictModel


class TransferCreate(StrictModel):
    """Money between two bank/card accounts. Paying a card is a transfer
    from the bank to the card."""

    date: dt_date
    from_account_id: int
    to_account_id: int
    amount: Decimal
    memo: Optional[str] = None
    reference: Optional[str] = None
    # the class both lines carry (#676), as a journal entry's header class
    class_id: Optional[int] = None


class TransferResponse(BaseModel):
    id: int
    date: dt_date
    from_account_id: int
    from_account_name: str
    to_account_id: int
    to_account_name: str
    amount: Decimal
    memo: str
    reference: str
    status: str
