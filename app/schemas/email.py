from datetime import date
from typing import Optional

from pydantic import BaseModel


class DocumentEmailRequest(BaseModel):
    recipient: str
    subject: Optional[str] = None


class StatementEmailRequest(DocumentEmailRequest):
    as_of_date: Optional[date] = None
