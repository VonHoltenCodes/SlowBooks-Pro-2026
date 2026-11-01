"""Reclassify (#337): the lines on an account, and the move.

GET  /api/reclassify/lines   an account's lines in a period, each with
                             whether it can move and why not; vendor_id
                             or payee narrows them to one payee's (#394)
POST /api/reclassify         move the ticked lines to an account, a class
                             and/or a job (#674); dry_run (the default) answers with the
                             plan and changes nothing
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.common import StrictModel
from app.services import reclassify as svc

router = APIRouter(prefix="/api/reclassify", tags=["reclassify"])


class ReclassifyRequest(StrictModel):
    line_ids: list[int] = Field(..., min_length=1, max_length=5000)
    # the account the lines go to; None leaves the account as it is
    account_id: Optional[int] = None
    # the class they get; None leaves it; clear_class removes the line's own
    class_id: Optional[int] = None
    clear_class: bool = False
    # the job they go on (#674); None leaves it; clear_job takes it off
    job_id: Optional[int] = None
    clear_job: bool = False
    dry_run: bool = True


@router.get("/lines")
def lines(
    account_id: int = Query(...),
    start_date: date = Query(...),
    end_date: date = Query(...),
    vendor_id: Optional[int] = Query(None),
    payee: Optional[str] = Query(None, max_length=200),
    db: Session = Depends(get_db),
):
    return svc.lines_on_account(
        db, account_id, start_date, end_date, vendor_id=vendor_id, payee=payee
    )


@router.post("")
def reclassify(data: ReclassifyRequest, db: Session = Depends(get_db)):
    return svc.reclassify(
        db,
        data.line_ids,
        account_id=data.account_id,
        class_id=data.class_id,
        clear_class=data.clear_class,
        job_id=data.job_id,
        clear_job=data.clear_job,
        dry_run=data.dry_run,
    )
