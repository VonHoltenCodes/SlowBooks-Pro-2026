from datetime import date
from typing import Optional
from pydantic import BaseModel


# --- Policies --------------------------------------------------------------
class PTOPolicyCreate(BaseModel):
    name: str
    pto_type: str = "vacation"
    accrual_method: str = "per_pay_period"
    accrual_rate: float = 0
    max_carryover: Optional[float] = None
    max_balance: Optional[float] = None


class PTOPolicyResponse(BaseModel):
    id: int
    name: str
    pto_type: str
    accrual_method: str
    accrual_rate: float = 0
    max_carryover: Optional[float] = None
    max_balance: Optional[float] = None
    is_active: bool = True
    model_config = {"from_attributes": True}


# --- Accruals --------------------------------------------------------------
class PTOAccrualCreate(BaseModel):
    employee_id: int
    policy_id: int
    balance: float = 0


class PTOAccrualResponse(BaseModel):
    id: int
    employee_id: int
    policy_id: int
    balance: float = 0
    accrued_ytd: float = 0
    used_ytd: float = 0
    model_config = {"from_attributes": True}


# --- Requests --------------------------------------------------------------
class PTORequestCreate(BaseModel):
    employee_id: int
    start_date: date
    end_date: date
    hours: float = 0
    pto_type: str = "vacation"
    notes: Optional[str] = None


class PTORequestDecision(BaseModel):
    status: str  # "approved" or "denied"
    approver_id: Optional[int] = None


class PTORequestResponse(BaseModel):
    id: int
    employee_id: int
    employee_name: Optional[str] = None
    start_date: date
    end_date: date
    hours: float = 0
    pto_type: str
    status: str
    approver_id: Optional[int] = None
    notes: Optional[str] = None
    model_config = {"from_attributes": True}
