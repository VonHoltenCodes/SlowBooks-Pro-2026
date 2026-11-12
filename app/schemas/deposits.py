from datetime import date as dt_date
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field
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


class DepositOtherLineIn(StrictModel):
    """A line on the slip for money that was never invoiced (#792): a
    reimbursement, a vendor's refund, a rebate. Who it came from is a
    customer, a vendor or a name typed in (all optional); the account is
    where the money goes (not a bank account, Undeposited Funds, Accounts
    Receivable or Accounts Payable)."""

    customer_id: Optional[int] = None
    vendor_id: Optional[int] = None
    received_from: Optional[str] = Field(default=None, max_length=200)
    account_id: int
    memo: Optional[str] = Field(default=None, max_length=300)
    check_number: Optional[str] = Field(default=None, max_length=50)
    method: Optional[str] = Field(default=None, max_length=30)
    class_id: Optional[int] = None
    job_id: Optional[int] = None
    amount: Decimal


class DepositOtherLineResponse(BaseModel):
    """An other line as the deposit's page and slip show it: the name it
    came from (the customer's or vendor's, else the one typed) and the
    account, class and job by name."""

    id: int
    account_id: int
    account_name: str = ""
    account_number: str = ""
    customer_id: Optional[int] = None
    vendor_id: Optional[int] = None
    received_from: str = ""
    memo: str = ""
    check_number: str = ""
    method: str = ""
    class_id: Optional[int] = None
    class_name: str = ""
    job_id: Optional[int] = None
    job_name: str = ""
    amount: float


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
    # the lines for money never invoiced it carries (#792)
    other_items: int = 0
    voided: bool = False
    reconciled: bool = False


class DepositDetailResponse(DepositResponse):
    """One deposit with the payments it took, each in the shape Make
    Deposits lists a waiting payment — what the bank register's link to a
    deposit opens. A void deposit has given its payments back to the list,
    so it names none."""

    payments: list[PendingDepositResponse] = []
    # the lines for money never invoiced (#792), and the two parts of the
    # amount: what the payments taken come to and what these lines do
    other_lines: list[DepositOtherLineResponse] = []
    payments_total: float = 0
    other_total: float = 0
    # After an edit (#388): the lines that lost their cleared mark and the
    # sentence that says so — the same note the journal entry's edit gives.
    uncleared: list[dict] = []
    note: Optional[str] = None


class DepositCreate(StrictModel):
    deposit_to_account_id: int
    date: dt_date
    # With line_ids or other_lines the deposit is the payments ticked plus
    # the other lines, and this is a cross-check of that whole; without
    # either (the API's older form) it is the amount.
    total: Optional[Decimal] = None
    reference: Optional[str] = None
    class_id: Optional[int] = None
    job_id: Optional[int] = None
    line_ids: list[int] = []
    # money that was never invoiced, on the same slip (#792)
    other_lines: list[DepositOtherLineIn] = []


class DepositUpdate(StrictModel):
    """Edit a deposit in place from the register (#388): its date, slip
    reference, class and job, and which undeposited payments it takes
    (`line_ids` — the ones it holds now plus any still waiting). A field
    left out keeps what the deposit has. A deposit on a reconciled bank
    statement keeps its amount and date; only its reference, class and job
    can change then. `other_lines` (#792), when sent, is the whole list of
    lines for money never invoiced (an empty list takes them all off);
    left out, the deposit keeps the lines it has."""

    date: Optional[dt_date] = None
    reference: Optional[str] = None
    class_id: Optional[int] = None
    job_id: Optional[int] = None
    line_ids: Optional[list[int]] = None
    other_lines: Optional[list[DepositOtherLineIn]] = None
