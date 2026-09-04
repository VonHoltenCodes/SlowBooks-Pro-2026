from typing import Optional
from pydantic import BaseModel


# --- Garnishment orders ----------------------------------------------------
class GarnishmentOrderCreate(BaseModel):
    employee_id: int
    garnishment_type: str = "creditor"
    calc_method: str = "fixed"
    amount: float = 0
    priority: int = 0
    case_number: Optional[str] = None
    supports_secondary_family: bool = False
    in_arrears_12_weeks: bool = False


class GarnishmentOrderResponse(BaseModel):
    id: int
    employee_id: int
    garnishment_type: str
    calc_method: str
    amount: float = 0
    priority: int = 0
    case_number: Optional[str] = None
    supports_secondary_family: bool = False
    in_arrears_12_weeks: bool = False
    is_active: bool = True
    model_config = {"from_attributes": True}


# --- Gross-up --------------------------------------------------------------
class GrossUpRequest(BaseModel):
    employee_id: int
    target_net: float
    supplemental: bool = True  # gross-ups are typically bonuses


class GrossUpResponse(BaseModel):
    employee_id: int
    target_net: float
    gross: float
    net: float
    withholding: float
