from pydantic import BaseModel


class IIFImportResult(BaseModel):
    accounts: int = 0
    customers: int = 0
    vendors: int = 0
    items: int = 0
    invoices: int = 0
    payments: int = 0
    estimates: int = 0
    errors: list[dict] = []
    warnings: list[str] = []


class IIFValidationReport(BaseModel):
    valid: bool
    sections_found: list[str] = []
    record_counts: dict = {}
    warnings: list[str] = []
    errors: list[str] = []
