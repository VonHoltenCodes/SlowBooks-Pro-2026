# ============================================================================
# Budget vs Actual — monthly budgets per account with variance reporting
# Phase 10: Quick Wins + Medium Effort Features
# ============================================================================

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
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
    existing = db.query(Budget).filter(
        Budget.account_id == data.account_id,
        Budget.year == data.year,
        Budget.month == data.month,
    ).first()
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
        existing = db.query(Budget).filter(
            Budget.account_id == item.account_id,
            Budget.year == item.year,
            Budget.month == item.month,
        ).first()
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
    from datetime import date

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
    account_info = {a.id: {"name": a.name, "number": a.account_number, "type": a.account_type.value} for a in accounts}

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
        .group_by(TransactionLine.account_id, sqlfunc.extract("month", Transaction.date))
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
            months.append({
                "month": m,
                "budget": budget_amt,
                "actual": round(actual_amt, 2),
                "variance": round(budget_amt - actual_amt, 2),
            })
        result.append({
            "account_id": acct_id,
            "account_name": info.get("name", ""),
            "account_number": info.get("number", ""),
            "account_type": info.get("type", ""),
            "months": months,
            "total_budget": sum(m["budget"] for m in months),
            "total_actual": sum(m["actual"] for m in months),
            "total_variance": sum(m["variance"] for m in months),
        })

    return {"year": year, "accounts": result}
