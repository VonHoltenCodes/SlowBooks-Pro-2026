from datetime import datetime
from typing import Optional

from pydantic import BaseModel
from app.schemas.common import StrictModel


class BankRuleCreate(StrictModel):
    name: str
    pattern: str
    account_id: Optional[int] = None
    vendor_id: Optional[int] = None
    # the class and job a matched line comes in with (#675)
    class_id: Optional[int] = None
    job_id: Optional[int] = None
    rule_type: str = "contains"
    priority: int = 0
    is_active: bool = True


class BankRuleUpdate(StrictModel):
    name: Optional[str] = None
    pattern: Optional[str] = None
    account_id: Optional[int] = None
    vendor_id: Optional[int] = None
    class_id: Optional[int] = None
    job_id: Optional[int] = None
    rule_type: Optional[str] = None
    priority: Optional[int] = None
    is_active: Optional[bool] = None


class BankRuleResponse(BaseModel):
    id: int
    name: str
    pattern: str
    account_id: Optional[int]
    vendor_id: Optional[int]
    class_id: Optional[int] = None
    job_id: Optional[int] = None
    rule_type: str
    priority: int
    is_active: bool
    created_at: datetime
    # names for the list and the form (#493)
    account_name: Optional[str] = None
    vendor_name: Optional[str] = None
    class_name: Optional[str] = None
    job_name: Optional[str] = None

    model_config = {"from_attributes": True}
