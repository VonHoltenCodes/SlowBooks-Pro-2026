from datetime import date as dt_date, datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, field_validator
from app.schemas.common import StrictModel

from app.models.banking import ReconciliationStatus


class BankAccountCreate(StrictModel):
    """A bank feed / statement identity for a ledger account that has
    bank_kind set. An opening balance posts a journal entry against 3900
    Opening Balance Equity; it is what the statement says (cash in the
    bank, or the amount owed on a card). When the ledger already carries
    the account on the opening date, only a difference can post, and only
    with `post_difference` set (the user confirmed it)."""

    name: str
    account_id: int
    bank_name: Optional[str] = None
    last_four: Optional[str] = None
    opening_balance: Decimal = Decimal("0")
    opening_date: Optional[dt_date] = None
    post_difference: bool = False


class BankAccountUpdate(StrictModel):
    name: Optional[str] = None
    account_id: Optional[int] = None
    bank_name: Optional[str] = None
    last_four: Optional[str] = None
    is_active: Optional[bool] = None
    # only null is accepted: dismisses the pre-2.10 balance banner
    legacy_balance: Optional[Decimal] = None

    @field_validator("legacy_balance")
    @classmethod
    def only_null(cls, v):
        if v is not None:
            raise ValueError("legacy_balance can only be cleared (null)")
        return v


class BankAccountResponse(BaseModel):
    id: int
    name: str
    account_id: Optional[int]
    account_name: Optional[str] = None
    bank_kind: Optional[str] = None
    bank_name: Optional[str]
    last_four: Optional[str]
    balance: Decimal  # the linked ledger account's balance (0 when unlinked)
    legacy_balance: Optional[Decimal] = None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class LegacyBalancePost(StrictModel):
    date: dt_date


class BankTransactionCreate(StrictModel):
    """A register entry: posts a journal entry. amount > 0 debits the bank
    or card account (money in / card payment), amount < 0 credits it
    (money out / card charge). `account_id` is the ledger account;
    `bank_account_id` (the feed) is accepted for older callers."""

    account_id: Optional[int] = None
    bank_account_id: Optional[int] = None
    date: dt_date
    amount: Decimal
    category_account_id: int
    payee: Optional[str] = None
    description: Optional[str] = None
    check_number: Optional[str] = None
    class_id: Optional[int] = None
    job_id: Optional[int] = None


class BankEntryResponse(BaseModel):
    id: int  # the journal entry
    account_id: int
    date: dt_date
    amount: Decimal
    category_account_id: int
    category_name: str
    payee: str
    description: str
    reference: str
    source_type: str
    status: str


class BankTransactionResponse(BaseModel):
    """A statement line in the review queue."""

    id: int
    bank_account_id: int
    date: dt_date
    amount: Decimal
    payee: Optional[str]
    description: Optional[str]
    check_number: Optional[str]
    category_account_id: Optional[int]
    category_name: Optional[str] = None
    match_status: Optional[str] = None
    transaction_id: Optional[int] = None
    transaction_line_id: Optional[int] = None
    import_source: Optional[str] = None
    reconciled: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class StatementMatch(StrictModel):
    line_id: int


class StatementAdd(StrictModel):
    category_account_id: Optional[int] = None
    payee: Optional[str] = None
    memo: Optional[str] = None
    class_id: Optional[int] = None
    job_id: Optional[int] = None


class ReconciliationCreate(StrictModel):
    """`account_id` is the ledger account; `bank_account_id` (a feed) is
    accepted for older callers."""

    account_id: Optional[int] = None
    bank_account_id: Optional[int] = None
    statement_date: dt_date
    statement_balance: Decimal


class ReconciliationResponse(BaseModel):
    id: int
    account_id: Optional[int]
    bank_account_id: Optional[int]
    statement_date: dt_date
    statement_balance: Decimal
    beginning_balance: Decimal
    cleared_total: Optional[Decimal]
    status: ReconciliationStatus
    created_at: datetime
    completed_at: Optional[datetime]

    model_config = {"from_attributes": True}
