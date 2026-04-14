from pydantic import BaseModel


class XeroImportVerificationSummary(BaseModel):
    trial_balance_ok: bool
    trial_balance_mismatches: list[str] = []
    profit_loss_ok: bool
    profit_loss_mismatches: list[str] = []
    balance_sheet_ok: bool
    balance_sheet_mismatches: list[str] = []


class XeroImportDryRunResponse(BaseModel):
    required_files: list[str]
    detected_files: dict[str, str]
    missing_files: list[str] = []
    counts: dict[str, int] = {}
    journal_groups: int = 0
    import_ready: bool = False
    errors: list[str] = []
    warnings: list[str] = []
    verification: XeroImportVerificationSummary


class XeroImportExecuteResponse(BaseModel):
    imported_accounts: int
    imported_transactions: int
    imported_transaction_lines: int
    verification: XeroImportVerificationSummary
