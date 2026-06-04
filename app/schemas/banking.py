from datetime import date as dt_date, datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel

from app.models.banking import ReconciliationStatus


class BankAccountCreate(BaseModel):
    name: str
    account_id: Optional[int] = None
    bank_name: Optional[str] = None
    last_four: Optional[str] = None
    balance: Decimal = Decimal("0")


class BankAccountUpdate(BaseModel):
    name: Optional[str] = None
    account_id: Optional[int] = None
    bank_name: Optional[str] = None
    last_four: Optional[str] = None
    is_active: Optional[bool] = None


class BankAccountResponse(BaseModel):
    id: int
    name: str
    account_id: Optional[int]
    bank_name: Optional[str]
    last_four: Optional[str]
    balance: Decimal
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class BankTransactionCreate(BaseModel):
    bank_account_id: int
    date: dt_date
    amount: Decimal
    payee: Optional[str] = None
    description: Optional[str] = None
    check_number: Optional[str] = None
    category_account_id: Optional[int] = None


class BankTransactionResponse(BaseModel):
    id: int
    bank_account_id: int
    date: dt_date
    amount: Decimal
    payee: Optional[str]
    description: Optional[str]
    check_number: Optional[str]
    category_account_id: Optional[int]
    reconciled: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class ReconciliationCreate(BaseModel):
    bank_account_id: int
    statement_date: dt_date
    statement_balance: Decimal


class ReconciliationResponse(BaseModel):
    id: int
    bank_account_id: int
    statement_date: dt_date
    statement_balance: Decimal
    status: ReconciliationStatus
    created_at: datetime
    completed_at: Optional[datetime]

    model_config = {"from_attributes": True}
