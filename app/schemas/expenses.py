from datetime import date as dt_date
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field


class ExpenseCreate(BaseModel):
    """One paid receipt: money already left a bank or credit-card account.
    DR expense account, CR the account it was paid from."""

    date: dt_date
    vendor_id: Optional[int] = None
    payee: Optional[str] = Field(None, max_length=200)
    expense_account_id: int
    paid_from_account_id: int
    amount: Decimal
    reference: Optional[str] = Field(None, max_length=100)
    memo: Optional[str] = None
    class_id: Optional[int] = None
    job_id: Optional[int] = None


class ExpenseResponse(BaseModel):
    id: int
    date: dt_date
    payee: str = ""
    vendor_id: Optional[int] = None
    expense_account_name: str = ""
    paid_from_account_name: str = ""
    amount: float
    reference: str = ""
    memo: str = ""
    status: str = "recorded"  # "recorded" | "void"
