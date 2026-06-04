from datetime import date as dt_date, datetime
from typing import Optional
from pydantic import BaseModel


class TimeEntryCreate(BaseModel):
    employee_id: int
    date: dt_date
    hours_regular: float = 0
    hours_overtime: float = 0
    hours_doubletime: float = 0
    project_id: Optional[int] = None
    notes: Optional[str] = None


class TimeEntryUpdate(BaseModel):
    date: Optional[dt_date] = None
    hours_regular: Optional[float] = None
    hours_overtime: Optional[float] = None
    hours_doubletime: Optional[float] = None
    project_id: Optional[int] = None
    notes: Optional[str] = None
    status: Optional[str] = None


class TimeEntryResponse(BaseModel):
    id: int
    employee_id: int
    employee_name: Optional[str] = None
    date: dt_date
    hours_regular: float = 0
    hours_overtime: float = 0
    hours_doubletime: float = 0
    project_id: Optional[int] = None
    notes: Optional[str] = None
    status: str
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    pay_run_id: Optional[int] = None
    model_config = {"from_attributes": True}


class TimeEntryApprove(BaseModel):
    approved_by: str = "manager"
