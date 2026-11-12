"""Profit & Loss Detail (#477): the P&L with every transaction under its
account, the report a bookkeeper sends a client beside the P&L each month.

QuickBooks Desktop's columns: Type, Date, Num, Name, Memo, Split, Amount,
Balance. Each account has its running balance and a total, sub-accounts sit
beneath their parents with a total per family (#416), and the section totals
(Total Income, Gross Profit, Total Expenses, Other Income / Other Expense
when there are any, Net Income) are the Profit & Loss's for the same dates,
class and adjusting-entries choice. A job narrows it to that job's lines, as
P&L by Job's column for the job.
"""

from datetime import date
from typing import Annotated, Optional

from fastapi import Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.routes.reports._router import router
from app.schemas.common import BIGINT_MAX
from app.routes.reports.financial import (
    _EXCLUDE_ADJUSTING,
    _BASIS_QUERY,
    _HIDE_ZERO,
    _SORT_QUERY,
    _account_order,
    _chart_index,
    _company_name,
    _csv_download,
    _flag,
    _has_other,
    _in_class,
    _money,
    _pdf_response,
    _pl_section_of,
    _with_figures,
    pl_totals,
)
from app.services import cash_basis
from app.services.fiscal_year import fiscal_year_start
from app.services.pl_detail import PL_SECTIONS, pl_detail_lines
from app.services.statement_layout import account_chart, family_rows
from app.services.terminology import terms_from_db


def _job_name(db, job_id) -> Optional[str]:
    if job_id is None:
        return None
    if job_id == 0:
        from app.services.jobs_service import NO_JOB_LABEL

        return terms_from_db(db).text(NO_JOB_LABEL)
    from app.models.jobs import Job

    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job.name


@router.get("/profit-loss-detail")
def profit_loss_detail(
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    db: Session = Depends(get_db),
    # after db, and a plain None by default: the PDF and CSV call this
    # directly with db as the third argument
    class_id: Annotated[
        Optional[int],
        Query(description="Only this class's lines; omit for all", le=BIGINT_MAX),
    ] = None,
    job_id: Annotated[
        Optional[int],
        Query(
            description="Only this job's lines; 0 for the lines with no job",
            le=BIGINT_MAX,
        ),
    ] = None,
    sort: str = _SORT_QUERY,
    exclude_adjusting: bool = _EXCLUDE_ADJUSTING,
    basis: str = _BASIS_QUERY,
    hide_zero: bool = _HIDE_ZERO,
):
    """Every posted line under each P&L account for the period (#477), in
    the P&L's sections and order. `sections` has each section's accounts
    with their lines; `tree` the same as printed rows, sub-accounts beneath
    their parents (statement_layout.family_rows); the totals are the
    P&L's own keys. `basis` (#483): accrual or cash, else the company's.
    `hide_zero` (#625) leaves out an account whose lines net to nothing,
    its lines with it; the totals are the P&L's either way."""
    basis = cash_basis.resolve_basis(db, basis if isinstance(basis, str) else None)
    exclude_adjusting = _flag(exclude_adjusting)
    hide_zero = _flag(hide_zero)
    sort = sort if isinstance(sort, str) else "number"
    class_id = class_id if isinstance(class_id, int) else None
    job_id = job_id if isinstance(job_id, int) else None
    if not start_date:
        start_date = fiscal_year_start(db, date.today())
    if not end_date:
        end_date = date.today()
    if start_date > end_date:
        raise HTTPException(status_code=400, detail="start_date is after end_date")
    cls = _in_class(db, class_id)[0] if class_id is not None else None
    job_name = _job_name(db, job_id)
    t = terms_from_db(db)
    chart = _chart_index(db)
    blocks = pl_detail_lines(
        db,
        start_date,
        end_date,
        class_id=class_id,
        job_id=job_id,
        exclude_adjusting=exclude_adjusting,
        chart=chart,
        words=t,
        basis=basis,
    )
    order = _account_order(db, sort, chart)
    sections = {key: [] for key in PL_SECTIONS}
    for block in sorted(blocks.values(), key=order):
        sections[block["section"]].append(block)
    # the totals from every account, before a zero one goes (#625)
    totals = pl_totals(*(sections[k] for k in PL_SECTIONS))
    if hide_zero:
        sections = {key: _with_figures(lines) for key, lines in sections.items()}

    section_of = {aid: _pl_section_of(row[3], row[4]) for aid, row in chart.items()}
    accounts = account_chart(index=chart)
    tree = {
        key: family_rows(
            sections[key],
            accounts,
            lambda aid, key=key: section_of.get(aid) == key,
            ("amount",),
            lambda aid: order({"account_id": aid}),
        )
        for key in PL_SECTIONS
    }
    data = {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "class_id": cls.id if cls else None,
        "class_name": cls.name if cls else None,
        "job_id": job_id,
        "job_name": job_name,
        "sort": sort,
        "exclude_adjusting": bool(exclude_adjusting),
        "basis": basis,
        "basis_note": cash_basis.receivable_note(db, end_date, basis),
        "hide_zero": bool(hide_zero),
        "sections": sections,
        "tree": tree,
        # the lines printed: a hidden account's go with it
        "line_count": sum(len(b["entries"]) for s in sections.values() for b in s),
        **totals,
    }
    data["has_other_income"] = _has_other(sections["other_income"])
    data["has_other_expense"] = _has_other(sections["other_expense"])
    data["has_other"] = data["has_other_income"] or data["has_other_expense"]
    return data


# The report's layout above Net Income, for the PDF and the CSV as the
# screen prints it: (section heading, section key or None for a summary
# row, total label, total key).
def detail_layout(data: dict, t) -> list[tuple]:
    out = [
        (t("Income"), "income", t("Total Income"), "total_operating_income"),
        ("Cost of Goods Sold", "cogs", "Total Cost of Goods Sold", "total_cogs"),
        (None, None, "Gross Profit", "operating_gross_profit"),
        ("Expenses", "expenses", "Total Expenses", "total_operating_expenses"),
    ]
    if data.get("has_other"):
        out.append((None, None, t("Net Operating Income"), "net_operating_income"))
        if data.get("has_other_income"):
            out.append(
                (
                    t("Other Income"),
                    "other_income",
                    t("Total Other Income"),
                    "total_other_income",
                )
            )
        if data.get("has_other_expense"):
            out.append(
                (
                    "Other Expense",
                    "other_expense",
                    "Total Other Expense",
                    "total_other_expense",
                )
            )
        out.append((None, None, t("Net Other Income"), "net_other_income"))
    return out


def detail_title(data: dict, t) -> str:
    return t("Profit & Loss") + " Detail"


def detail_scope(data: dict, t) -> str:
    """What the figures are, after the dates: one class's or one job's,
    without the adjusting entries."""
    words = []
    if data.get("class_name"):
        words.append(f"{t('Class')}: {data['class_name']} only")
    if data.get("job_name"):
        words.append(f"{t('Job')}: {data['job_name']} only")
    if data.get("exclude_adjusting"):
        words.append("adjusting entries left out")
    if data.get("hide_zero"):
        words.append("zero balances hidden")
    return ", ".join(words)


def _detail_filename(data: dict, t, ext: str) -> str:
    name = t.slug("Profit & Loss") + "-detail"
    if data.get("class_name"):
        name += "_" + (t.slug(data["class_name"]) or "class")
    if data.get("job_name"):
        name += "_" + (t.slug(data["job_name"]) or "job")
    return f"{name}_{data['start_date']}_{data['end_date']}.{ext}"


def _cut(text, n) -> str:
    text = text or ""
    return text if len(text) <= n else text[: n - 1] + "…"


def _detail_section(data: dict, t) -> dict:
    """The report's rows for the PDF: an account's heading, its lines
    indented beneath it, its total; families as the P&L prints them."""
    blank7 = [""] * 7
    rows = []

    def account_rows(r):
        indent = r["depth"] + 1
        rows.append(
            {"cells": [r["label"]] + blank7, "style": "group", "indent": indent}
        )
        for e in r.get("entries", []):
            rows.append(
                {
                    "cells": [
                        e["type"] + (" (void)" if e.get("voided") else ""),
                        e["date"],
                        _cut(e.get("num"), 14),
                        _cut(e.get("name"), 28),
                        _cut(e.get("memo"), 36),
                        _cut(e.get("split"), 28),
                        _money(e["amount"]),
                        _money(e["balance"]),
                    ],
                    "indent": indent + 1,
                }
            )
        rows.append(
            {
                "cells": [f"Total {r['label']}"] + [""] * 5 + [_money(r["amount"]), ""],
                "style": "subtotal",
                "indent": indent,
            }
        )

    for head, key, total_label, total_key in detail_layout(data, t):
        if key:
            rows.append({"cells": [head] + blank7, "style": "subtotal"})
            for r in data["tree"][key]:
                if r["kind"] in ("account", "direct"):
                    account_rows(r)
                elif r["kind"] == "heading":
                    rows.append(
                        {
                            "cells": [r["label"]] + blank7,
                            "style": "group",
                            "indent": r["depth"] + 1,
                        }
                    )
                else:
                    rows.append(
                        {
                            "cells": [r["label"]]
                            + [""] * 5
                            + [_money(r["amount"]), ""],
                            "style": "subtotal",
                            "indent": r["depth"] + 1,
                        }
                    )
        rows.append(
            {
                "cells": [total_label] + [""] * 5 + [_money(data[total_key]), ""],
                "style": "subtotal",
            }
        )
    rows.append(
        {
            "cells": [t("Net Income")] + [""] * 5 + [_money(data["net_income"]), ""],
            "style": "grand-total",
        }
    )
    period = f"{data['start_date']} — {data['end_date']}"
    if data.get("basis") == "cash":
        period += " (cash basis)"
    scope = detail_scope(data, t)
    return {
        "title": detail_title(data, t),
        "period": period + (f" · {scope}" if scope else ""),
        "columns": [
            "Type",
            "Date",
            "Num",
            "Name",
            "Memo",
            "Split",
            "Amount",
            "Balance",
        ],
        # Type to Split are words, read from the left (report_pdf.html)
        "text_columns": 6,
        "rows": rows,
        "note": data.get("basis_note"),
    }


@router.get("/profit-loss-detail/pdf")
def profit_loss_detail_pdf(
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    db: Session = Depends(get_db),
    class_id: Optional[int] = Query(default=None, le=BIGINT_MAX),
    job_id: Optional[int] = Query(default=None, le=BIGINT_MAX),
    sort: str = _SORT_QUERY,
    exclude_adjusting: bool = _EXCLUDE_ADJUSTING,
    basis: str = _BASIS_QUERY,
    hide_zero: bool = _HIDE_ZERO,
):
    data = profit_loss_detail(
        start_date,
        end_date,
        db,
        class_id=class_id,
        job_id=job_id,
        sort=sort,
        exclude_adjusting=exclude_adjusting,
        basis=basis,
        hide_zero=hide_zero,
    )
    t = terms_from_db(db)
    return _pdf_response(
        [_detail_section(data, t)],
        db,
        _detail_filename(data, t, "pdf"),
        landscape=True,
    )


@router.get("/profit-loss-detail/csv")
def profit_loss_detail_csv_route(
    request: Request,
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    db: Session = Depends(get_db),
    class_id: Optional[int] = Query(default=None, le=BIGINT_MAX),
    job_id: Optional[int] = Query(default=None, le=BIGINT_MAX),
    sort: str = _SORT_QUERY,
    exclude_adjusting: bool = _EXCLUDE_ADJUSTING,
    basis: str = _BASIS_QUERY,
    hide_zero: bool = _HIDE_ZERO,
):
    from app.services.ledger_exports import profit_loss_detail_csv

    data = profit_loss_detail(
        start_date,
        end_date,
        db,
        class_id=class_id,
        job_id=job_id,
        sort=sort,
        exclude_adjusting=exclude_adjusting,
        basis=basis,
        hide_zero=hide_zero,
    )
    t = terms_from_db(db)
    return _csv_download(
        profit_loss_detail_csv(data, _company_name(db), t),
        _detail_filename(data, t, "csv"),
        request,
    )
