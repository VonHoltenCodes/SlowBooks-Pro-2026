from pydantic import BaseModel
from pydantic import Field
from typing import Literal


class QBOImportRunRequest(BaseModel):
    entities: (
        list[
            Literal[
                "accounts",
                "customers",
                "vendors",
                "items",
                "invoices",
                "payments",
                "sales_receipts",
                "journal_entries",
                "ledger",
            ]
        ]
        | None
    ) = Field(default=None, min_length=1, max_length=9)


class QBOImportResult(BaseModel):
    accounts: int = 0
    customers: int = 0
    vendors: int = 0
    items: int = 0
    invoices: int = 0
    payments: int = 0
    sales_receipts: int = 0
    journal_entries: int = 0
    ledger: int = 0
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
