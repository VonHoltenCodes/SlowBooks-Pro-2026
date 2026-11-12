"""Activity on every document (#894): GET /api/activity?type=<record>&id=<n>.

Each document's view shows what happened to it, newest first, in sentences
with who and when (app.services.activity). A read for every role, read-only
sign-ins and API tokens included: it writes nothing.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.activity import ActivityResponse
from app.services.activity import ActivityNotFound, document_activity

router = APIRouter(prefix="/api/activity", tags=["activity"])


@router.get("", response_model=ActivityResponse)
def get_activity(
    type: str = Query(..., max_length=40),
    id: int = Query(..., ge=1),
    tz_offset: Optional[int] = Query(default=None, ge=-900, le=900),
    count_only: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    """The document's events, newest first. `tz_offset` is the page's clock
    offset (JavaScript's getTimezoneOffset); `count_only` answers the count
    for the panel's closed heading without the rows."""
    try:
        found = document_activity(db, type, id, tz_offset)
    except ActivityNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    if count_only:
        found["events"] = []
    return found
