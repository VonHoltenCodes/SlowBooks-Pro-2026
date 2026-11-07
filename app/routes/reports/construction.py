"""Construction job reports (#556): the WIP schedule with over/under billing,
cost-to-complete, the change-order log, the buyout report and job cost by
vendor. Each is a plain grid with a JSON route, a PDF through the shared report
renderer and a CSV through the shared CSV helper, like P&L by Job.

The service math is pure in app/services/job_reports.py; this module turns it
into the routes, the printed grids and the exports. It also gives the existing
Job Profitability and Job Budget vs Actual reports the PDF/CSV routes they
lacked, on the same renderer.
"""

import io
from datetime import date
from typing import Annotated, Optional

from fastapi import Depends, Query, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.routes.reports._router import router
from app.routes.reports.financial import (
    _company_name,
    _csv_download,
    _pdf_response,
    _period,
)
from app.services.csv_export import _SafeWriter
from app.services.fiscal_year import fiscal_year_start
from app.services.job_reports import (
    buyout_report,
    change_order_log,
    commitment_status,
    cost_to_complete,
    ctc_forecast_report,
    job_cost_by_vendor,
    wip_schedule,
)
from app.services.ledger_exports import _Percent, _money, _pdf_money, _preamble

# ── shared grid formatting ─────────────────────────────────────────────────


def _pdf_cell(value, kind: str) -> str:
    if value is None or value == "":
        return ""
    if kind == "money":
        return _pdf_money(value)
    if kind == "pct":
        return f"{float(value):.1f}%"
    if kind == "int":
        return str(int(value))
    if kind == "num":
        # a count that can be fractional (a bill split across jobs): trim zeros
        return f"{float(value):g}"
    return str(value)


def _csv_cell(value, kind: str):
    if value is None or value == "":
        return ""
    if kind == "money":
        return _money(value)
    if kind == "pct":
        return _Percent(float(value))
    if kind == "int":
        return int(value)
    if kind == "num":
        return float(value)
    return value


def _text_columns(columns: list[dict]) -> int:
    """How many columns from the left are words (the report_pdf.html hint):
    the leading text columns before the first number."""
    n = 0
    for col in columns:
        if col["kind"] == "text":
            n += 1
        else:
            break
    return max(n, 1)


_STATUS_Q = Annotated[
    Optional[str],
    Query(description="One job status; default leaves out closed and not-awarded"),
]
_JOB_Q = Annotated[Optional[int], Query(description="Only this job")]
_CUST_Q = Annotated[Optional[int], Query(description="Only this customer's jobs")]
_CLOSED_Q = Annotated[bool, Query(description="Include closed jobs")]


# ── 1. WIP schedule ─────────────────────────────────────────────────────────


@router.get("/wip-schedule")
def wip_schedule_route(
    as_of: date = Query(default=None),
    db: Session = Depends(get_db),
    job_id: _JOB_Q = None,
    customer_id: _CUST_Q = None,
    status: _STATUS_Q = None,
    include_closed: _CLOSED_Q = False,
):
    """The work-in-progress schedule (cost-to-cost): one row per job with a
    contract, over and under billing, retainage, cost to complete (from the
    cost-to-complete forecast) and any projected loss. `adjusting_entry` is the
    entry with amounts, per job and net, between the WIP accounts named in
    `adjustment_accounts`. It posts nothing."""
    return wip_schedule(db, as_of, job_id, customer_id, status, include_closed)


def _warnings_note(data: dict):
    """The report's warnings as one note line under the table (None if none)."""
    return " ".join(data.get("warnings") or []) or None


def _wip_sections(data: dict) -> list[dict]:
    cols = data["columns"]
    tc = _text_columns(cols)
    rows = []
    for r in data["rows"]:
        rows.append({"cells": [_pdf_cell(r[c["key"]], c["kind"]) for c in cols]})
    totals = data["totals"]
    total_cells = []
    for i, c in enumerate(cols):
        if i == 0:
            total_cells.append("Total")
        elif c["key"] in totals:
            total_cells.append(_pdf_cell(totals[c["key"]], c["kind"]))
        else:
            total_cells.append("")
    rows.append({"cells": total_cells, "style": "grand-total"})
    return [
        {
            "title": "WIP Schedule",
            "period": f"As of {data['as_of']} · cost-to-cost",
            "columns": [c["label"] for c in cols],
            "text_columns": tc,
            "rows": rows,
            "note": _warnings_note(data),
        }
    ]


_ENTRY_COLUMNS = ["Job", "Account", "Debit", "Credit"]


def _wip_entry_sections(data: dict) -> list[dict]:
    """The adjusting entry as a second printed grid: each job's lines, then the
    net entry to post."""
    entry = data["adjusting_entry"]
    rows = []
    for job in entry["jobs"]:
        for i, ln in enumerate(job["lines"]):
            rows.append(
                {
                    "cells": [
                        job["job_name"] if i == 0 else "",
                        ln["account"],
                        _pdf_cell(ln["debit"] or None, "money"),
                        _pdf_cell(ln["credit"] or None, "money"),
                    ]
                }
            )
    for i, ln in enumerate(entry["lines"]):
        rows.append(
            {
                "cells": [
                    "Net entry" if i == 0 else "",
                    ln["account"],
                    _pdf_cell(ln["debit"] or None, "money"),
                    _pdf_cell(ln["credit"] or None, "money"),
                ],
                "style": "grand-total" if i == 0 else "subtotal",
            }
        )
    if entry["lines"]:
        rows.append(
            {
                "cells": [
                    "",
                    "Total",
                    _pdf_cell(entry["total_debits"], "money"),
                    _pdf_cell(entry["total_credits"], "money"),
                ],
                "style": "grand-total",
            }
        )
    return [
        {
            "title": "WIP Adjusting Entry",
            "period": f"As of {data['as_of']} · computed, not posted",
            "columns": _ENTRY_COLUMNS,
            "text_columns": 2,
            "rows": rows,
        }
    ]


def _wip_entry_csv_rows(data: dict, w) -> None:
    entry = data["adjusting_entry"]
    w.writerow([])
    w.writerow(["WIP Adjusting Entry (computed, not posted)"])
    w.writerow(_ENTRY_COLUMNS)
    for job in entry["jobs"]:
        for ln in job["lines"]:
            w.writerow(
                [
                    job["job_name"],
                    ln["account"],
                    _csv_cell(ln["debit"] or None, "money"),
                    _csv_cell(ln["credit"] or None, "money"),
                ]
            )
    for ln in entry["lines"]:
        w.writerow(
            [
                "Net entry",
                ln["account"],
                _csv_cell(ln["debit"] or None, "money"),
                _csv_cell(ln["credit"] or None, "money"),
            ]
        )
    w.writerow(
        [
            "",
            "Total",
            _csv_cell(entry["total_debits"], "money"),
            _csv_cell(entry["total_credits"], "money"),
        ]
    )


def _grid_csv(data: dict, company: str, report: str, period: str, extra=None) -> str:
    """A plain grid CSV: the preamble, the column header, a row per data row and
    a Total row."""
    cols = data["columns"]
    out = io.StringIO()
    w = _SafeWriter(out)
    _preamble(w, company, report, period)
    w.writerow([c["label"] for c in cols])
    for r in data["rows"]:
        w.writerow([_csv_cell(r[c["key"]], c["kind"]) for c in cols])
    totals = data["totals"]
    row = []
    for i, c in enumerate(cols):
        if i == 0:
            row.append("Total")
        elif c["key"] in totals:
            row.append(_csv_cell(totals[c["key"]], c["kind"]))
        else:
            row.append("")
    w.writerow(row)
    for text in data.get("warnings") or []:
        w.writerow([text])
    if extra is not None:
        extra(data, w)
    return out.getvalue()


@router.get("/wip-schedule/pdf")
def wip_schedule_pdf(
    as_of: date = Query(default=None),
    db: Session = Depends(get_db),
    job_id: _JOB_Q = None,
    customer_id: _CUST_Q = None,
    status: _STATUS_Q = None,
    include_closed: _CLOSED_Q = False,
):
    data = wip_schedule(db, as_of, job_id, customer_id, status, include_closed)
    return _pdf_response(
        _wip_sections(data) + _wip_entry_sections(data),
        db,
        f"wip-schedule_{data['as_of']}.pdf",
        landscape=True,
    )


@router.get("/wip-schedule/csv")
def wip_schedule_csv(
    request: Request,
    as_of: date = Query(default=None),
    db: Session = Depends(get_db),
    job_id: _JOB_Q = None,
    customer_id: _CUST_Q = None,
    status: _STATUS_Q = None,
    include_closed: _CLOSED_Q = False,
):
    data = wip_schedule(db, as_of, job_id, customer_id, status, include_closed)
    text = _grid_csv(
        data,
        _company_name(db),
        "WIP Schedule",
        f"As of {data['as_of']} (cost-to-cost)",
        extra=_wip_entry_csv_rows,
    )
    return _csv_download(text, f"wip-schedule_{data['as_of']}.csv", request)


# ── 2. Cost to complete ───────────────────────────────────────────────────


@router.get("/cost-to-complete")
def cost_to_complete_route(
    as_of: date = Query(default=None),
    db: Session = Depends(get_db),
    job_id: _JOB_Q = None,
    customer_id: _CUST_Q = None,
    status: _STATUS_Q = None,
    include_closed: _CLOSED_Q = False,
):
    """Cost to complete per job, per cost code (division › section): budget,
    committed, actual, cost to complete, projected final and variance. The JSON
    keeps the tree (`children`); the PDF/CSV flatten it with the code path."""
    return cost_to_complete(db, as_of, job_id, customer_id, status, include_closed)


def _ctc_flatten(node: dict, depth: int, cols: list[dict], rows: list):
    label = node["label"]
    cells = [label]
    for c in cols[1:]:
        cells.append(_pdf_cell(node["figures"][c["key"]], c["kind"]))
    rows.append({"cells": cells, "indent": depth})
    for ch in node.get("children", []):
        _ctc_flatten(ch, depth + 1, cols, rows)


def _ctc_sections(data: dict) -> list[dict]:
    cols = data["columns"]
    rows = []
    for job in data["jobs"]:
        rows.append(
            {"cells": [job["job_name"]] + [""] * (len(cols) - 1), "style": "subtotal"}
        )
        for node in job["tree"]:
            _ctc_flatten(node, 1, cols, rows)
        if job.get("uncoded"):
            cells = ["Uncoded"] + [
                _pdf_cell(job["uncoded"][c["key"]], c["kind"]) for c in cols[1:]
            ]
            rows.append({"cells": cells, "indent": 1})
        t = job["totals"]
        cells = [f"Total {job['job_name']}"] + [
            _pdf_cell(t[c["key"]], c["kind"]) for c in cols[1:]
        ]
        rows.append({"cells": cells, "style": "subtotal"})
    gt = data["totals"]
    cells = ["Grand total"] + [_pdf_cell(gt[c["key"]], c["kind"]) for c in cols[1:]]
    rows.append({"cells": cells, "style": "grand-total"})
    return [
        {
            "title": "Cost to Complete",
            "period": f"As of {data['as_of']}",
            "columns": [c["label"] for c in cols],
            "text_columns": 1,
            "rows": rows,
        }
    ]


def _ctc_csv(data: dict, company: str) -> str:
    cols = data["columns"]
    out = io.StringIO()
    w = _SafeWriter(out)
    _preamble(w, company, "Cost to Complete", f"As of {data['as_of']}")
    w.writerow(["Job"] + [c["label"] for c in cols])
    for job in data["jobs"]:
        for node in job["tree"]:
            _ctc_csv_job_node(node, job["job_name"], cols, w)
        if job.get("uncoded"):
            w.writerow(
                [job["job_name"], "Uncoded"]
                + [_csv_cell(job["uncoded"][c["key"]], c["kind"]) for c in cols[1:]]
            )
        t = job["totals"]
        w.writerow(
            [job["job_name"], f"Total {job['job_name']}"]
            + [_csv_cell(t[c["key"]], c["kind"]) for c in cols[1:]]
        )
    gt = data["totals"]
    w.writerow(
        ["", "Grand total"] + [_csv_cell(gt[c["key"]], c["kind"]) for c in cols[1:]]
    )
    return out.getvalue()


def _ctc_csv_job_node(node: dict, job_name: str, cols: list[dict], w, prefix: str = ""):
    path = f"{prefix}{node['label']}"
    w.writerow(
        [job_name, path]
        + [_csv_cell(node["figures"][c["key"]], c["kind"]) for c in cols[1:]]
    )
    for ch in node.get("children", []):
        _ctc_csv_job_node(ch, job_name, cols, w, prefix + "  ")


@router.get("/cost-to-complete/pdf")
def cost_to_complete_pdf(
    as_of: date = Query(default=None),
    db: Session = Depends(get_db),
    job_id: _JOB_Q = None,
    customer_id: _CUST_Q = None,
    status: _STATUS_Q = None,
    include_closed: _CLOSED_Q = False,
):
    data = cost_to_complete(db, as_of, job_id, customer_id, status, include_closed)
    return _pdf_response(
        _ctc_sections(data), db, f"cost-to-complete_{data['as_of']}.pdf", landscape=True
    )


@router.get("/cost-to-complete/csv")
def cost_to_complete_csv(
    request: Request,
    as_of: date = Query(default=None),
    db: Session = Depends(get_db),
    job_id: _JOB_Q = None,
    customer_id: _CUST_Q = None,
    status: _STATUS_Q = None,
    include_closed: _CLOSED_Q = False,
):
    data = cost_to_complete(db, as_of, job_id, customer_id, status, include_closed)
    return _csv_download(
        _ctc_csv(data, _company_name(db)),
        f"cost-to-complete_{data['as_of']}.csv",
        request,
    )


# ── 2b. Cost-to-complete forecast ─────────────────────────────────────────


@router.get("/cost-to-complete-forecast")
def cost_to_complete_forecast_route(
    as_of: date = Query(default=None),
    db: Session = Depends(get_db),
    job_id: _JOB_Q = None,
    customer_id: _CUST_Q = None,
    status: _STATUS_Q = None,
    include_closed: _CLOSED_Q = False,
):
    """The cost-to-complete forecast the WIP schedule reads: per job, section
    and cost type, the budget, actual, the default (budget less actual, floored
    at zero), any amount entered and the cost to complete that counts. Enter
    amounts with PUT /api/jobs/{id}/cost-to-complete-forecast."""
    return ctc_forecast_report(db, as_of, job_id, customer_id, status, include_closed)


@router.get("/cost-to-complete-forecast/pdf")
def cost_to_complete_forecast_pdf(
    as_of: date = Query(default=None),
    db: Session = Depends(get_db),
    job_id: _JOB_Q = None,
    customer_id: _CUST_Q = None,
    status: _STATUS_Q = None,
    include_closed: _CLOSED_Q = False,
):
    data = ctc_forecast_report(db, as_of, job_id, customer_id, status, include_closed)
    return _pdf_response(
        _per_job_grid_sections(
            data, "Cost-to-Complete Forecast", f"As of {data['as_of']}"
        ),
        db,
        f"cost-to-complete-forecast_{data['as_of']}.pdf",
        landscape=True,
    )


@router.get("/cost-to-complete-forecast/csv")
def cost_to_complete_forecast_csv(
    request: Request,
    as_of: date = Query(default=None),
    db: Session = Depends(get_db),
    job_id: _JOB_Q = None,
    customer_id: _CUST_Q = None,
    status: _STATUS_Q = None,
    include_closed: _CLOSED_Q = False,
):
    data = ctc_forecast_report(db, as_of, job_id, customer_id, status, include_closed)
    return _csv_download(
        _per_job_grid_csv(
            data,
            _company_name(db),
            "Cost-to-Complete Forecast",
            f"As of {data['as_of']}",
        ),
        f"cost-to-complete-forecast_{data['as_of']}.csv",
        request,
    )


# ── 6. Commitment status ──────────────────────────────────────────────────


@router.get("/commitment-status")
def commitment_status_route(
    as_of: date = Query(default=None),
    db: Session = Depends(get_db),
    job_id: _JOB_Q = None,
    customer_id: _CUST_Q = None,
    status: _STATUS_Q = None,
    include_closed: _CLOSED_Q = False,
):
    """Each commitment (PO or subcontract) per job: committed, billed to date,
    retainage held and remaining, with totals per job and grand. As of
    `as_of`, only commitments and bills dated by then count."""
    return commitment_status(db, as_of, job_id, customer_id, status, include_closed)


@router.get("/commitment-status/pdf")
def commitment_status_pdf(
    as_of: date = Query(default=None),
    db: Session = Depends(get_db),
    job_id: _JOB_Q = None,
    customer_id: _CUST_Q = None,
    status: _STATUS_Q = None,
    include_closed: _CLOSED_Q = False,
):
    data = commitment_status(db, as_of, job_id, customer_id, status, include_closed)
    return _pdf_response(
        _per_job_grid_sections(data, "Commitment Status", f"As of {data['as_of']}"),
        db,
        f"commitment-status_{data['as_of']}.pdf",
        landscape=True,
    )


@router.get("/commitment-status/csv")
def commitment_status_csv(
    request: Request,
    as_of: date = Query(default=None),
    db: Session = Depends(get_db),
    job_id: _JOB_Q = None,
    customer_id: _CUST_Q = None,
    status: _STATUS_Q = None,
    include_closed: _CLOSED_Q = False,
):
    data = commitment_status(db, as_of, job_id, customer_id, status, include_closed)
    return _csv_download(
        _per_job_grid_csv(
            data, _company_name(db), "Commitment Status", f"As of {data['as_of']}"
        ),
        f"commitment-status_{data['as_of']}.csv",
        request,
    )


# ── 3. Change-order log ───────────────────────────────────────────────────


@router.get("/change-order-log")
def change_order_log_route(
    as_of: date = Query(default=None),
    db: Session = Depends(get_db),
    job_id: _JOB_Q = None,
    customer_id: _CUST_Q = None,
    status: _STATUS_Q = None,
    include_closed: _CLOSED_Q = False,
):
    """Every change order per job — number, kind, status, title, date, funding,
    cost, markup, sell, the running approved total (as of `as_of`) and the
    contract it builds — with pending totals and counts by status."""
    return change_order_log(db, as_of, job_id, customer_id, status, include_closed)


def _co_period(data: dict) -> str:
    return f"As of {data['as_of']}"


def _co_sections(data: dict) -> list[dict]:
    cols = data["columns"]
    out_rows = []
    for job in data["jobs"]:
        c = job["contract"]
        contract_line = (
            f"{job['job_name']} — contract "
            f"{_pdf_money(c['original'] or 0)} + {_pdf_money(c['approved_changes'] or 0)}"
            f" = {_pdf_money(c['revised'] or 0)}"
        )
        out_rows.append(
            {
                "cells": [contract_line] + [""] * (len(cols) - 1),
                "style": "subtotal",
            }
        )
        for r in job["rows"]:
            out_rows.append(
                {"cells": [_pdf_cell(r[col["key"]], col["kind"]) for col in cols]}
            )
    counts = data["totals"]["counts"]
    count_text = ", ".join(f"{k}: {v}" for k, v in sorted(counts.items())) or "none"
    out_rows.append(
        {
            "cells": [
                f"Pending {_pdf_money(data['totals']['pending_total'])} · {count_text}"
            ]
            + [""] * (len(cols) - 1),
            "style": "grand-total",
        }
    )
    return [
        {
            "title": "Change Order Log",
            "period": _co_period(data),
            "columns": [c["label"] for c in cols],
            "text_columns": _text_columns(cols),
            "rows": out_rows,
        }
    ]


def _co_csv(data: dict, company: str) -> str:
    cols = data["columns"]
    out = io.StringIO()
    w = _SafeWriter(out)
    _preamble(w, company, "Change Order Log", _co_period(data))
    w.writerow(["Job"] + [c["label"] for c in cols])
    for job in data["jobs"]:
        c = job["contract"]
        # The contract the change orders build, in a labelled row of its own.
        w.writerow(
            [
                job["job_name"],
                "Contract (original + approved changes = revised)",
                _money(c["original"] or 0),
                _money(c["approved_changes"] or 0),
                _money(c["revised"] or 0),
            ]
        )
        for r in job["rows"]:
            w.writerow(
                [job["job_name"]]
                + [_csv_cell(r[col["key"]], col["kind"]) for col in cols]
            )
    counts = data["totals"]["counts"]
    count_text = "; ".join(f"{k}: {v}" for k, v in sorted(counts.items()))
    w.writerow(["", "Pending total", _money(data["totals"]["pending_total"])])
    w.writerow(["", "Counts", count_text])
    return out.getvalue()


@router.get("/change-order-log/pdf")
def change_order_log_pdf(
    as_of: date = Query(default=None),
    db: Session = Depends(get_db),
    job_id: _JOB_Q = None,
    customer_id: _CUST_Q = None,
    status: _STATUS_Q = None,
    include_closed: _CLOSED_Q = False,
):
    data = change_order_log(db, as_of, job_id, customer_id, status, include_closed)
    return _pdf_response(_co_sections(data), db, "change-order-log.pdf", landscape=True)


@router.get("/change-order-log/csv")
def change_order_log_csv(
    request: Request,
    as_of: date = Query(default=None),
    db: Session = Depends(get_db),
    job_id: _JOB_Q = None,
    customer_id: _CUST_Q = None,
    status: _STATUS_Q = None,
    include_closed: _CLOSED_Q = False,
):
    data = change_order_log(db, as_of, job_id, customer_id, status, include_closed)
    return _csv_download(
        _co_csv(data, _company_name(db)), "change-order-log.csv", request
    )


# ── 4. Buyout report ──────────────────────────────────────────────────────


@router.get("/buyout")
def buyout_route(
    as_of: date = Query(default=None),
    db: Session = Depends(get_db),
    job_id: _JOB_Q = None,
    customer_id: _CUST_Q = None,
    status: _STATUS_Q = None,
    include_closed: _CLOSED_Q = False,
):
    """The buyout report: each job's trade packages — bid cost, bought,
    variance, committed-remaining and billed-to-date, with the commitments
    behind each — the Unassigned row kept, totals per job and grand. As of
    `as_of`, only commitments dated by then count and billing is the bills
    dated by then."""
    return buyout_report(db, as_of, job_id, customer_id, status, include_closed)


def _per_job_grid_sections(data: dict, title: str, period: str = "") -> list[dict]:
    """A per-job grid (buyout, job cost by vendor): a job header, its rows, a
    job total, then a grand total."""
    cols = data["columns"]
    out_rows = []
    for job in data["jobs"]:
        out_rows.append(
            {"cells": [job["job_name"]] + [""] * (len(cols) - 1), "style": "subtotal"}
        )
        for r in job["rows"]:
            out_rows.append(
                {"cells": [_pdf_cell(r[c["key"]], c["kind"]) for c in cols]}
            )
        t = job["totals"]
        cells = [f"Total {job['job_name']}"] + [
            _pdf_cell(t[c["key"]], c["kind"]) if c["key"] in t else "" for c in cols[1:]
        ]
        out_rows.append({"cells": cells, "style": "subtotal"})
    gt = data["totals"]
    cells = ["Grand total"] + [
        _pdf_cell(gt[c["key"]], c["kind"]) if c["key"] in gt else "" for c in cols[1:]
    ]
    out_rows.append({"cells": cells, "style": "grand-total"})
    return [
        {
            "title": title,
            "period": period,
            "columns": [c["label"] for c in cols],
            "text_columns": _text_columns(cols),
            "rows": out_rows,
        }
    ]


def _per_job_grid_csv(data: dict, company: str, report: str, period: str = "") -> str:
    cols = data["columns"]
    out = io.StringIO()
    w = _SafeWriter(out)
    _preamble(w, company, report, period)
    w.writerow(["Job"] + [c["label"] for c in cols])
    for job in data["jobs"]:
        for r in job["rows"]:
            w.writerow(
                [job["job_name"]] + [_csv_cell(r[c["key"]], c["kind"]) for c in cols]
            )
        t = job["totals"]
        w.writerow(
            [job["job_name"], f"Total {job['job_name']}"]
            + [
                _csv_cell(t[c["key"]], c["kind"]) if c["key"] in t else ""
                for c in cols[1:]
            ]
        )
    gt = data["totals"]
    w.writerow(
        ["", "Grand total"]
        + [
            _csv_cell(gt[c["key"]], c["kind"]) if c["key"] in gt else ""
            for c in cols[1:]
        ]
    )
    return out.getvalue()


@router.get("/buyout/pdf")
def buyout_pdf(
    as_of: date = Query(default=None),
    db: Session = Depends(get_db),
    job_id: _JOB_Q = None,
    customer_id: _CUST_Q = None,
    status: _STATUS_Q = None,
    include_closed: _CLOSED_Q = False,
):
    data = buyout_report(db, as_of, job_id, customer_id, status, include_closed)
    return _pdf_response(
        _per_job_grid_sections(data, "Buyout Report", f"As of {data['as_of']}"),
        db,
        "buyout.pdf",
        landscape=True,
    )


@router.get("/buyout/csv")
def buyout_csv(
    request: Request,
    as_of: date = Query(default=None),
    db: Session = Depends(get_db),
    job_id: _JOB_Q = None,
    customer_id: _CUST_Q = None,
    status: _STATUS_Q = None,
    include_closed: _CLOSED_Q = False,
):
    data = buyout_report(db, as_of, job_id, customer_id, status, include_closed)
    return _csv_download(
        _per_job_grid_csv(
            data, _company_name(db), "Buyout Report", f"As of {data['as_of']}"
        ),
        "buyout.csv",
        request,
    )


# ── 5. Job cost by vendor ─────────────────────────────────────────────────


@router.get("/job-cost-by-vendor")
def job_cost_by_vendor_route(
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    db: Session = Depends(get_db),
    job_id: _JOB_Q = None,
    customer_id: _CUST_Q = None,
    status: _STATUS_Q = None,
    include_closed: _CLOSED_Q = False,
):
    """Posted job cost grouped job → vendor, with each vendor's bills count,
    paid to date, retainage held outstanding and open A/P on the job. Cost is
    for the period; the bill figures are job-to-date. A No vendor bucket holds
    the cost with no vendor on its transaction (journals, job cost entries)."""
    # start_date > end_date is refused inside the service, so the JSON, PDF and
    # CSV routes all share the one guard.
    start_date, end_date = _period(db, start_date, end_date)
    return job_cost_by_vendor(
        db, start_date, end_date, job_id, customer_id, status, include_closed
    )


def _vendor_period(data: dict) -> str:
    return f"{data['start_date']} — {data['end_date']}"


@router.get("/job-cost-by-vendor/pdf")
def job_cost_by_vendor_pdf(
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    db: Session = Depends(get_db),
    job_id: _JOB_Q = None,
    customer_id: _CUST_Q = None,
    status: _STATUS_Q = None,
    include_closed: _CLOSED_Q = False,
):
    start_date, end_date = _period(db, start_date, end_date)
    data = job_cost_by_vendor(
        db, start_date, end_date, job_id, customer_id, status, include_closed
    )
    sections = _per_job_grid_sections(data, "Job Cost by Vendor")
    sections[0]["period"] = _vendor_period(data)
    return _pdf_response(
        sections,
        db,
        f"job-cost-by-vendor_{data['start_date']}_{data['end_date']}.pdf",
        landscape=True,
    )


@router.get("/job-cost-by-vendor/csv")
def job_cost_by_vendor_csv(
    request: Request,
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    db: Session = Depends(get_db),
    job_id: _JOB_Q = None,
    customer_id: _CUST_Q = None,
    status: _STATUS_Q = None,
    include_closed: _CLOSED_Q = False,
):
    start_date, end_date = _period(db, start_date, end_date)
    data = job_cost_by_vendor(
        db, start_date, end_date, job_id, customer_id, status, include_closed
    )
    out = io.StringIO()
    w = _SafeWriter(out)
    cols = data["columns"]
    _preamble(w, _company_name(db), "Job Cost by Vendor", _vendor_period(data))
    w.writerow(["Job"] + [c["label"] for c in cols])
    for job in data["jobs"]:
        for r in job["rows"]:
            w.writerow(
                [job["job_name"]] + [_csv_cell(r[c["key"]], c["kind"]) for c in cols]
            )
        t = job["totals"]
        w.writerow(
            [job["job_name"], f"Total {job['job_name']}"]
            + [
                _csv_cell(t[c["key"]], c["kind"]) if c["key"] in t else ""
                for c in cols[1:]
            ]
        )
    gt = data["totals"]
    w.writerow(
        ["", "Grand total"]
        + [
            _csv_cell(gt[c["key"]], c["kind"]) if c["key"] in gt else ""
            for c in cols[1:]
        ]
    )
    return _csv_download(
        out.getvalue(),
        f"job-cost-by-vendor_{data['start_date']}_{data['end_date']}.csv",
        request,
    )


# ── Export routes for the two existing contractor reports (#556) ────────────
# Job Profitability and Job Budget vs Actual had on-screen views but no PDF or
# CSV; these add them on the shared renderer so the Report Center can offer the
# same Save PDF / Save CSV buttons the other reports carry.


_JOB_PROFIT_COLUMNS = [
    {"key": "customer_name", "label": "Customer", "kind": "text"},
    {"key": "job_name", "label": "Job", "kind": "text"},
    {"key": "contract_amount", "label": "Contract", "kind": "money"},
    {"key": "income", "label": "Income", "kind": "money"},
    {"key": "total_costs", "label": "Costs", "kind": "money"},
    {"key": "net_income", "label": "Net", "kind": "money"},
    {"key": "margin_pct", "label": "Margin", "kind": "pct"},
]


def _job_profitability_data(db, start_date, end_date, customer_id):
    from app.routes.reports.financial import job_profitability_report

    return job_profitability_report(start_date, end_date, customer_id, db)


def _job_profitability_grid(data: dict) -> dict:
    return {
        "columns": _JOB_PROFIT_COLUMNS,
        "rows": data["jobs"],
        "totals": {
            "income": data["total_income"],
            "total_costs": data["total_costs"],
            "net_income": data["total_net_income"],
        },
        "as_of": None,
    }


@router.get("/job-profitability/pdf")
def job_profitability_pdf(
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    customer_id: int = Query(default=None),
    db: Session = Depends(get_db),
):
    data = _job_profitability_data(db, start_date, end_date, customer_id)
    grid = _job_profitability_grid(data)
    cols = grid["columns"]
    rows = [
        {"cells": [_pdf_cell(r.get(c["key"]), c["kind"]) for c in cols]}
        for r in grid["rows"]
    ]
    totals = grid["totals"]
    rows.append(
        {
            "cells": ["Total", ""]
            + [
                _pdf_cell(totals.get(c["key"]), c["kind"]) if c["key"] in totals else ""
                for c in cols[2:]
            ],
            "style": "grand-total",
        }
    )
    sections = [
        {
            "title": "Job Profitability",
            "period": f"{data['start_date']} — {data['end_date']}",
            "columns": [c["label"] for c in cols],
            "text_columns": 2,
            "rows": rows,
        }
    ]
    return _pdf_response(
        sections,
        db,
        f"job-profitability_{data['start_date']}_{data['end_date']}.pdf",
        landscape=True,
    )


@router.get("/job-profitability/csv")
def job_profitability_csv(
    request: Request,
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    customer_id: int = Query(default=None),
    db: Session = Depends(get_db),
):
    data = _job_profitability_data(db, start_date, end_date, customer_id)
    grid = _job_profitability_grid(data)
    cols = grid["columns"]
    out = io.StringIO()
    w = _SafeWriter(out)
    _preamble(
        w,
        _company_name(db),
        "Job Profitability",
        f"{data['start_date']} to {data['end_date']}",
    )
    w.writerow([c["label"] for c in cols])
    for r in grid["rows"]:
        w.writerow([_csv_cell(r.get(c["key"]), c["kind"]) for c in cols])
    totals = grid["totals"]
    w.writerow(
        ["Total", ""]
        + [
            _csv_cell(totals.get(c["key"]), c["kind"]) if c["key"] in totals else ""
            for c in cols[2:]
        ]
    )
    return _csv_download(
        out.getvalue(),
        f"job-profitability_{data['start_date']}_{data['end_date']}.csv",
        request,
    )


_BVA_COLUMNS = [
    {"key": "customer_name", "label": "Customer", "kind": "text"},
    {"key": "job_name", "label": "Job", "kind": "text"},
    {"key": "revised", "label": "Budget", "kind": "money"},
    {"key": "committed", "label": "Committed", "kind": "money"},
    {"key": "actual", "label": "Actual", "kind": "money"},
    {"key": "projected", "label": "Projected", "kind": "money"},
    {"key": "variance", "label": "Variance", "kind": "money"},
    {"key": "pct_used", "label": "% Used", "kind": "pct"},
    {"key": "act_revenue", "label": "Revenue", "kind": "money"},
]

_BVA_SUMMED = ("revised", "committed", "actual", "projected", "variance", "act_revenue")


def _bva_period(db, start_date, end_date):
    if not start_date:
        start_date = fiscal_year_start(db, date.today())
    if not end_date:
        end_date = date.today()
    return start_date, end_date


def _bva_rows(db, start_date, end_date, customer_id=None, include_inactive=False):
    from app.services.job_costing import budget_vs_actual_all_jobs

    return budget_vs_actual_all_jobs(
        db,
        start_date,
        end_date,
        customer_id=customer_id,
        include_inactive=include_inactive,
    )


@router.get("/job-budget-vs-actual/pdf")
def job_budget_vs_actual_pdf(
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    customer_id: _CUST_Q = None,
    include_inactive: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    start_date, end_date = _bva_period(db, start_date, end_date)
    rows_data = _bva_rows(db, start_date, end_date, customer_id, include_inactive)
    cols = _BVA_COLUMNS
    from decimal import Decimal

    totals = {k: Decimal("0") for k in _BVA_SUMMED}
    rows = []
    for r in rows_data:
        for k in _BVA_SUMMED:
            totals[k] += Decimal(str(r.get(k) or 0))
        rows.append({"cells": [_pdf_cell(r.get(c["key"]), c["kind"]) for c in cols]})
    rows.append(
        {
            "cells": ["Total", ""]
            + [
                (
                    _pdf_cell(float(totals[c["key"]]), c["kind"])
                    if c["key"] in totals
                    else ""
                )
                for c in cols[2:]
            ],
            "style": "grand-total",
        }
    )
    sections = [
        {
            "title": "Job Budget vs Actual",
            "period": f"{start_date} — {end_date}",
            "columns": [c["label"] for c in cols],
            "text_columns": 2,
            "rows": rows,
        }
    ]
    return _pdf_response(
        sections,
        db,
        f"job-budget-vs-actual_{start_date}_{end_date}.pdf",
        landscape=True,
    )


@router.get("/job-budget-vs-actual/csv")
def job_budget_vs_actual_csv(
    request: Request,
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    customer_id: _CUST_Q = None,
    include_inactive: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    start_date, end_date = _bva_period(db, start_date, end_date)
    rows_data = _bva_rows(db, start_date, end_date, customer_id, include_inactive)
    cols = _BVA_COLUMNS
    from decimal import Decimal

    out = io.StringIO()
    w = _SafeWriter(out)
    _preamble(
        w, _company_name(db), "Job Budget vs Actual", f"{start_date} to {end_date}"
    )
    w.writerow([c["label"] for c in cols])
    totals = {k: Decimal("0") for k in _BVA_SUMMED}
    for r in rows_data:
        for k in _BVA_SUMMED:
            totals[k] += Decimal(str(r.get(k) or 0))
        w.writerow([_csv_cell(r.get(c["key"]), c["kind"]) for c in cols])
    w.writerow(
        ["Total", ""]
        + [
            _csv_cell(float(totals[c["key"]]), c["kind"]) if c["key"] in totals else ""
            for c in cols[2:]
        ]
    )
    return _csv_download(
        out.getvalue(), f"job-budget-vs-actual_{start_date}_{end_date}.csv", request
    )


# ── Job Cost Detail (#691): the job's Cost Detail tab, saved ─────────────

_COST_TREE_COLUMNS = [
    {"key": "original", "label": "Original", "kind": "money"},
    {"key": "changes", "label": "Changes", "kind": "money"},
    {"key": "revised", "label": "Budget", "kind": "money"},
    {"key": "committed", "label": "Committed", "kind": "money"},
    {"key": "actual", "label": "Actual", "kind": "money"},
    {"key": "projected", "label": "Projected", "kind": "money"},
    {"key": "variance", "label": "Variance", "kind": "money"},
    {"key": "pct_used", "label": "% Used", "kind": "pct"},
    {"key": "est_revenue", "label": "Est. Rev", "kind": "money"},
    {"key": "act_revenue", "label": "Act. Rev", "kind": "money"},
]


def cost_tree_period(tree: dict) -> str:
    """The period line: Job to date, or the dates the actuals were read for."""
    start, end = tree.get("start_date"), tree.get("end_date")
    if start and end:
        return f"{start} — {end}"
    if end:
        return f"Job to date through {end}"
    if start:
        return f"From {start}"
    return "Job to date"


def _cost_tree_shows_uncoded(un: dict) -> bool:
    f = un["figures"]
    return bool(un["lines"]) or any(f[c["key"]] for c in _COST_TREE_COLUMNS)


def _cost_tree_rows(tree: dict, opened=None) -> list[dict]:
    """Every row of the tree in the tab's order: each cost type, its codes
    (depth-first, children under their parent), its "No cost code" row, then
    the whole-job budget. `opened` is the tab's set of expanded keys
    (`type:<code>`, `code:<id>`); None means every row."""

    def is_open(key):
        return opened is None or key in opened

    rows = []

    def walk(node, ty, depth):
        rows.append(
            {
                "kind": "code",
                "cost_type": ty["name"],
                "code": node["code"],
                "name": node["name"],
                "label": f"{node['code']} {node['name']}"
                + ("" if node["is_active"] else " (inactive)"),
                "depth": depth,
                "figures": node["figures"],
            }
        )
        if is_open(f"code:{node['id']}"):
            for ch in node["children"]:
                walk(ch, ty, depth + 1)

    for ty in tree["types"]:
        rows.append(
            {
                "kind": "type",
                "cost_type": ty["name"],
                "code": "",
                "name": ty["name"],
                "label": ty["name"],
                "depth": 0,
                "figures": ty["figures"],
            }
        )
        if not is_open(f"type:{ty['cost_type']}"):
            continue
        for node in ty["codes"]:
            walk(node, ty, 1)
        if _cost_tree_shows_uncoded(ty["uncoded"]):
            rows.append(
                {
                    "kind": "uncoded",
                    "cost_type": ty["name"],
                    "code": "",
                    "name": "No cost code",
                    "label": "No cost code",
                    "depth": 1,
                    "figures": ty["uncoded"]["figures"],
                }
            )
    jl = tree["job_level_budget"]
    if jl["original"] or jl["changes"] or jl["est_revenue"]:
        rows.append(
            {
                "kind": "joblevel",
                "cost_type": "",
                "code": "",
                "name": "Whole-job budget (not by code)",
                "label": "Whole-job budget (not by code)",
                "depth": 0,
                "figures": jl,
            }
        )
    return rows


_COST_TREE_NOTE = (
    "Budgets and committed cost are job to date; actual cost and revenue are "
    "for the period."
)


def cost_tree_sections(tree: dict, job_name: str, opened=None) -> list[dict]:
    """The Cost Detail tab as one landscape grid: types bold, codes indented
    by level, the totals row last."""
    cols = _COST_TREE_COLUMNS
    rows = []
    for r in _cost_tree_rows(tree, opened):
        row = {
            "cells": [r["label"]]
            + [_pdf_cell(r["figures"][c["key"]], c["kind"]) for c in cols]
        }
        if r["kind"] == "type":
            row["style"] = "subtotal"
        elif r["depth"]:
            row["indent"] = r["depth"]
        rows.append(row)
    t = tree["totals"]
    rows.append(
        {
            "cells": ["Total"] + [_pdf_cell(t[c["key"]], c["kind"]) for c in cols],
            "style": "grand-total",
        }
    )
    dated = tree.get("start_date") or tree.get("end_date")
    return [
        {
            "title": f"Job Cost Detail — {job_name}",
            "period": cost_tree_period(tree),
            "columns": ["Cost type › code"] + [c["label"] for c in cols],
            "text_columns": 1,
            "rows": rows,
            "note": _COST_TREE_NOTE if dated else None,
        }
    ]


def cost_tree_csv(tree: dict, job_name: str, company: str) -> str:
    """Every row of the tree, fully expanded: Cost type | Code | Name | Level
    and the tab's figures, then the Total row."""
    cols = _COST_TREE_COLUMNS
    out = io.StringIO()
    w = _SafeWriter(out)
    _preamble(w, company, f"Job Cost Detail — {job_name}", cost_tree_period(tree))
    w.writerow(["Cost type", "Code", "Name", "Level"] + [c["label"] for c in cols])
    for r in _cost_tree_rows(tree):
        w.writerow(
            [r["cost_type"], r["code"], r["name"], r["depth"]]
            + [_csv_cell(r["figures"][c["key"]], c["kind"]) for c in cols]
        )
    t = tree["totals"]
    w.writerow(
        ["Total", "", "", ""] + [_csv_cell(t[c["key"]], c["kind"]) for c in cols]
    )
    if tree.get("start_date") or tree.get("end_date"):
        w.writerow([_COST_TREE_NOTE])
    return out.getvalue()


# ── Field ticket log ──────────────────────────────────────────────────────
# Per job, the T&M tickets that are still money on the table: unsigned, and
# signed but not yet billed, at their bill rates (app/services/field_tickets).


@router.get("/field-ticket-log")
def field_ticket_log_route(
    as_of: date = Query(default=None),
    db: Session = Depends(get_db),
    job_id: _JOB_Q = None,
    customer_id: _CUST_Q = None,
    status: _STATUS_Q = None,
    include_closed: _CLOSED_Q = False,
):
    """Per job, the T&M tickets dated by `as_of` not yet billed: unsigned ones
    and signed ones, each at its bill rates, with the change order a signed
    ticket is priced on when that is not yet approved. Totals per job and
    grand. A job with no open ticket is left out."""
    from app.services.field_tickets import field_ticket_log

    return field_ticket_log(db, as_of, job_id, customer_id, status, include_closed)


@router.get("/field-ticket-log/pdf")
def field_ticket_log_pdf(
    as_of: date = Query(default=None),
    db: Session = Depends(get_db),
    job_id: _JOB_Q = None,
    customer_id: _CUST_Q = None,
    status: _STATUS_Q = None,
    include_closed: _CLOSED_Q = False,
):
    from app.services.field_tickets import field_ticket_log

    data = field_ticket_log(db, as_of, job_id, customer_id, status, include_closed)
    return _pdf_response(
        _per_job_grid_sections(data, "Field Ticket Log", f"As of {data['as_of']}"),
        db,
        f"field-ticket-log_{data['as_of']}.pdf",
        landscape=True,
    )


@router.get("/field-ticket-log/csv")
def field_ticket_log_csv(
    request: Request,
    as_of: date = Query(default=None),
    db: Session = Depends(get_db),
    job_id: _JOB_Q = None,
    customer_id: _CUST_Q = None,
    status: _STATUS_Q = None,
    include_closed: _CLOSED_Q = False,
):
    from app.services.field_tickets import field_ticket_log

    data = field_ticket_log(db, as_of, job_id, customer_id, status, include_closed)
    return _csv_download(
        _per_job_grid_csv(
            data, _company_name(db), "Field Ticket Log", f"As of {data['as_of']}"
        ),
        f"field-ticket-log_{data['as_of']}.csv",
        request,
    )
