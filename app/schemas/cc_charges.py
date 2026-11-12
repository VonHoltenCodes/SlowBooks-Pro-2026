from datetime import date as dt_date
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel
from app.schemas.common import StrictModel
from app.schemas.attachments import PendingAttachmentIds


class CCChargeCreate(StrictModel):
    date: dt_date
    payee: Optional[str] = None
    account_id: int
    # the card (a liability account; default 2100 Credit Card)
    card_account_id: Optional[int] = None
    amount: Decimal
    memo: Optional[str] = None
    reference: Optional[str] = None
    class_id: Optional[int] = None
    job_id: Optional[int] = None
    function: Optional[str] = None  # nonprofit: program | management | fundraising
    # Files attached on the form before this first save (#796)
    pending_attachments: PendingAttachmentIds = []


class CCChargeResponse(BaseModel):
    id: int
    date: dt_date
    payee: str = ""
    account_name: str = ""
    amount: float
    memo: str = ""
    reference: str = ""
