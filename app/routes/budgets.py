# ============================================================================
# Budget vs Actual — monthly budgets per account with variance reporting
# Phase 10: Quick Wins + Medium Effort Features
# ============================================================================


from datetime import date

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from sqlalchemy import func as sqlfunc

from app.database import get_db
from app.models.budgets import Budget
from app.models.accounts import Account
from app.models.transactions import Transaction, TransactionLine
from app.schemas.budgets import BudgetCreate, BudgetResponse

router = APIRouter(prefix="/api/budgets", tags=["budgets"])


@router.get("", response_model=list[BudgetResponse])
def list_budgets(
    year: int = Query(default=None),
    account_id: int = Query(default=None),
    db: Session = Depends(get_db),
):
    q = db.query(Budget)
    if year:
        q = q.filter(Budget.year == year)
    if account_id:
        q = q.filter(Budget.account_id == account_id)
    return q.order_by(Budget.account_id, Budget.month).all()


@router.post("", response_model=BudgetResponse, status_code=201)
def create_budget(data: BudgetCreate, db: Session = Depends(get_db)):
    existing = (
        db.query(Budget)
        .filter(
            Budget.account_id == data.account_id,
            Budget.year == data.year,
            Budget.month == data.month,
        )
        .first()
    )
    if existing:
        existing.amount = data.amount
        db.commit()
        db.refresh(existing)
        return existing
    budget = Budget(**data.model_dump())
    db.add(budget)
    db.commit()
    db.refresh(budget)
    return budget


@router.post("/bulk")
def bulk_upsert(items: list[BudgetCreate], db: Session = Depends(get_db)):
    """Batch upsert budget entries."""
    count = 0
    for item in items:
        existing = (
            db.query(Budget)
            .filter(
                Budget.account_id == item.account_id,
                Budget.year == item.year,
                Budget.month == item.month,
            )
            .first()
        )
        if existing:
            existing.amount = item.amount
        else:
            db.add(Budget(**item.model_dump()))
        count += 1
    db.commit()
    return {"saved": count}


@router.get("/variance")
def budget_variance(
    year: int = Query(...),
    db: Session = Depends(get_db),
):
    """Compare budget vs actual TransactionLine sums per account per month."""

    # Get all budgets for the year
    budgets = db.query(Budget).filter(Budget.year == year).all()
    budget_map = {}
    for b in budgets:
        budget_map.setdefault(b.account_id, {})[b.month] = float(b.amount)

    # Get all account IDs with budgets
    account_ids = list(budget_map.keys())
    if not account_ids:
        return {"year": year, "accounts": []}

    accounts = db.query(Account).filter(Account.id.in_(account_ids)).all()
    account_info = {
        a.id: {"name": a.name, "number": a.account_number, "type": a.account_type.value}
        for a in accounts
    }

    # Get actual amounts per account per month
    actuals = (
        db.query(
            TransactionLine.account_id,
            sqlfunc.extract("month", Transaction.date).label("month"),
            sqlfunc.sum(TransactionLine.debit - TransactionLine.credit),
        )
        .join(Transaction, TransactionLine.transaction_id == Transaction.id)
        .filter(TransactionLine.account_id.in_(account_ids))
        .filter(sqlfunc.extract("year", Transaction.date) == year)
        .group_by(
            TransactionLine.account_id, sqlfunc.extract("month", Transaction.date)
        )
        .all()
    )

    actual_map = {}
    for acct_id, month, amount in actuals:
        actual_map.setdefault(acct_id, {})[int(month)] = float(amount)

    result = []
    for acct_id in account_ids:
        info = account_info.get(acct_id, {})
        months = []
        for m in range(1, 13):
            budget_amt = budget_map.get(acct_id, {}).get(m, 0)
            actual_amt = actual_map.get(acct_id, {}).get(m, 0)
            # For expense/cogs accounts, actual is debit-credit (positive = spent)
            # For income accounts, actual is debit-credit (negative = earned), flip sign
            if info.get("type") in ("income", "liability", "equity"):
                actual_amt = -actual_amt
            months.append(
                {
                    "month": m,
                    "budget": budget_amt,
                    "actual": round(actual_amt, 2),
                    "variance": round(budget_amt - actual_amt, 2),
                }
            )
        result.append(
            {
                "account_id": acct_id,
                "account_name": info.get("name", ""),
                "account_number": info.get("number", ""),
                "account_type": info.get("type", ""),
                "months": months,
                "total_budget": sum(m["budget"] for m in months),
                "total_actual": sum(m["actual"] for m in months),
                "total_variance": sum(m["variance"] for m in months),
            }
        )

    return {"year": year, "accounts": result}


# ── Print pages (NEW-208, #702) ──────────────────────────────────────────
# The desktop app has no print menu, so a page's Print button opens one of
# these in a window of its own, as a report's Print does (#683): the
# report PDF's template and stylesheet, landscape, the month row at the top
# of every page, and the print dialog on load.

MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
# the accounts the Budgets page has a row for (budgets.js)
BUDGET_TYPES = ("expense", "income", "cogs")


def _account_words(db: Session, name: str, number, sep: str) -> str:
    """The account as the page names it: its number first when the company
    uses numbers (accountLabel in utils.js)."""
    from app.services.settings_service import use_account_numbers

    if number and use_account_numbers(db):
        return sep.join([str(number), name or ""])
    return name or ""


def _print_response(db: Session, section: dict) -> HTMLResponse:
    from app.routes._print import print_html
    from app.services.pdf_service import render_report_html
    from app.services.settings_service import get_all_settings

    html_str = render_report_html([section], get_all_settings(db), landscape=True)
    return HTMLResponse(content=print_html(html_str))


@router.get("/print", response_class=HTMLResponse)
def print_budgets(
    year: int = Query(default=None),
    account_id: int = Query(default=None),
    db: Session = Depends(get_db),
):
    """The Budgets sheet for a year, as a page that opens the print dialog:
    every account the page has a row for, its twelve months. No year is
    this year's sheet."""
    from app.routes.accounts import list_accounts
    from app.routes.reports.financial import _money

    year = year or date.today().year
    accounts = [a for a in list_accounts(db=db) if a.account_type in BUDGET_TYPES]
    if account_id:
        accounts = [a for a in accounts if a.id == account_id]
    amounts: dict = {}
    for b in list_budgets(year=year, account_id=account_id, db=db):
        amounts.setdefault(b.account_id, {})[b.month] = b.amount
    rows = []
    for a in accounts:
        mine = amounts.get(a.id, {})
        rows.append(
            {
                "cells": [_account_words(db, a.name, a.account_number, " ")]
                + [_money(mine[m]) if mine.get(m) else "" for m in range(1, 13)]
            }
        )
    return _print_response(
        db,
        {
            "title": "Budgets",
            "period": str(year),
            "columns": ["Account"] + MONTHS,
            "rows": rows,
            "note": None if rows else "No budgetable accounts found.",
        },
    )


@router.get("/variance/print", response_class=HTMLResponse)
def print_budget_variance(
    year: int = Query(...),
    db: Session = Depends(get_db),
):
    """Budget vs Actual for a year, as its dialog shows it, as a page that
    opens the print dialog: each account's Budget, Actual and Variance by
    month with the total."""
    from app.routes.reports.financial import _money

    data = budget_variance(year=year, db=db)
    rows = []
    for acct in data["accounts"]:
        rows.append(
            {
                "cells": [
                    _account_words(
                        db, acct["account_name"], acct["account_number"], " — "
                    )
                ]
                + [""] * 13,
                "style": "group",
            }
        )
        for label, key in (
            ("Budget", "budget"),
            ("Actual", "actual"),
            ("Variance", "variance"),
        ):
            rows.append(
                {
                    "cells": [label]
                    + [_money(m[key]) for m in acct["months"]]
                    + [_money(acct["total_" + key])],
                    "indent": 1,
                    **({"style": "subtotal"} if key == "variance" else {}),
                }
            )
    return _print_response(
        db,
        {
            "title": "Budget vs Actual",
            "period": str(year),
            "columns": [""] + MONTHS + ["Total"],
            "rows": rows,
            "note": (
                None if rows else "No budgets set for this year. Enter budgets first."
            ),
        },
    )
