from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class BankRuleCreate(BaseModel):
    name: str
    pattern: str
    account_id: Optional[int] = None
    vendor_id: Optional[int] = None
    rule_type: str = "contains"
    priority: int = 0
    is_active: bool = True


class BankRuleUpdate(BaseModel):
    name: Optional[str] = None
    pattern: Optional[str] = None
    account_id: Optional[int] = None
    vendor_id: Optional[int] = None
    rule_type: Optional[str] = None
    priority: Optional[int] = None
    is_active: Optional[bool] = None


class BankRuleResponse(BaseModel):
    id: int
    name: str
    pattern: str
    account_id: Optional[int]
    vendor_id: Optional[int]
    rule_type: str
    priority: int
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}
