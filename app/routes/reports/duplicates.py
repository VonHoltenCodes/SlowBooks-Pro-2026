# ── Possible Duplicates (#414) ───────────────────────────────────────────
#
# Reading the report is open to every role; "not a duplicate" and putting
# a group back are writes (a read-only or accountant sign-in is refused by
# the RBAC middleware, as for any POST/DELETE outside its own). Voiding an
# entry is not here: the page calls the entry's own void route, which
# the report names per entry (void_path).

from datetime import date
from typing import Optional

from fastapi import Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.duplicate_dismissals import DuplicateDismissal
from app.routes.reports._router import router
from app.routes.reports.financial import (
    _company_name,
    _csv_download,
    _pdf_response,
    _range_or_year,
)
from app.schemas.common import StrictModel
from app.services import possible_duplicates as pd


def _report(db, account_id, start_date, end_date, include_dismissed):
    account = pd.require_register_account(db, account_id)
    start_date, end_date = _range_or_year(db, start_date, end_date)
    if start_date > end_date:
        raise HTTPException(status_code=400, detail="From is after To")
    return pd.possible_duplicates(db, account, start_date, end_date, include_dismissed)


def _filename(data, ext):
    label = data["account"]["number"] or str(data["account"]["id"])
    label = "".join(c for c in label if c.isalnum() or c in "-_") or "account"
    return f"possible-duplicates_{label}_{data['start_date']}_{data['end_date']}.{ext}"


@router.get("/possible-duplicates")
def possible_duplicates_route(
    account_id: Optional[int] = Query(default=None),
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    include_dismissed: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    """Groups of entries on a bank or card account with the same amount on
    the same side and the same or a similar payee, at most `window_days`
    apart (services/possible_duplicates.py). Groups kept as "not a
    duplicate" are left out unless `include_dismissed`."""
    return _report(db, account_id, start_date, end_date, include_dismissed)


@router.get("/possible-duplicates/csv")
def possible_duplicates_csv_route(
    request: Request,
    account_id: Optional[int] = Query(default=None),
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    include_dismissed: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    data = _report(db, account_id, start_date, end_date, include_dismissed)
    return _csv_download(
        pd.report_csv(data, _company_name(db)), _filename(data, "csv"), request
    )


@router.get("/possible-duplicates/pdf")
def possible_duplicates_pdf_route(
    account_id: Optional[int] = Query(default=None),
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    include_dismissed: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    data = _report(db, account_id, start_date, end_date, include_dismissed)
    return _pdf_response(
        [pd.report_section(data)], db, _filename(data, "pdf"), landscape=True
    )


class DismissGroup(StrictModel):
    account_id: int
    transaction_ids: list[int]


@router.post("/possible-duplicates/dismiss")
def dismiss_possible_duplicate(data: DismissGroup, db: Session = Depends(get_db)):
    """Not a duplicate: the group stays off the report (for everyone)."""
    account = pd.require_register_account(db, data.account_id)
    row = pd.dismiss(
        db, account, data.transaction_ids, db.info.get("acting_username") or "operator"
    )
    db.commit()
    return {
        "id": row.id,
        "account_id": row.account_id,
        "transaction_ids": [int(i) for i in row.transaction_ids.split(",")],
        "dismissed_by": row.dismissed_by,
    }


@router.delete("/possible-duplicates/dismissals/{dismissal_id}")
def restore_possible_duplicate(dismissal_id: int, db: Session = Depends(get_db)):
    """Put a dismissed group back on the report."""
    row = db.get(DuplicateDismissal, dismissal_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Dismissal not found")
    db.delete(row)
    db.commit()
    return {"id": dismissal_id, "restored": True}
