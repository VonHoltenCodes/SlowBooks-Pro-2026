from datetime import date as dt_date
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel


class JournalLineCreate(BaseModel):
    account_id: int
    job_id: Optional[int] = None
    class_id: Optional[int] = None
    cost_code_id: Optional[int] = None
    is_billable: bool = False
    debit: Decimal = Decimal("0")
    credit: Decimal = Decimal("0")
    description: Optional[str] = None


class JournalLineResponse(BaseModel):
    id: int
    account_id: int
    account_name: str = ""
    account_number: str = ""
    job_id: Optional[int] = None
    class_id: Optional[int] = None
    cost_code_id: Optional[int] = None
    is_billable: bool = False
    debit: float
    credit: float
    description: str = ""


class JournalEntryCreate(BaseModel):
    date: dt_date
    description: str
    reference: Optional[str] = None
    class_id: Optional[int] = None
    job_id: Optional[int] = None
    lines: list[JournalLineCreate]


class JournalEntryResponse(BaseModel):
    id: int
    date: dt_date
    description: str
    reference: str = ""
    class_id: Optional[int] = None
    job_id: Optional[int] = None
    source_type: str = ""
    lines: list[JournalLineResponse] = []
    total_debit: float = 0
    total_credit: float = 0
