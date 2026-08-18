from pydantic import BaseModel


class QBOImportResult(BaseModel):
    accounts: int = 0
    customers: int = 0
    vendors: int = 0
    items: int = 0
    invoices: int = 0
    payments: int = 0
    sales_receipts: int = 0
    errors: list[dict] = []


class QBOExportResult(BaseModel):
    accounts: int = 0
    customers: int = 0
    vendors: int = 0
    items: int = 0
    invoices: int = 0
    payments: int = 0
    errors: list[dict] = []


class QBOConnectionStatus(BaseModel):
    connected: bool = False
    company_name: str = ""
    realm_id: str = ""
