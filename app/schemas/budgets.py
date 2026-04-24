from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel


class BudgetCreate(BaseModel):
    account_id: int
    year: int
    month: int
    amount: Decimal = Decimal("0")


class BudgetUpdate(BaseModel):
    amount: Optional[Decimal] = None


class BudgetResponse(BaseModel):
    id: int
    account_id: int
    year: int
    month: int
    amount: Decimal
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
