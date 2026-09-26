from datetime import date as dt_date
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel
from app.schemas.common import StrictModel


class PendingDepositResponse(BaseModel):
    transaction_line_id: int
    transaction_id: int
    date: dt_date
    description: str
    reference: str = ""
    source_type: str = ""
    amount: float
    # The payment behind the line, so the list can tell two "Payment from
    # Acme" lines apart: who paid, the check number and reference, the
    # method, and the invoice or sales receipt it paid.
    payment_id: Optional[int] = None
    received_from: str = ""
    check_number: str = ""
    payment_reference: str = ""
    method: str = ""
    document: str = ""


class DepositResponse(BaseModel):
    """A deposit made: its journal entry id, the bank account and amount,
    how many payments it took (None for a deposit that named none — made
    before deposits kept their list, imported, or posted by hand), and whether it is void or
    on a reconciled bank statement."""

    id: int
    date: dt_date
    reference: str = ""
    account_id: Optional[int] = None
    account_name: str = ""
    amount: Decimal
    items: Optional[int] = None
    voided: bool = False
    reconciled: bool = False


class DepositCreate(StrictModel):
    deposit_to_account_id: int
    date: dt_date
    # With line_ids the deposit is the sum of those payments and this is a
    # cross-check; without them (the API's older form) it is the amount.
    total: Optional[Decimal] = None
    reference: Optional[str] = None
    class_id: Optional[int] = None
    job_id: Optional[int] = None
    line_ids: list[int] = []
