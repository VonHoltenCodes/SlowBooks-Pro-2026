from datetime import date as dt_date
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, field_validator
from app.schemas.common import StrictModel


class JournalLineCreate(StrictModel):
    account_id: int
    job_id: Optional[int] = None
    class_id: Optional[int] = None
    cost_code_id: Optional[int] = None
    function: Optional[str] = None
    is_billable: bool = False
    debit: Decimal = Decimal("0")
    credit: Decimal = Decimal("0")

    @field_validator("debit", "credit")
    @classmethod
    def _cents(cls, value: Decimal) -> Decimal:
        # the ledger holds two decimals; a sub-cent amount that balanced on
        # paper could be stored a cent out (#286), so it is refused here
        if value != value.quantize(Decimal("0.01")):
            raise ValueError("Amounts are in cents: at most two decimal places.")
        return value

    description: Optional[str] = None


class JournalLineResponse(BaseModel):
    id: int
    account_id: int
    account_name: str = ""
    account_number: str = ""
    job_id: Optional[int] = None
    class_id: Optional[int] = None
    cost_code_id: Optional[int] = None
    function: Optional[str] = None
    is_billable: bool = False
    debit: float
    credit: float
    description: str = ""


class JournalEntryCreate(StrictModel):
    date: dt_date
    description: str
    reference: Optional[str] = None
    class_id: Optional[int] = None
    job_id: Optional[int] = None
    # An adjusting entry (#341). Left out, an accountant's sign-in posts
    # adjusting entries and every other sign-in posts ordinary ones.
    is_adjusting: Optional[bool] = None
    lines: list[JournalLineCreate]


class JournalEntryResponse(BaseModel):
    id: int
    date: dt_date
    description: str
    reference: str = ""
    class_id: Optional[int] = None
    job_id: Optional[int] = None
    source_type: str = ""
    # An accountant's adjustment, and the sign-in that posted the entry
    # (#340, #341); documents' postings carry neither.
    is_adjusting: bool = False
    posted_by: str = ""
    lines: list[JournalLineResponse] = []
    total_debit: float = 0
    total_credit: float = 0
    # A reversing entry stands against it (its own void, or its document's):
    # the page shows "Voided" instead of offering Void again.
    voided: bool = False
    # The page may offer Void: a journal of its own (hand-typed, imported,
    # the opening balances), not a reversal, not voided. The route decides.
    voidable: bool = False
    # A bank entry's register: the bank or card account it was posted
    # from, so the page can say where it is edited. Empty for the rest.
    register_account_id: Optional[int] = None
    register_account_name: str = ""
