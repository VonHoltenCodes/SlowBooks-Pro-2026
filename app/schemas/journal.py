from datetime import date
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel


class JournalLineCreate(BaseModel):
    account_id: int
    debit: Decimal = Decimal("0")
    credit: Decimal = Decimal("0")
    description: Optional[str] = None


class JournalLineResponse(BaseModel):
    id: int
    account_id: int
    account_name: str = ""
    account_number: str = ""
    debit: float
    credit: float
    description: str = ""


class JournalEntryCreate(BaseModel):
    date: date
    description: str
    reference: Optional[str] = None
    lines: list[JournalLineCreate]


class JournalEntryResponse(BaseModel):
    id: int
    date: date
    description: str
    reference: str = ""
    source_type: str = ""
    lines: list[JournalLineResponse] = []
    total_debit: float = 0
    total_credit: float = 0
