from datetime import date
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, field_validator


SUPPORTED_PAY_TYPES = {"hourly", "salary"}
SUPPORTED_PAY_FREQUENCIES = {"weekly", "fortnightly", "monthly"}
SUPPORTED_TAX_CODES = {
    "M", "ME", "M SL", "ME SL",
    "SB", "S", "SH", "ST", "SA",
    "SB SL", "S SL", "SH SL", "ST SL", "SA SL",
    "ND", "NSW",
}
SUPPORTED_KIWISAVER_RATES = {
    Decimal("0.0300"),
    Decimal("0.0350"),
    Decimal("0.0400"),
    Decimal("0.0600"),
    Decimal("0.0800"),
    Decimal("0.1000"),
}
SUPPORTED_ESCT_RATES = {
    Decimal("0.0000"),
    Decimal("0.1050"),
    Decimal("0.1750"),
    Decimal("0.3000"),
    Decimal("0.3300"),
    Decimal("0.3900"),
}


def _normalize_tax_code(value: str | None) -> str | None:
    if value is None:
        return None
    return " ".join(str(value).upper().split())


class EmployeeCreate(BaseModel):
    first_name: str
    last_name: str
    ird_number: Optional[str] = None
    pay_type: str = "hourly"
    pay_rate: float = 0
    tax_code: str = "M"
    kiwisaver_enrolled: bool = False
    kiwisaver_rate: Decimal = Decimal("0.0350")
    student_loan: bool = False
    child_support: bool = False
    child_support_amount: Decimal = Decimal("0.00")
    esct_rate: Decimal = Decimal("0.0000")
    pay_frequency: str = "fortnightly"
    address1: Optional[str] = None
    address2: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None

    @field_validator("pay_type")
    @classmethod
    def validate_pay_type(cls, value: str) -> str:
        value = str(value).lower()
        if value not in SUPPORTED_PAY_TYPES:
            raise ValueError("Unsupported pay type")
        return value

    @field_validator("pay_frequency")
    @classmethod
    def validate_pay_frequency(cls, value: str) -> str:
        value = str(value).lower()
        if value not in SUPPORTED_PAY_FREQUENCIES:
            raise ValueError("Unsupported pay frequency")
        return value

    @field_validator("tax_code")
    @classmethod
    def validate_tax_code(cls, value: str) -> str:
        value = _normalize_tax_code(value) or "M"
        if value not in SUPPORTED_TAX_CODES:
            raise ValueError("Unsupported NZ tax code")
        return value

    @field_validator("kiwisaver_rate")
    @classmethod
    def validate_kiwisaver_rate(cls, value: Decimal) -> Decimal:
        value = Decimal(str(value)).quantize(Decimal("0.0001"))
        if value not in SUPPORTED_KIWISAVER_RATES:
            raise ValueError("Unsupported KiwiSaver rate")
        return value

    @field_validator("esct_rate")
    @classmethod
    def validate_esct_rate(cls, value: Decimal) -> Decimal:
        value = Decimal(str(value)).quantize(Decimal("0.0001"))
        if value not in SUPPORTED_ESCT_RATES:
            raise ValueError("Unsupported ESCT rate")
        return value

    @field_validator("pay_rate")
    @classmethod
    def validate_pay_rate(cls, value: float) -> float:
        if value < 0:
            raise ValueError("Pay rate must be non-negative")
        return value

    @field_validator("child_support_amount")
    @classmethod
    def validate_child_support_amount(cls, value: Decimal) -> Decimal:
        value = Decimal(str(value)).quantize(Decimal("0.01"))
        if value < 0:
            raise ValueError("Child support amount must be non-negative")
        return value


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
    child_support_amount: Optional[Decimal] = None
    esct_rate: Optional[Decimal] = None
    pay_frequency: Optional[str] = None
    address1: Optional[str] = None
    address2: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    is_active: Optional[bool] = None

    @field_validator("pay_type")
    @classmethod
    def validate_pay_type(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        value = str(value).lower()
        if value not in SUPPORTED_PAY_TYPES:
            raise ValueError("Unsupported pay type")
        return value

    @field_validator("pay_frequency")
    @classmethod
    def validate_pay_frequency(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        value = str(value).lower()
        if value not in SUPPORTED_PAY_FREQUENCIES:
            raise ValueError("Unsupported pay frequency")
        return value

    @field_validator("tax_code")
    @classmethod
    def validate_tax_code(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        value = _normalize_tax_code(value)
        if value not in SUPPORTED_TAX_CODES:
            raise ValueError("Unsupported NZ tax code")
        return value

    @field_validator("kiwisaver_rate")
    @classmethod
    def validate_kiwisaver_rate(cls, value: Optional[Decimal]) -> Optional[Decimal]:
        if value is None:
            return value
        value = Decimal(str(value)).quantize(Decimal("0.0001"))
        if value not in SUPPORTED_KIWISAVER_RATES:
            raise ValueError("Unsupported KiwiSaver rate")
        return value

    @field_validator("esct_rate")
    @classmethod
    def validate_esct_rate(cls, value: Optional[Decimal]) -> Optional[Decimal]:
        if value is None:
            return value
        value = Decimal(str(value)).quantize(Decimal("0.0001"))
        if value not in SUPPORTED_ESCT_RATES:
            raise ValueError("Unsupported ESCT rate")
        return value

    @field_validator("pay_rate")
    @classmethod
    def validate_pay_rate(cls, value: Optional[float]) -> Optional[float]:
        if value is None:
            return value
        if value < 0:
            raise ValueError("Pay rate must be non-negative")
        return value

    @field_validator("child_support_amount")
    @classmethod
    def validate_child_support_amount(cls, value: Optional[Decimal]) -> Optional[Decimal]:
        if value is None:
            return value
        value = Decimal(str(value)).quantize(Decimal("0.01"))
        if value < 0:
            raise ValueError("Child support amount must be non-negative")
        return value


class EmployeeResponse(BaseModel):
    id: int
    first_name: str
    last_name: str
    ird_number: Optional[str] = None
    pay_type: str
    pay_rate: float = 0
    tax_code: str = "M"
    kiwisaver_enrolled: bool = False
    kiwisaver_rate: Decimal = Decimal("0.0350")
    student_loan: bool = False
    child_support: bool = False
    child_support_amount: Decimal = Decimal("0.00")
    esct_rate: Decimal = Decimal("0.0000")
    pay_frequency: str = "fortnightly"
    address1: Optional[str] = None
    address2: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip: Optional[str] = None
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
    tax_code: str
    hours: float = 0
    gross_pay: float = 0
    paye: float = 0
    acc_earners_levy: float = 0
    student_loan_deduction: float = 0
    kiwisaver_employee_deduction: float = 0
    employer_kiwisaver_contribution: float = 0
    esct: float = 0
    child_support_deduction: float = 0
    total_deductions: float = 0
    employer_kiwisaver_net: float = 0
    net_pay: float = 0
    model_config = {"from_attributes": True}


class PayRunResponse(BaseModel):
    id: int
    period_start: date
    period_end: date
    pay_date: date
    tax_year: int
    status: str
    total_gross: float = 0
    total_net: float = 0
    total_taxes: float = 0
    total_paye: float = 0
    total_acc_earners_levy: float = 0
    total_student_loan: float = 0
    total_kiwisaver_employee: float = 0
    total_employer_kiwisaver: float = 0
    total_esct: float = 0
    total_child_support: float = 0
    transaction_id: Optional[int] = None
    stubs: list[PayStubResponse] = []
    model_config = {"from_attributes": True}
