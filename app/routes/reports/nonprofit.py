"""Nonprofit statements: Financial Position, Activities, Fund Balances,
Functional Expenses — each as JSON, PDF (the shared report renderer) and
CSV (Form 990 Part IX column order for functional expenses)."""

from datetime import date
from decimal import Decimal

from fastapi import Depends, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.routes.reports._router import router
from app.routes.reports.financial import _money, _pdf_response
from app.services import nonprofit_reports as svc


def _period(start_date, end_date):
    if not start_date:
        start_date = date(date.today().year, 1, 1)
    if not end_date:
        end_date = date.today()
    return start_date, end_date


def _csv_response(text: str, filename: str) -> Response:
    # The shared CSV helper: it adds the byte-order mark Excel needs to read
    # the file as UTF-8.
    from app.routes.csv import _csv_response as csv_download

    return csv_download(text, filename)


# ── Statement of Financial Position ──────────────────────────────────────


@router.get("/statement-of-financial-position")
def statement_of_financial_position(
    as_of_date: date = Query(default=None), db: Session = Depends(get_db)
):
    return svc.statement_of_financial_position(db, as_of_date or date.today())


def _sofp_section(data: dict) -> dict:
    rows = []
    for label, key, total_key in (
        ("Assets", "assets", "total_assets"),
        ("Liabilities", "liabilities", "total_liabilities"),
        ("Net Assets", "net_assets", "total_net_assets"),
    ):
        rows.append({"cells": [label, ""], "style": "subtotal"})
        for item in data[key]:
            rows.append(
                {"cells": [f"  {item['account_name']}", _money(item["amount"])]}
            )
        rows.append(
            {"cells": [f"Total {label}", _money(data[total_key])], "style": "subtotal"}
        )
    rows.append(
        {
            "cells": [
                "Liabilities + Net Assets",
                _money(data["total_liabilities_and_net_assets"]),
            ],
            "style": "grand-total",
        }
    )
    return {
        "title": "Statement of Financial Position",
        "period": f"As of {data['as_of_date']}",
        "columns": ["", "Amount"],
        "rows": rows,
    }


@router.get("/statement-of-financial-position/pdf")
def statement_of_financial_position_pdf(
    as_of_date: date = Query(default=None), db: Session = Depends(get_db)
):
    data = svc.statement_of_financial_position(db, as_of_date or date.today())
    return _pdf_response(
        [_sofp_section(data)],
        db,
        f"statement-of-financial-position_{data['as_of_date']}.pdf",
    )


@router.get("/statement-of-financial-position/csv")
def statement_of_financial_position_csv(
    as_of_date: date = Query(default=None), db: Session = Depends(get_db)
):
    data = svc.statement_of_financial_position(db, as_of_date or date.today())
    return _csv_response(
        svc.financial_position_csv(data),
        f"statement-of-financial-position_{data['as_of_date']}.csv",
    )


# ── Statement of Activities ──────────────────────────────────────────────


def _soa(db, start_date, end_date, compare):
    if compare == "prior_year":
        return svc.statement_of_activities_compared(db, start_date, end_date)
    return svc.statement_of_activities(db, start_date, end_date)


def _sfe(db, start_date, end_date, compare):
    if compare == "prior_year":
        return svc.functional_expenses_compared(db, start_date, end_date)
    return svc.functional_expenses(db, start_date, end_date)


@router.get("/statement-of-activities")
def statement_of_activities(
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    compare: str = Query(
        default=None, description="prior_year adds last year's column"
    ),
    db: Session = Depends(get_db),
):
    start_date, end_date = _period(start_date, end_date)
    return _soa(db, start_date, end_date, compare)


def _soa_section(data: dict) -> dict:
    t = data["totals"]
    cmp = data.get("compare") == "prior_year"
    pt = data.get("prior", {}).get("totals", {}) if cmp else {}

    def line(label, without, with_, total, prior=None, style=None):
        cells = [label, _money(without), _money(with_), _money(total)]
        if cmp:
            cells += [
                _money(prior or 0),
                _money(Decimal(str(total or 0)) - Decimal(str(prior or 0))),
            ]
        row = {"cells": cells}
        if style:
            row["style"] = style
        return row

    def head(label):
        return {"cells": [label] + [""] * (5 if cmp else 3), "style": "subtotal"}

    rows = [head("Revenue & Support")]
    for r in data["revenue"]:
        rows.append(
            line(
                f"  {r['account_name']}",
                r["without"],
                r["with"],
                r["total"],
                r.get("prior_total"),
            )
        )
    rows.append(
        line(
            "Total Revenue & Support",
            t["revenue_without"],
            t["revenue_with"],
            t["revenue"],
            pt.get("revenue"),
            "subtotal",
        )
    )
    rl = data["releases"]
    rows.append(
        line("Net assets released from restrictions", rl["without"], rl["with"], 0, 0)
    )
    rows.append(head("Expenses"))
    for r in data["expenses"]:
        rows.append(
            line(
                f"  {r['account_name']}",
                r["without"],
                0,
                r["total"],
                r.get("prior_total"),
            )
        )
    rows.append(
        line(
            "Total Expenses",
            t["expenses"],
            0,
            t["expenses"],
            pt.get("expenses"),
            "subtotal",
        )
    )
    rows.append(
        line(
            "Change in Net Assets",
            t["change_without"],
            t["change_with"],
            t["change_total"],
            pt.get("change_total"),
            "grand-total",
        )
    )
    columns = ["", "Without Donor Restrictions", "With Donor Restrictions", "Total"]
    if cmp:
        columns += [f"Prior year ({data['prior']['start_date'][:4]})", "Change"]
    return {
        "title": "Statement of Activities",
        "period": f"{data['start_date']} — {data['end_date']}",
        "columns": columns,
        "rows": rows,
    }


@router.get("/statement-of-activities/pdf")
def statement_of_activities_pdf(
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    compare: str = Query(default=None),
    db: Session = Depends(get_db),
):
    start_date, end_date = _period(start_date, end_date)
    data = _soa(db, start_date, end_date, compare)
    return _pdf_response(
        [_soa_section(data)],
        db,
        f"statement-of-activities_{start_date}_{end_date}.pdf",
    )


@router.get("/statement-of-activities/csv")
def statement_of_activities_csv(
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    compare: str = Query(default=None),
    db: Session = Depends(get_db),
):
    start_date, end_date = _period(start_date, end_date)
    data = _soa(db, start_date, end_date, compare)
    return _csv_response(
        svc.activities_csv(data),
        f"statement-of-activities_{start_date}_{end_date}.csv",
    )


# ── Fund balances ────────────────────────────────────────────────────────


@router.get("/fund-balances")
def fund_balances(
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    db: Session = Depends(get_db),
):
    start_date, end_date = _period(start_date, end_date)
    return svc.fund_balances(db, start_date, end_date)


def _funds_section(data: dict) -> dict:
    keys = (
        "beginning",
        "contributions",
        "expenses",
        "releases",
        "ending",
        "unreleased",
    )
    rows = [
        {"cells": [f["class_name"]] + [_money(f[k]) for k in keys]}
        for f in data["funds"]
    ]
    if data["unassigned"]:
        u = data["unassigned"]
        rows.append({"cells": [u["class_name"]] + [_money(u[k]) for k in keys]})
    rows.append(
        {
            "cells": ["Total"] + [_money(data["totals"][k]) for k in keys],
            "style": "grand-total",
        }
    )
    return {
        "title": "Fund Balances (With Donor Restrictions)",
        "period": f"{data['start_date']} — {data['end_date']}",
        "columns": [
            "Fund",
            "Beginning",
            "Contributions",
            "Spent",
            "Released",
            "Ending",
            "Unreleased",
        ],
        "rows": rows,
    }


@router.get("/fund-balances/pdf")
def fund_balances_pdf(
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    db: Session = Depends(get_db),
):
    start_date, end_date = _period(start_date, end_date)
    data = svc.fund_balances(db, start_date, end_date)
    return _pdf_response(
        [_funds_section(data)], db, f"fund-balances_{start_date}_{end_date}.pdf"
    )


@router.get("/fund-balances/csv")
def fund_balances_csv(
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    db: Session = Depends(get_db),
):
    start_date, end_date = _period(start_date, end_date)
    data = svc.fund_balances(db, start_date, end_date)
    return _csv_response(
        svc.fund_balances_csv(data), f"fund-balances_{start_date}_{end_date}.csv"
    )


# ── Statement of Functional Expenses ─────────────────────────────────────


@router.get("/functional-expenses")
def functional_expenses(
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    compare: str = Query(
        default=None, description="prior_year adds last year's column"
    ),
    db: Session = Depends(get_db),
):
    start_date, end_date = _period(start_date, end_date)
    return _sfe(db, start_date, end_date, compare)


def _sfe_section(data: dict) -> dict:
    keys = ("total", "program", "management", "fundraising", "unassigned")
    cmp = data.get("compare") == "prior_year"

    def extra(r):
        if not cmp:
            return []
        return [_money(r.get("prior_total", 0)), _money(r.get("change", 0))]

    rows = [
        {
            "cells": [f"{r['account_number'] or ''} {r['account_name']}".strip()]
            + [_money(r[k]) for k in keys]
            + extra(r)
        }
        for r in data["rows"]
    ]
    pt = data.get("prior", {}).get("totals", {}) if cmp else {}
    total_extra = (
        [
            _money(pt.get("total", 0)),
            _money(
                Decimal(str(data["totals"]["total"])) - Decimal(str(pt.get("total", 0)))
            ),
        ]
        if cmp
        else []
    )
    rows.append(
        {
            "cells": ["Total"]
            + [_money(data["totals"][k]) for k in keys]
            + total_extra,
            "style": "grand-total",
        }
    )
    if data["programs"]:
        rows.append(
            {
                "cells": ["Program services by program", "", "", "", "", ""],
                "style": "subtotal",
            }
        )
        for p in data["programs"]:
            rows.append(
                {"cells": [f"  {p['class_name']}", "", _money(p["amount"]), "", "", ""]}
            )
    columns = ["Expense", "Total", "Program", "Management", "Fundraising", "Unassigned"]
    if cmp:
        columns += [f"Prior year ({data['prior']['start_date'][:4]})", "Change"]
    return {
        "title": "Statement of Functional Expenses",
        "period": f"{data['start_date']} — {data['end_date']}",
        "columns": columns,
        "rows": rows,
    }


@router.get("/functional-expenses/pdf")
def functional_expenses_pdf(
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    compare: str = Query(default=None),
    db: Session = Depends(get_db),
):
    start_date, end_date = _period(start_date, end_date)
    data = _sfe(db, start_date, end_date, compare)
    return _pdf_response(
        [_sfe_section(data)], db, f"functional-expenses_{start_date}_{end_date}.pdf"
    )


@router.get("/functional-expenses/csv")
def functional_expenses_csv(
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    compare: str = Query(default=None),
    db: Session = Depends(get_db),
):
    start_date, end_date = _period(start_date, end_date)
    data = _sfe(db, start_date, end_date, compare)
    return _csv_response(
        svc.functional_expenses_csv(data),
        f"functional-expenses_{start_date}_{end_date}.csv",
    )


def nonprofit_statement_sections(db: Session, start: date, end: date) -> list[dict]:
    """The statements pack in nonprofit words: Activities + Financial
    Position (used by the pack endpoint when company_type is nonprofit)."""
    return [
        _soa_section(svc.statement_of_activities(db, start, end)),
        _sofp_section(svc.statement_of_financial_position(db, end)),
    ]
