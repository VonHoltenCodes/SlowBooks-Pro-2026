from datetime import date
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel


class EmployeeCreate(BaseModel):
    first_name: str
    last_name: str
    ird_number: Optional[str] = None
    pay_type: str = "hourly"
    pay_rate: float = 0
    tax_code: str = "M"
    kiwisaver_enrolled: bool = False
    kiwisaver_rate: Decimal = Decimal("0.0300")
    student_loan: bool = False
    child_support: bool = False
    esct_rate: Decimal = Decimal("0.0000")
    pay_frequency: str = "fortnightly"
    start_date: Optional[date] = None
    end_date: Optional[date] = None


class EmployeeUpdate(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    ird_number: Optional[str] = None
    pay_type: Optional[str] = None
    pay_rate: Optional[float] = None
    tax_code: Optional[str] = None
    kiwisaver_enrolled: Optional[bool] = None
    kiwisaver_rate: Optional[Decimal] = None
    student_loan: Optional[bool] = None
    child_support: Optional[bool] = None
    esct_rate: Optional[Decimal] = None
    pay_frequency: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    is_active: Optional[bool] = None


class EmployeeResponse(BaseModel):
    id: int
    first_name: str
    last_name: str
    ird_number: Optional[str] = None
    pay_type: str
    pay_rate: float = 0
    tax_code: str = "M"
    kiwisaver_enrolled: bool = False
    kiwisaver_rate: Decimal = Decimal("0.0300")
    student_loan: bool = False
    child_support: bool = False
    esct_rate: Decimal = Decimal("0.0000")
    pay_frequency: str = "fortnightly"
    is_active: bool = True
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    model_config = {"from_attributes": True}


class PayStubInput(BaseModel):
    employee_id: int
    hours: float = 0


class PayRunCreate(BaseModel):
    period_start: date
    period_end: date
    pay_date: date
    stubs: list[PayStubInput] = []


class PayStubResponse(BaseModel):
    id: int
    employee_id: int
    employee_name: Optional[str] = None
    hours: float = 0
    gross_pay: float = 0
    model_config = {"from_attributes": True}


class PayRunResponse(BaseModel):
    id: int
    period_start: date
    period_end: date
    pay_date: date
    status: str
    total_gross: float = 0
    total_net: float = 0
    total_taxes: float = 0
    stubs: list[PayStubResponse] = []
    model_config = {"from_attributes": True}
