from typing import Optional

from pydantic import BaseModel, Field

from app.schemas.common import StrictModel


class RuleCreate(StrictModel):
    days_from_due: int = Field(ge=-365, le=3650)
    enabled: bool = True
    subject: str = Field(max_length=500)
    body: str


class RuleUpdate(StrictModel):
    days_from_due: Optional[int] = Field(None, ge=-365, le=3650)
    enabled: Optional[bool] = None
    subject: Optional[str] = Field(None, max_length=500)
    body: Optional[str] = None


class RuleResponse(BaseModel):
    id: int
    days_from_due: int
    label: str
    enabled: bool
    subject: str
    body: str


# A send is always dated today (review of #654): neither body takes a date,
# and one sent is a 422, as every unknown field is.
class RunRequest(StrictModel):
    invoice_ids: Optional[list[int]] = None


class StatementRunRequest(StrictModel):
    force: bool = False
