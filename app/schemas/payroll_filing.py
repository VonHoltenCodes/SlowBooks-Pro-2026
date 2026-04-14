from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class PayrollFilingAuditResponse(BaseModel):
    id: int
    filing_type: str
    status: str
    employee_id: Optional[int] = None
    pay_run_id: Optional[int] = None
    export_filename: str
    export_reference: Optional[str] = None
    notes: Optional[str] = None
    generated_by_user_id: Optional[int] = None
    status_updated_by_user_id: Optional[int] = None
    generated_at: Optional[datetime] = None
    status_updated_at: Optional[datetime] = None
    changed_since_source: bool = False


class PayrollFilingAuditStatusUpdate(BaseModel):
    status: str
    reference: Optional[str] = None
    notes: Optional[str] = None
