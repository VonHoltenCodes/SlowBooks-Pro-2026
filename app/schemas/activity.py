"""The Activity panel's rows (#894)."""

from pydantic import BaseModel


class ActivityEvent(BaseModel):
    when: str  # ISO 8601, UTC
    when_local: str  # "YYYY-MM-DD HH:MM" on the company's clock
    who: str
    kind: str  # created, edited, voided, emailed, attachment, status, ...
    text: str  # the sentence
    details: list[str] = []  # one line per field changed, line added, ...


class ActivityResponse(BaseModel):
    type: str
    id: int
    count: int
    events: list[ActivityEvent]
