from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel


class JournalLineCreate(BaseModel):
    account_id: int
    debit: Decimal = Decimal("0")
    credit: Decimal = Decimal("0")
    description: Optional[str] = None


class JournalLineResponse(BaseModel):
    account_id: int
    account_name: Optional[str] = None
    account_number: Optional[str] = None
    debit: Decimal
    credit: Decimal
    description: Optional[str] = None


class JournalEntryCreate(BaseModel):
    date: date
    description: str
    reference: Optional[str] = None
    lines: list[JournalLineCreate]


class JournalEntryResponse(BaseModel):
    id: int
    date: date
    description: Optional[str] = None
    reference: Optional[str] = None
    source_type: Optional[str] = None
    is_voided: bool = False
    lines: list[JournalLineResponse] = []
    created_at: datetime

    model_config = {"from_attributes": True}
