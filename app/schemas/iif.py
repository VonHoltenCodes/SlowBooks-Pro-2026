from pydantic import BaseModel


class IIFImportResult(BaseModel):
    classes: int = 0
    accounts: int = 0
    customers: int = 0
    vendors: int = 0
    items: int = 0
    invoices: int = 0
    payments: int = 0
    sales_receipts: int = 0
    estimates: int = 0
    bills: int = 0
    deposits: int = 0
    duplicates_skipped: int = 0
    errors: list[dict] = []
    warnings: list[str] = []


class IIFValidationReport(BaseModel):
    valid: bool
    sections_found: list[str] = []
    record_counts: dict = {}
    warnings: list[str] = []
    errors: list[str] = []
