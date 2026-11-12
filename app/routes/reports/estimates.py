"""Estimate Closing Rate (#795): won, lost and open estimates over a period,
by count and by dollars, per customer and per job type, with the days from
sent to decision. A JSON route, a PDF through the shared report renderer and
a CSV through the shared CSV helper, like the construction grids. Every
company mode has estimates, so it is not gated. The math is in
app/services/estimate_closing_report.py.
"""

import io
from datetime import date
from typing import Annotated, Optional

from fastapi import Depends, Query, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.routes._helpers import refuse_unknown_filters
from app.routes.reports._router import router
from app.routes.reports.construction import _csv_cell, _pdf_cell
from app.routes.reports.financial import _company_name, _csv_download, _pdf_response
from app.services.csv_export import _SafeWriter
from app.services.estimate_closing_report import estimate_closing_report
from app.services.ledger_exports import _preamble

_CUST_Q = Annotated[Optional[int], Query(description="Only this customer's estimates")]
_TYPE_Q = Annotated[
    Optional[str],
    Query(description='Only this job type; "" for estimates with no job type'),
]

_TITLE = "Estimate Closing Rate"


@router.get("/estimate-closing-rate", dependencies=[Depends(refuse_unknown_filters)])
def estimate_closing_rate(
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    db: Session = Depends(get_db),
    customer_id: _CUST_Q = None,
    job_type: _TYPE_Q = None,
):
    """Estimates dated in the period: sent, won (accepted or converted), lost
    and open, by count and by dollars, with the closing rate (won ÷ won +
    lost) and the average days from sent to decision; in total, per customer
    and per job type. Reading it posts nothing."""
    return estimate_closing_report(db, start_date, end_date, customer_id, job_type)


def _period_words(data: dict) -> str:
    return f"{data['start_date']} — {data['end_date']}"


def _grids(data: dict) -> list[tuple[str, list[dict]]]:
    """The two grids, each with the grand total as its last row."""
    total = {"name": "Total", **data["totals"]}
    return [
        ("By customer", data["by_customer"] + [total]),
        ("By job type", data["by_job_type"] + [total]),
    ]


@router.get(
    "/estimate-closing-rate/pdf", dependencies=[Depends(refuse_unknown_filters)]
)
def estimate_closing_rate_pdf(
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    db: Session = Depends(get_db),
    customer_id: _CUST_Q = None,
    job_type: _TYPE_Q = None,
):
    data = estimate_closing_report(db, start_date, end_date, customer_id, job_type)
    cols = data["columns"]
    sections = []
    for title, rows in _grids(data):
        sections.append(
            {
                "title": f"{_TITLE}: {title.lower()}",
                "period": _period_words(data),
                "columns": [c["label"] for c in cols],
                "text_columns": 1,
                "rows": [
                    {
                        "cells": [_pdf_cell(r[c["key"]], c["kind"]) for c in cols],
                        **({"style": "grand-total"} if r is rows[-1] else {}),
                    }
                    for r in rows
                ],
            }
        )
    return _pdf_response(
        sections,
        db,
        f"estimate-closing-rate_{data['start_date']}_{data['end_date']}.pdf",
        landscape=True,
    )


@router.get(
    "/estimate-closing-rate/csv", dependencies=[Depends(refuse_unknown_filters)]
)
def estimate_closing_rate_csv(
    request: Request,
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    db: Session = Depends(get_db),
    customer_id: _CUST_Q = None,
    job_type: _TYPE_Q = None,
):
    data = estimate_closing_report(db, start_date, end_date, customer_id, job_type)
    cols = data["columns"]
    out = io.StringIO()
    w = _SafeWriter(out)
    _preamble(w, _company_name(db), _TITLE, _period_words(data))
    for title, rows in _grids(data):
        w.writerow([title])
        w.writerow([c["label"] for c in cols])
        for r in rows:
            w.writerow([_csv_cell(r[c["key"]], c["kind"]) for c in cols])
        w.writerow([])
    return _csv_download(
        out.getvalue(),
        f"estimate-closing-rate_{data['start_date']}_{data['end_date']}.csv",
        request,
    )
