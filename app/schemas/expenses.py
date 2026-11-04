from datetime import date as dt_date
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field, field_validator
from app.schemas.common import StrictModel, clean_line_cost_type


class ExpenseLineIn(StrictModel):
    """One line of an expense (#599): the account it is recorded to, its
    share of the amount paid, its own memo, and the class, job, cost code
    and cost type it carries. Left out, class and job are the expense's."""

    account_id: int
    amount: Decimal
    description: Optional[str] = Field(None, max_length=300)
    class_id: Optional[int] = None
    job_id: Optional[int] = None
    cost_code_id: Optional[int] = None
    cost_type: Optional[str] = None
    function: Optional[str] = None  # nonprofit: program | management | fundraising
    is_billable: bool = False

    _cost_type = field_validator("cost_type")(clean_line_cost_type)


class ExpenseCreate(StrictModel):
    """One paid receipt: money already left a bank or credit-card account.
    DR the expense account (or, with `lines`, each line's account for its
    share), CR the account it was paid from. Send `lines` or the older
    single `expense_account_id`, not both; the lines add up to `amount`
    (left out, `amount` is their sum)."""

    date: dt_date
    vendor_id: Optional[int] = None
    payee: Optional[str] = Field(None, max_length=200)
    expense_account_id: Optional[int] = None
    paid_from_account_id: int
    amount: Optional[Decimal] = None
    lines: Optional[list[ExpenseLineIn]] = None
    reference: Optional[str] = Field(None, max_length=100)
    memo: Optional[str] = None
    class_id: Optional[int] = None
    job_id: Optional[int] = None
    cost_code_id: Optional[int] = None
    cost_type: Optional[str] = None
    function: Optional[str] = None  # nonprofit: program | management | fundraising
    is_billable: bool = False

    _cost_type = field_validator("cost_type")(clean_line_cost_type)


class ExpenseLineOut(BaseModel):
    account_id: int
    account_name: str = ""
    amount: float
    description: str = ""
    class_id: Optional[int] = None
    job_id: Optional[int] = None
    cost_code_id: Optional[int] = None
    cost_type: Optional[str] = None
    function: Optional[str] = None
    is_billable: bool = False
    line_order: int = 0


class ExpenseResponse(BaseModel):
    id: int
    date: dt_date
    payee: str = ""
    vendor_id: Optional[int] = None
    # the first line's account, kept for older callers; see `lines`
    expense_account_id: Optional[int] = None
    expense_account_name: str = ""
    lines: list[ExpenseLineOut] = []
    paid_from_account_name: str = ""
    # the paid-from account and the header's class and job, which the form
    # fills in when it opens to change the expense (#689)
    paid_from_account_id: Optional[int] = None
    class_id: Optional[int] = None
    job_id: Optional[int] = None
    amount: float
    reference: str = ""
    memo: str = ""
    status: str = "recorded"  # "recorded" | "void"
    printable: bool = False  # paid from a bank account and not void: Print Check
    # Void-and-replace (#689): the expense this one replaced, and the one that
    # replaced it (this one is then void).
    replaces_id: Optional[int] = None
    replaced_by_id: Optional[int] = None
