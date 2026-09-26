"""Donor reports: the pledge report (JSON, PDF, CSV)."""

from datetime import date
from typing import Optional

from fastapi import Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.routes.reports._router import router
from app.routes.reports.financial import _money, _pdf_response
from app.services.csv_export import _SafeWriter
from app.services.pledge_report import pledge_report

_COLS = (
    "pledged",
    "invoiced",
    "not_yet_invoiced",
    "received",
    "written_off",
    "outstanding",
)
_HEAD = (
    "Pledged",
    "Invoiced",
    "Not yet invoiced",
    "Received",
    "Written off",
    "Outstanding",
)


def _period(start_date, end_date):
    if not start_date:
        start_date = date(date.today().year, 1, 1)
    if not end_date:
        end_date = date.today()
    return start_date, end_date


@router.get("/pledges")
def pledges(
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    class_id: Optional[int] = None,
    customer_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    start_date, end_date = _period(start_date, end_date)
    return pledge_report(db, start_date, end_date, class_id, customer_id)


def _pledges_section(data: dict) -> dict:
    rows = []
    for d in data["by_donor"]:
        rows.append(
            {
                "cells": [d["customer_name"]] + [_money(d[c]) for c in _COLS],
                "style": "subtotal",
            }
        )
        for p in d["pledges"]:
            rows.append(
                {
                    "cells": [f"  {p['label']} ({p['class_name']})"]
                    + [_money(p[c]) for c in _COLS]
                }
            )
    rows.append(
        {
            "cells": ["Total"] + [_money(data["totals"][c]) for c in _COLS],
            "style": "grand-total",
        }
    )
    return {
        "title": "Pledge Report",
        "period": f"{data['start_date']} — {data['end_date']}",
        "columns": ["Donor / pledge", *_HEAD],
        "rows": rows,
    }


@router.get("/pledges/pdf")
def pledges_pdf(
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    db: Session = Depends(get_db),
):
    start_date, end_date = _period(start_date, end_date)
    data = pledge_report(db, start_date, end_date)
    return _pdf_response(
        [_pledges_section(data)], db, f"pledge-report_{start_date}_{end_date}.pdf"
    )


@router.get("/pledges/csv")
def pledges_csv(
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    db: Session = Depends(get_db),
):
    import io

    start_date, end_date = _period(start_date, end_date)
    data = pledge_report(db, start_date, end_date)
    buf = io.StringIO()
    w = _SafeWriter(buf)
    w.writerow(["Donor", "Pledge", "Campaign", *_HEAD])
    for d in data["by_donor"]:
        for p in d["pledges"]:
            w.writerow(
                [d["customer_name"], p["label"], p["class_name"]]
                + [f"{p[c]:.2f}" for c in _COLS]
            )
    w.writerow(["Total", "", ""] + [f"{data['totals'][c]:.2f}" for c in _COLS])
    from app.routes.csv import _csv_response

    return _csv_response(buf.getvalue(), f"pledge-report_{start_date}_{end_date}.csv")
