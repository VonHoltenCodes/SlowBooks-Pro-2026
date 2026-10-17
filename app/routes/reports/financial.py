from datetime import date
from decimal import Decimal
from typing import Annotated, Optional

from fastapi import Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session
from sqlalchemy import func as sqlfunc

from app.database import get_db
from app.models.accounts import Account, AccountType
from app.models.transactions import Transaction, TransactionLine
from app.routes.reports._router import router
from app.services.terminology import Terms, terms_from_db

# Debit-normal account types. For these, natural balance = debit - credit.
# For the rest (liability, equity, income), natural balance = credit - debit.
from app.services.bank_register import DEBIT_NORMAL as _DEBIT_NORMAL  # noqa: E402


def _in_class(db, class_id):
    """The class `class_id` (404 if there's none) and the SQL test for a
    posted line belonging to it: the line's own class, else its
    transaction's, else Uncategorized, as P&L by Class groups them (#213)."""
    from app.models.classes import TxnClass
    from app.services.classes_service import class_attribution, uncategorized_class_id

    cls = db.get(TxnClass, class_id)
    if cls is None:
        raise HTTPException(status_code=404, detail="Class not found")
    uncat_id = uncategorized_class_id(db)
    db.commit()
    return cls, class_attribution(uncat_id) == class_id


ACCOUNT_ORDERS = ("number", "name")
_SORT_QUERY = Query(
    default="number",
    pattern="^(number|name)$",
    description=(
        "The accounts' order: by number, then name, as the Trial Balance "
        "lists them, or by name; a sub-account right after its parent either "
        "way (#326)"
    ),
)


def _chart_index(db) -> dict:
    """Every account's number, name and parent, by id."""
    return {
        a.id: (a.account_number or "", a.name or "", a.parent_id)
        for a in db.query(
            Account.id, Account.account_number, Account.name, Account.parent_id
        )
    }


def _chain(chart: dict, account_id) -> list:
    """The account's ids from the root down, the account itself last."""
    path = []
    while account_id in chart and len(path) < 32:
        path.append(account_id)
        account_id = chart[account_id][2]
    return list(reversed(path))


def _account_order(db, sort="number", chart: dict | None = None):
    """A sort key for a report's account rows: by number then name (an
    account with no number after the numbered ones, by name), or by name;
    a sub-account right after its parent either way. The P&L and Balance
    Sheet listed accounts in the order they were created (#326,
    @TigerCoder27): the query had no ORDER BY, so a chart added to after
    seeding read 6000, 6200, 6600, 6900, 6250, 6410. The Trial Balance and
    General Ledger take the same key: ORDER BY account_number put the
    accounts with no number first, in creation order (2.23.4 gate, W-7)."""
    chart = chart if chart is not None else _chart_index(db)

    def level(account_id):
        number, name, _ = chart[account_id]
        name = name.lower()
        if sort == "name":
            return (0, name, number)
        return (0, number, name) if number else (1, "", name)

    def key(row):
        path = tuple(level(a) for a in _chain(chart, row["account_id"]))
        return path or ((2, "", ""),)

    return key


def _account_words(chart: dict, account_id) -> tuple[str, str]:
    """(path, label) for a report row: the path names a sub-account under
    its parents ("Automobile: Insurance"), the label puts the number
    first ("6310 - Automobile: Insurance"). A statement row read as the
    bare leaf, so two "Insurance" rows and a "Fuel" with no Automobile
    above it could not be told apart (2.23.4 gate, W-8)."""
    chain = _chain(chart, account_id)
    if not chain:
        return "", ""
    path = ": ".join(chart[a][1] for a in chain)
    number = chart[account_id][0]
    return path, (f"{number} - {path}" if number else path)


def _totals_by_account(
    db, acct_type, date_start=None, date_end=None, in_class=None, sort="number"
):
    """Return a list of {account_id, account_name, account_number, amount}
    rows where amount is signed by the account type's natural balance
    (always positive for a normal-balance ledger), in the chart's order
    (`sort`: number or name, see _account_order).

    `account_id` is included so the SPA can drill into /account-transactions
    for any line — Phase 11 drill-down support.
    """
    q = (
        db.query(
            Account.id,
            Account.name,
            Account.account_number,
            sqlfunc.coalesce(sqlfunc.sum(TransactionLine.debit), 0),
            sqlfunc.coalesce(sqlfunc.sum(TransactionLine.credit), 0),
        )
        .join(TransactionLine, TransactionLine.account_id == Account.id)
        .join(Transaction, TransactionLine.transaction_id == Transaction.id)
        .filter(Account.account_type == acct_type)
    )
    if date_start is not None:
        q = q.filter(Transaction.date >= date_start)
    if date_end is not None:
        q = q.filter(Transaction.date <= date_end)
    if in_class is not None:
        q = q.filter(in_class)
    q = q.group_by(Account.id, Account.name, Account.account_number)

    rows = []
    chart = _chart_index(db)
    for acct_id, name, number, dr, cr in q.all():
        amount = (dr - cr) if acct_type in _DEBIT_NORMAL else (cr - dr)
        path, label = _account_words(chart, acct_id)
        rows.append(
            {
                "account_id": acct_id,
                "account_name": name,
                "account_number": number,
                "account_path": path or name,
                "account_label": label or name,
                "amount": float(amount),
            }
        )
    rows.sort(key=_account_order(db, sort, chart))
    return rows


@router.get("/profit-loss")
def profit_loss(
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    db: Session = Depends(get_db),
    # after db, and a plain None by default: other routes call this
    # directly, with db as the third argument
    class_id: Annotated[
        Optional[int], Query(description="Only this class's lines (#213); omit for all")
    ] = None,
    sort: str = _SORT_QUERY,
):
    if not start_date:
        start_date = date(date.today().year, 1, 1)
    if not end_date:
        end_date = date.today()
    cls, in_class = _in_class(db, class_id) if class_id is not None else (None, None)

    income = _totals_by_account(
        db, AccountType.INCOME, start_date, end_date, in_class, sort
    )
    cogs = _totals_by_account(
        db, AccountType.COGS, start_date, end_date, in_class, sort
    )
    expenses = _totals_by_account(
        db, AccountType.EXPENSE, start_date, end_date, in_class, sort
    )

    total_income = sum(i["amount"] for i in income)
    total_cogs = sum(c["amount"] for c in cogs)
    total_expenses = sum(e["amount"] for e in expenses)

    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "class_id": cls.id if cls else None,
        "class_name": cls.name if cls else None,
        "sort": sort,
        "income": income,
        "cogs": cogs,
        "expenses": expenses,
        "total_income": total_income,
        "total_cogs": total_cogs,
        "gross_profit": total_income - total_cogs,
        "total_expenses": total_expenses,
        "net_income": total_income - total_cogs - total_expenses,
    }


@router.get("/balance-sheet")
def balance_sheet(
    as_of_date: date = Query(default=None),
    db: Session = Depends(get_db),
    sort: str = _SORT_QUERY,
):
    t = terms_from_db(db)
    if not as_of_date:
        as_of_date = date.today()

    assets = _totals_by_account(db, AccountType.ASSET, date_end=as_of_date, sort=sort)
    liabilities = _totals_by_account(
        db, AccountType.LIABILITY, date_end=as_of_date, sort=sort
    )
    equity = _totals_by_account(db, AccountType.EQUITY, date_end=as_of_date, sort=sort)

    total_assets = sum(a["amount"] for a in assets)
    total_liabilities = sum(liab["amount"] for liab in liabilities)
    total_equity = sum(e["amount"] for e in equity)

    # Net income for all periods up to as_of_date flows into equity as retained
    # earnings. Without this, the balance sheet fails to balance whenever there
    # is income or expense activity that hasn't been formally closed into an
    # equity account (which is the normal state in this app — income/expense
    # accounts are never explicitly closed).
    income_rows = _totals_by_account(db, AccountType.INCOME, date_end=as_of_date)
    cogs_rows = _totals_by_account(db, AccountType.COGS, date_end=as_of_date)
    expense_rows = _totals_by_account(db, AccountType.EXPENSE, date_end=as_of_date)
    net_income = (
        sum(r["amount"] for r in income_rows)
        - sum(r["amount"] for r in cogs_rows)
        - sum(r["amount"] for r in expense_rows)
    )

    if net_income != 0:
        equity = list(equity) + [
            {
                "account_id": None,
                "account_name": f"{t('Net Income')} (current period)",
                "account_number": None,
                "account_path": f"{t('Net Income')} (current period)",
                "account_label": f"{t('Net Income')} (current period)",
                "amount": float(net_income),
            }
        ]
        total_equity += net_income

    return {
        "as_of_date": as_of_date.isoformat(),
        "sort": sort,
        "assets": assets,
        "liabilities": liabilities,
        "equity": equity,
        "total_assets": total_assets,
        "total_liabilities": total_liabilities,
        "total_equity": total_equity,
    }


@router.get("/general-ledger")
def general_ledger(
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    account_id: int = Query(default=None),
    db: Session = Depends(get_db),
    # after db, and a plain None by default: the csv and pdf routes call
    # this directly with db as the fourth argument
    class_id: Annotated[
        Optional[int],
        Query(description="Only this class's lines (#236); omit for all"),
    ] = None,
):
    """General Ledger detail report. With `class_id`, only the lines of
    that class (the line's own, else its transaction's, else Uncategorized,
    as P&L by Class groups them), the balance brought forward included —
    computed under the same test, or the running balances would be wrong
    (#236)."""
    if not start_date:
        start_date = date(date.today().year, 1, 1)
    if not end_date:
        end_date = date.today()
    cls, in_class = _in_class(db, class_id) if class_id is not None else (None, None)

    q = (
        db.query(TransactionLine, Transaction, Account)
        .join(Transaction, TransactionLine.transaction_id == Transaction.id)
        .join(Account, TransactionLine.account_id == Account.id)
        .filter(Transaction.date >= start_date, Transaction.date <= end_date)
    )
    if account_id:
        q = q.filter(TransactionLine.account_id == account_id)
    if in_class is not None:
        q = q.filter(in_class)

    # date, then posting order: a stable order is what makes a running
    # balance mean the same thing on screen and in an export (#179)
    q = q.order_by(
        Account.account_number, Transaction.date, Transaction.id, TransactionLine.id
    )
    results = q.all()

    # Balance brought forward per account: everything posted before the
    # period. Balances read in the account's natural sign, as the balance
    # sheet and the overview show them: a debit-normal account (asset,
    # expense, COGS) is debit minus credit, every other account credit minus
    # debit, so a payable you owe reads positive. The debit and credit
    # columns are untouched, so period Dr - Cr still equals the TB's Net.
    ids = {acct.id for _, _, acct in results}
    opening = {}
    if ids:
        before = (
            db.query(
                TransactionLine.account_id,
                sqlfunc.coalesce(sqlfunc.sum(TransactionLine.debit), 0),
                sqlfunc.coalesce(sqlfunc.sum(TransactionLine.credit), 0),
            )
            .join(Transaction, TransactionLine.transaction_id == Transaction.id)
            .filter(Transaction.date < start_date, TransactionLine.account_id.in_(ids))
        )
        if in_class is not None:
            before = before.filter(in_class)
        for acct_id, dr, cr in before.group_by(TransactionLine.account_id).all():
            opening[acct_id] = Decimal(str(dr)) - Decimal(str(cr))

    def _sign(acct):
        return 1 if acct.account_type in _DEBIT_NORMAL else -1

    # each line's class, as P&L by Class attributes it (#213): its own,
    # else its transaction's, else the system's Uncategorized
    from app.models.classes import TxnClass

    all_classes = db.query(TxnClass).all()
    class_names = {c.id: c.name for c in all_classes}
    uncat_name = next(
        (c.name for c in all_classes if c.is_system_default), "Uncategorized"
    )

    entries_by_account = {}
    chart = _chart_index(db)
    for tl, txn, acct in results:
        key = acct.id
        if key not in entries_by_account:
            path, label = _account_words(chart, acct.id)
            entries_by_account[key] = {
                "account_id": acct.id,
                "account_number": acct.account_number,
                "account_name": acct.name,
                "account_path": path or acct.name,
                "account_label": label or acct.name,
                "account_type": acct.account_type.value,
                "normal_balance": "debit" if _sign(acct) > 0 else "credit",
                "opening_balance": _sign(acct) * opening.get(acct.id, Decimal(0)),
                "entries": [],
                "total_debit": Decimal(0),
                "total_credit": Decimal(0),
                "_running": _sign(acct) * opening.get(acct.id, Decimal(0)),
            }
        a = entries_by_account[key]
        a["_running"] += _sign(acct) * (tl.debit - tl.credit)
        a["entries"].append(
            {
                "date": txn.date.isoformat(),
                "description": txn.description or tl.description or "",
                "reference": txn.reference or "",
                "debit": float(tl.debit),
                "credit": float(tl.credit),
                "running_balance": float(a["_running"]),
                "source_type": txn.source_type or "journal",
                "class_name": class_names.get(tl.class_id or txn.class_id)
                or uncat_name,
            }
        )
        a["total_debit"] += tl.debit
        a["total_credit"] += tl.credit

    accounts_list = list(entries_by_account.values())
    # the statements' order: by number then name, the unnumbered last, a
    # sub-account after its parent (ORDER BY account_number put the
    # unnumbered first: 2.23.4 gate, W-7)
    accounts_list.sort(key=_account_order(db, chart=chart))
    for a in accounts_list:
        a["closing_balance"] = float(a.pop("_running"))
        a["opening_balance"] = float(a["opening_balance"])
        a["total_debit"] = float(a["total_debit"])
        a["total_credit"] = float(a["total_credit"])

    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "account_id": account_id or None,
        "class_id": cls.id if cls else None,
        "class_name": cls.name if cls else None,
        "accounts": accounts_list,
    }


@router.get("/account-transactions")
def account_transactions(
    account_id: int = Query(..., description="Account to drill into"),
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    db: Session = Depends(get_db),
    # after db, and a plain None by default: other routes call this
    # directly, with db as the third argument
    class_id: Annotated[
        Optional[int], Query(description="Only this class's lines (#213); omit for all")
    ] = None,
    job_id: Annotated[
        Optional[int],
        Query(description="Only this job's lines (#242); 0 for the lines with no job"),
    ] = None,
):
    """Phase 11: drill-down support. Every journal entry line hitting a
    given account in the date range, with source document linkage so the
    UI can jump from a P&L row straight to the underlying invoice/bill/JE.
    The register service (bank_register.account_register) does the work —
    the bank register is the same view."""
    from app.services.bank_register import account_register

    acct = db.query(Account).filter(Account.id == account_id).first()
    if not acct:
        raise HTTPException(status_code=404, detail="Account not found")
    if not start_date:
        start_date = date(date.today().year, 1, 1)
    if not end_date:
        end_date = date.today()
    cls = _in_class(db, class_id)[0] if class_id is not None else None
    job_name = None
    if job_id is not None:
        if job_id == 0:
            from app.services.jobs_service import NO_JOB_LABEL

            job_name = terms_from_db(db).text(NO_JOB_LABEL)
        else:
            from app.models.jobs import Job

            job = db.get(Job, job_id)
            if job is None:
                raise HTTPException(status_code=404, detail="Job not found")
            job_name = job.name
    out = account_register(
        db, acct, start_date, end_date, class_id=class_id, job_id=job_id
    )
    out["start_date"] = start_date.isoformat()
    out["end_date"] = end_date.isoformat()
    out["class_id"] = cls.id if cls else None
    out["class_name"] = cls.name if cls else None
    out["job_id"] = job_id
    out["job_name"] = job_name
    return out


# ============================================================================
# Phase 10: Quick Wins — Trial Balance, Cash Flow, Batch Email,
#            Collection Letters, 1099 Summary
# ============================================================================


@router.get("/trial-balance")
def trial_balance(
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    db: Session = Depends(get_db),
):
    """Trial Balance: sum all debits/credits per account for a date range."""
    if not start_date:
        start_date = date(date.today().year, 1, 1)
    if not end_date:
        end_date = date.today()

    results = (
        db.query(
            Account.id,
            Account.account_number,
            Account.name,
            Account.account_type,
            sqlfunc.coalesce(sqlfunc.sum(TransactionLine.debit), 0),
            sqlfunc.coalesce(sqlfunc.sum(TransactionLine.credit), 0),
        )
        .join(TransactionLine, TransactionLine.account_id == Account.id)
        .join(Transaction, TransactionLine.transaction_id == Transaction.id)
        .filter(Transaction.date >= start_date, Transaction.date <= end_date)
        .group_by(
            Account.id, Account.account_number, Account.name, Account.account_type
        )
        .all()
    )

    items = []
    total_debit = Decimal(0)
    total_credit = Decimal(0)
    chart = _chart_index(db)
    for acct_id, acct_num, acct_name, acct_type, debit, credit in results:
        total_debit += debit
        total_credit += credit
        path, label = _account_words(chart, acct_id)
        items.append(
            {
                "account_id": acct_id,
                "account_number": acct_num or "",
                "account_name": acct_name,
                "account_path": path or acct_name,
                "account_label": label or acct_name,
                "account_type": acct_type.value,
                "total_debit": float(debit),
                "total_credit": float(credit),
                "net_balance": float(debit - credit),
            }
        )
    # by number then name, the unnumbered last, a sub-account after its
    # parent (ORDER BY account_number put the unnumbered first: W-7); each
    # row named by its path and number, as the statements' are (W-12)
    items.sort(key=_account_order(db, chart=chart))

    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "items": items,
        "total_debit": float(total_debit),
        "total_credit": float(total_credit),
        "difference": float(total_debit - total_credit),
    }


@router.get("/cash-flow")
def cash_flow(
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    db: Session = Depends(get_db),
):
    """Statement of cash flows, indirect method: net income, non-cash
    adjustments (depreciation), changes in working capital, then investing
    and financing; the net change is the change in the bank accounts
    (app/services/cash_flow.py)."""
    from app.services.cash_flow import statement_of_cash_flows

    if not start_date:
        start_date = date(date.today().year, 1, 1)
    if not end_date:
        end_date = date.today()
    return statement_of_cash_flows(
        db, start_date, end_date, terms_from_db(db)("Net Income")
    )


_PL_TYPES = (AccountType.INCOME, AccountType.COGS, AccountType.EXPENSE)


def _ids_param(values, name: str) -> Optional[set[int]]:
    """?class_ids=3&class_ids=5, or ?class_ids=3,5 (the form an address or a
    saved report carries) → {3, 5}; None when nothing was asked. A value
    that is not a number is a 400, not a column that is quietly dropped."""
    if not values:
        return None
    out: set[int] = set()
    for v in values:
        for part in str(v).split(","):
            part = part.strip()
            if not part:
                continue
            if not part.lstrip("-").isdigit():
                raise HTTPException(
                    status_code=400, detail=f"{name}: {part!r} is not an id"
                )
            out.add(int(part))
    return out or None


def _pl_pivot(
    db,
    start_date: date,
    end_date: date,
    key_expr,
    meta: dict,
    first_key,
    *,
    chosen: Optional[set] = None,
    show: str = "all",
    include_empty: bool = False,
    restrict: Optional[set] = None,
) -> dict:
    """P&L accounts down the side, one column per value of a dimension
    (class, job) across, from the posted lines of the period.

    `key_expr` is the SQL for a line's value (class_attribution,
    job_attribution); `meta` names every value the dimension has, inactive
    ones included: {key: {"name", "inactive"}}; `first_key` is the column
    that leads (Uncategorized, No job), the rest alphabetical.

    Which columns (R3, #233): every value with activity, unless `chosen`
    names a subset, `show` is "active" (an archived class or inactive job
    is left out), or `include_empty` adds the values with no activity as
    zero columns; `restrict` (one customer's jobs) is the most any column
    can be. A chosen value with nothing in the period is no column unless
    `include_empty` asks for the empty ones; it is named in `chosen_empty`
    instead, so a zero column is never counted as a class shown (NEW-26,
    W-1), and every column says whether it is `empty`. When a value with
    activity is left out the result is `filtered`, its totals are the
    columns shown, and `unfiltered` carries the company's totals, so a
    partial Net Income is never read as the P&L's.

    An account that nets to nothing in every column shown is left out.
    """
    rows = (
        db.query(
            key_expr.label("k"),
            Account.id,
            Account.account_number,
            Account.name,
            Account.account_type,
            sqlfunc.coalesce(sqlfunc.sum(TransactionLine.debit), 0),
            sqlfunc.coalesce(sqlfunc.sum(TransactionLine.credit), 0),
        )
        .select_from(Transaction)
        .join(TransactionLine, TransactionLine.transaction_id == Transaction.id)
        .join(Account, TransactionLine.account_id == Account.id)
        .filter(
            Account.account_type.in_(_PL_TYPES),
            Transaction.date >= start_date,
            Transaction.date <= end_date,
        )
        .group_by(
            "k",
            Account.id,
            Account.account_number,
            Account.name,
            Account.account_type,
        )
        .all()
    )

    by_key: dict = {}
    # each account's amount in each column: the rows of the report (#213)
    by_account: dict[int, dict] = {}
    for key, acct_id, number, name, acct_type, dr, cr in rows:
        acct = by_account.setdefault(
            acct_id,
            {
                "id": acct_id,
                "number": number,
                "name": name,
                "type": acct_type,
                "by": {},
            },
        )
        dr, cr = Decimal(str(dr)), Decimal(str(cr))
        natural = (cr - dr) if acct_type == AccountType.INCOME else (dr - cr)
        acct["by"][key] = acct["by"].get(key, Decimal("0")) + natural
        bucket = by_key.setdefault(
            key,
            {"income": Decimal("0"), "cogs": Decimal("0"), "expenses": Decimal("0")},
        )
        if acct_type == AccountType.INCOME:
            bucket["income"] += cr - dr
        elif acct_type == AccountType.COGS:
            bucket["cogs"] += dr - cr
        else:
            bucket["expenses"] += dr - cr

    def _meta(key):
        return meta.get(key) or {"name": "Unknown", "inactive": False}

    def allowed(key):
        if restrict is not None and key not in restrict:
            return False
        if chosen is not None:
            return key in chosen
        if show == "active" and _meta(key)["inactive"]:
            return False
        return True

    with_activity = set(by_key)
    column_keys = {k for k in with_activity if allowed(k)}
    if include_empty:
        column_keys |= {k for k in meta if allowed(k)}
    chosen_empty = sorted(
        _meta(k)["name"]
        for k in (chosen or ())
        if k in meta and allowed(k) and k not in with_activity
    )
    filtered = bool(with_activity - column_keys)

    def totals_of(keys):
        t = {"income": Decimal("0"), "cogs": Decimal("0"), "expenses": Decimal("0")}
        for k in keys:
            b = by_key.get(k)
            if b:
                for f in t:
                    t[f] += b[f]
        return {
            "income": float(t["income"]),
            "cogs": float(t["cogs"]),
            "gross_profit": float(t["income"] - t["cogs"]),
            "expenses": float(t["expenses"]),
            "net_income": float(t["income"] - t["cogs"] - t["expenses"]),
        }

    columns = []
    for key in sorted(
        column_keys, key=lambda k: (k != first_key, _meta(k)["name"].lower())
    ):
        columns.append(
            {
                "key": key,
                "name": _meta(key)["name"],
                "inactive": bool(_meta(key)["inactive"]),
                "empty": key not in with_activity,
                **totals_of([key]),
            }
        )

    # Accounts down the side, an amount per column in the order of
    # `columns` and the account's total, by section.
    order = [c["key"] for c in columns]
    section_of = {
        AccountType.INCOME: "income",
        AccountType.COGS: "cogs",
        AccountType.EXPENSE: "expenses",
    }
    accounts: dict[str, list] = {"income": [], "cogs": [], "expenses": []}
    for a in sorted(
        by_account.values(), key=lambda a: (a["number"] or "", a["name"].lower())
    ):
        amounts = [a["by"].get(k, Decimal("0")) for k in order]
        if not any(amounts):
            continue
        accounts[section_of[a["type"]]].append(
            {
                "account_id": a["id"],
                "account_number": a["number"],
                "account_name": a["name"],
                "amounts": [float(x) for x in amounts],
                "total": float(sum(amounts, Decimal("0"))),
            }
        )

    shown = totals_of(order)
    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "columns": columns,
        "accounts": accounts,
        "total_income": shown["income"],
        "total_cogs": shown["cogs"],
        "total_gross_profit": shown["gross_profit"],
        "total_expenses": shown["expenses"],
        "total_net_income": shown["net_income"],
        "filtered": filtered,
        "columns_total": len(with_activity),
        "chosen_empty": chosen_empty,
        "unfiltered": totals_of(with_activity),
    }


def _period(start_date, end_date):
    if not start_date:
        start_date = date(date.today().year, 1, 1)
    if not end_date:
        end_date = date.today()
    return start_date, end_date


@router.get("/profit-loss-by-class")
def profit_loss_by_class(
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    db: Session = Depends(get_db),
    class_ids: Annotated[
        Optional[list[str]],
        Query(
            description="Only these classes (repeat it, or 3,5,7); each gets a column"
        ),
    ] = None,
    show: Annotated[
        str, Query(description="all (archived classes too) or active")
    ] = "all",
    include_empty: Annotated[
        bool, Query(description="A column for every class, activity or not")
    ] = False,
):
    """P&L split by the class dimension on each posted line.

    A line's own class wins, then the transaction header's; untagged
    activity groups with the system-default "Uncategorized" class so every
    posting is accounted for and the column totals reconcile with the
    plain Profit & Loss. With class_ids, show=active or include_empty the
    columns are chosen (R3, #233): a view that leaves out a class with
    activity says `filtered` and its totals are the columns shown.
    """
    from app.models.classes import TxnClass
    from app.services.classes_service import class_attribution, uncategorized_class_id

    start_date, end_date = _period(start_date, end_date)
    if show not in ("all", "active"):
        raise HTTPException(status_code=400, detail="show must be all or active")
    chosen = _ids_param(class_ids, "class_ids")
    uncat_id = uncategorized_class_id(db)
    db.commit()
    meta = {
        c.id: {"name": c.name, "inactive": bool(c.is_archived)}
        for c in db.query(TxnClass).all()
    }
    out = _pl_pivot(
        db,
        start_date,
        end_date,
        class_attribution(uncat_id),
        meta,
        uncat_id,
        chosen=chosen,
        show=show,
        include_empty=include_empty,
    )
    out["classes"] = [
        {
            "class_id": c["key"],
            "class_name": c["name"],
            "archived": c["inactive"],
            "empty": c["empty"],
            "income": c["income"],
            "cogs": c["cogs"],
            "gross_profit": c["gross_profit"],
            "expenses": c["expenses"],
            "net_income": c["net_income"],
        }
        for c in out.pop("columns")
    ]
    out["filter"] = {
        "show": show,
        "class_ids": sorted(chosen) if chosen else [],
        "include_empty": include_empty,
    }
    return out


@router.get("/profit-loss-by-job")
def profit_loss_by_job(
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    db: Session = Depends(get_db),
    job_ids: Annotated[
        Optional[list[str]],
        Query(
            description="Only these jobs (repeat it, or 3,5,7); 0 is the No job column"
        ),
    ] = None,
    customer_id: Annotated[
        Optional[int], Query(description="Only this customer's jobs")
    ] = None,
    show: Annotated[
        str, Query(description="all (inactive jobs too) or active")
    ] = "all",
    include_empty: Annotated[
        bool, Query(description="A column for every job, activity or not")
    ] = False,
):
    """P&L split by job (R13, #242): every P&L account down the side, a
    column per job and a "No job" column first, as P&L by Class lays it
    out. A line's job is its own, else its transaction's; untagged
    activity is "No job" — which also holds the applied-cost credits
    behind Job Cost Entries, so its costs can be negative — and the column
    totals equal the plain Profit & Loss, the promise Job Profitability
    makes. The columns are chosen as P&L by Class's are (#233); a
    customer narrows them to that customer's jobs.
    """
    from app.models.jobs import Job
    from app.services.jobs_service import NO_JOB_LABEL, job_attribution

    start_date, end_date = _period(start_date, end_date)
    if show not in ("all", "active"):
        raise HTTPException(status_code=400, detail="show must be all or active")
    chosen = _ids_param(job_ids, "job_ids")
    if chosen is not None:
        # 0 names the No job column (its key is None)
        chosen = {None if k == 0 else k for k in chosen}
    t = terms_from_db(db)
    jobs = db.query(Job).all()
    meta = {
        j.id: {
            "name": j.name,
            "inactive": not j.is_active,
            "customer_id": j.customer_id,
            "customer_name": j.customer.name if j.customer else "",
        }
        for j in jobs
    }
    meta[None] = {
        "name": t.text(NO_JOB_LABEL),
        "inactive": False,
        "customer_id": None,
        "customer_name": "",
    }
    restrict = None
    if customer_id is not None:
        restrict = {k for k, m in meta.items() if m["customer_id"] == customer_id}
    out = _pl_pivot(
        db,
        start_date,
        end_date,
        job_attribution(),
        meta,
        None,
        chosen=chosen,
        show=show,
        include_empty=include_empty,
        restrict=restrict,
    )
    out["jobs"] = [
        {
            "job_id": c["key"],
            "job_name": c["name"],
            "customer_id": meta[c["key"]]["customer_id"],
            "customer_name": meta[c["key"]]["customer_name"],
            "inactive": c["inactive"],
            "empty": c["empty"],
            "income": c["income"],
            "cogs": c["cogs"],
            "gross_profit": c["gross_profit"],
            "expenses": c["expenses"],
            "net_income": c["net_income"],
        }
        for c in out.pop("columns")
    ]
    out["filter"] = {
        "show": show,
        "job_ids": sorted((0 if k is None else k) for k in chosen) if chosen else [],
        "customer_id": customer_id,
        "include_empty": include_empty,
    }
    return out


def _by_job_data(db, start_date, end_date, job_ids, customer_id, show, include_empty):
    data = profit_loss_by_job(
        start_date,
        end_date,
        db,
        job_ids=job_ids,
        customer_id=customer_id,
        show=show,
        include_empty=include_empty,
    )
    t = terms_from_db(db)
    title = f"{t('P&L')} by {t('Job')}"
    columns = [dict(c, name=c["job_name"]) for c in data["jobs"]]
    data["report_name"] = title
    data["dimension"] = t("Job")
    return data, title, columns, t


@router.get("/profit-loss-by-job/pdf")
def profit_loss_by_job_pdf(
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    db: Session = Depends(get_db),
    job_ids: Optional[list[str]] = Query(default=None),
    customer_id: Optional[int] = None,
    show: str = "all",
    include_empty: bool = False,
):
    data, title, columns, t = _by_job_data(
        db, start_date, end_date, job_ids, customer_id, show, include_empty
    )
    return _pdf_response(
        _grid_sections(data, title, columns, t),
        db,
        f"{t.slug('P&L')}-by-{t.slug('Job')}_{data['start_date']}_{data['end_date']}.pdf",
        landscape=True,
    )


@router.get("/profit-loss-by-job/csv")
def profit_loss_by_job_csv_route(
    request: Request,
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    db: Session = Depends(get_db),
    job_ids: Optional[list[str]] = Query(default=None),
    customer_id: Optional[int] = None,
    show: str = "all",
    include_empty: bool = False,
    layout: str = Query(default="wide"),
):
    from app.services.ledger_exports import profit_loss_by_class_csv

    data, title, columns, t = _by_job_data(
        db, start_date, end_date, job_ids, customer_id, show, include_empty
    )
    return _csv_download(
        profit_loss_by_class_csv(data, columns, _company_name(db), t, layout=layout),
        f"{t.slug('P&L')}-by-{t.slug('Job')}_{data['start_date']}_{data['end_date']}.csv",
        request,
    )


@router.get("/job-profitability")
def job_profitability_report(
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    customer_id: int = Query(default=None),
    db: Session = Depends(get_db),
):
    """Income, costs and margin per job from posted lines.

    A line's job is its own job_id, else its transaction's; untagged
    activity is the "No job" row, so the totals equal the plain Profit &
    Loss for the same period (the same reconciliation promise as P&L by
    Class).
    """
    from app.services.jobs_service import job_profitability

    if not start_date:
        start_date = date(date.today().year, 1, 1)
    if not end_date:
        end_date = date.today()
    rows = job_profitability(db, start_date, end_date, customer_id=customer_id)
    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "jobs": rows,
        "total_income": sum(r["income"] for r in rows),
        "total_costs": sum(r["total_costs"] for r in rows),
        "total_net_income": sum(r["net_income"] for r in rows),
    }


# ── Financial-report PDFs ────────────────────────────────────────────────
# One shared renderer (report_pdf.html + _report_theme.html) with the
# pdf_paper_size setting (letter default, a4 selectable). The statements
# pack bundles P&L + Balance Sheet + Trial Balance into one document.


def _money(value) -> str:
    amount = Decimal(str(value or 0))
    sign = "-" if amount < 0 else ""
    return f"{sign}${abs(amount):,.2f}"


def _row_words(item: dict) -> str:
    """A statement row's words on the PDF: its number and path (W-8)."""
    return item.get("account_label") or item["account_name"]


def _pl_section(data: dict, t=None) -> dict:
    """P&L rows for the PDF; t (Terms) picks the company's words —
    "Statement of Activities" / "Revenue & Support" for a nonprofit. One
    class's P&L (class_id on the data, #232) is titled with the class and
    says it is that class only."""
    t = t or Terms()
    rows = []
    for label, key, total_key in (
        (t("Income"), "income", "total_income"),
        ("Cost of Goods Sold", "cogs", "total_cogs"),
    ):
        rows.append({"cells": [label, ""], "style": "subtotal"})
        for item in data[key]:
            rows.append({"cells": [f"  {_row_words(item)}", _money(item["amount"])]})
        rows.append(
            {"cells": [f"Total {label}", _money(data[total_key])], "style": "subtotal"}
        )
    rows.append(
        {"cells": ["Gross Profit", _money(data["gross_profit"])], "style": "subtotal"}
    )
    rows.append({"cells": ["Expenses", ""], "style": "subtotal"})
    for item in data["expenses"]:
        rows.append({"cells": [f"  {_row_words(item)}", _money(item["amount"])]})
    rows.append(
        {
            "cells": ["Total Expenses", _money(data["total_expenses"])],
            "style": "subtotal",
        }
    )
    rows.append(
        {"cells": [t("Net Income"), _money(data["net_income"])], "style": "grand-total"}
    )
    cls = data.get("class_name")
    return {
        "title": t("Profit & Loss") + (f" — {cls}" if cls else ""),
        "period": f"{data['start_date']} — {data['end_date']}"
        + (f" · {t('Class')}: {cls} only, not the company total" if cls else ""),
        "columns": ["", "Amount"],
        "rows": rows,
    }


# A page holds this many class or job columns beside Account and Total;
# past it the grid goes over several pages, each with the Account column
# and the same Total column (the total across every column shown).
GRID_COLUMNS_PER_PAGE = 8


def _grid_sections(data: dict, title: str, columns: list, t=None) -> list[dict]:
    """The P&L by Class / by Job grid as report sections for the PDF:
    Account | one column per class or job | Total, the sections and
    subtotals of the screen. `columns` are the grid's {name, income, cogs,
    gross_profit, expenses, net_income} in order, aligned with each
    account's amounts[]. More than GRID_COLUMNS_PER_PAGE columns go over
    several landscape pages, numbered in the period line."""
    t = t or Terms()
    total_label = "Total (shown)" if data.get("filtered") else "Total"
    pages = [
        list(range(i, min(i + GRID_COLUMNS_PER_PAGE, len(columns))))
        for i in range(0, len(columns), GRID_COLUMNS_PER_PAGE)
    ] or [[]]
    sections = []
    for page_no, idx in enumerate(pages, start=1):
        cols = [columns[i] for i in idx]

        def row(label, cells, style=None):
            r = {"cells": [label] + cells}
            if style:
                r["style"] = style
            return r

        def account_row(a):
            return row(
                f"  {a['account_number'] + ' - ' if a['account_number'] else ''}{a['account_name']}",
                [_money(a["amounts"][i]) if a["amounts"][i] else "" for i in idx]
                + [_money(a["total"])],
            )

        def sum_row(label, key, total, style):
            return row(label, [_money(c[key]) for c in cols] + [_money(total)], style)

        rows = []
        for label, key in (
            (t("Income"), "income"),
            ("Cost of Goods Sold", "cogs"),
            ("Expenses", "expenses"),
        ):
            rows.append(row(label, [""] * (len(cols) + 1), "subtotal"))
            rows.extend(account_row(a) for a in data["accounts"][key])
            if key == "income":
                rows.append(
                    sum_row(
                        t("Total Income"), "income", data["total_income"], "subtotal"
                    )
                )
            elif key == "cogs":
                rows.append(
                    sum_row(
                        "Gross Profit",
                        "gross_profit",
                        data["total_gross_profit"],
                        "subtotal",
                    )
                )
            else:
                rows.append(
                    sum_row(
                        "Total Expenses", "expenses", data["total_expenses"], "subtotal"
                    )
                )
        rows.append(
            sum_row(
                t("Net Income"), "net_income", data["total_net_income"], "grand-total"
            )
        )
        period = f"{data['start_date']} — {data['end_date']}"
        if len(pages) > 1:
            period += (
                f" · columns {idx[0] + 1}–{idx[-1] + 1} of {len(columns)}"
                f" (page {page_no} of {len(pages)}; Total is across every column)"
            )
        if data.get("filtered"):
            from app.services.ledger_exports import filtered_phrase

            period += " · " + filtered_phrase(data, columns)
        sections.append(
            {
                "title": title,
                "period": period,
                "columns": ["Account"] + [c["name"] for c in cols] + [total_label],
                "rows": rows,
            }
        )
    return sections


def _bs_section(data: dict, t=None) -> dict:
    t = t or Terms()
    rows = []
    for label, key, total_key in (
        ("Assets", "assets", "total_assets"),
        ("Liabilities", "liabilities", "total_liabilities"),
        (t("Equity"), "equity", "total_equity"),
    ):
        rows.append({"cells": [label, ""], "style": "subtotal"})
        for item in data[key]:
            rows.append({"cells": [f"  {_row_words(item)}", _money(item["amount"])]})
        rows.append(
            {"cells": [f"Total {label}", _money(data[total_key])], "style": "subtotal"}
        )
    rows.append(
        {
            "cells": [
                t("Liabilities + Equity"),
                _money(data["total_liabilities"] + data["total_equity"]),
            ],
            "style": "grand-total",
        }
    )
    return {
        "title": t("Balance Sheet"),
        "period": f"As of {data['as_of_date']}",
        "columns": ["", "Amount"],
        "rows": rows,
    }


def _tb_section(data: dict) -> dict:
    rows = [
        {
            "cells": [
                _row_words(item),  # the number and path, as the statements (W-12)
                _money(item["total_debit"]),
                _money(item["total_credit"]),
            ]
        }
        for item in data["items"]
    ]
    rows.append(
        {
            "cells": [
                "Total",
                _money(data["total_debit"]),
                _money(data["total_credit"]),
            ],
            "style": "grand-total",
        }
    )
    return {
        "title": "Trial Balance",
        "period": f"{data['start_date']} — {data['end_date']}",
        "columns": ["Account", "Debit", "Credit"],
        "rows": rows,
    }


def _gl_section(data: dict) -> dict:
    rows = []
    for a in data["accounts"]:
        head = _row_words(a)  # the number and path, as the statements (W-12)
        rows.append({"cells": [head, "", "", "", "", ""], "style": "subtotal"})
        rows.append(
            {
                "cells": [
                    "",
                    "",
                    "Balance brought forward",
                    "",
                    "",
                    _money(a["opening_balance"]),
                ]
            }
        )
        for e in a["entries"]:
            rows.append(
                {
                    "cells": [
                        e["date"],
                        e["reference"],
                        e["description"],
                        _money(e["debit"]) if e["debit"] else "",
                        _money(e["credit"]) if e["credit"] else "",
                        _money(e["running_balance"]),
                    ]
                }
            )
        rows.append(
            {
                "cells": [
                    "",
                    "",
                    "Period total",
                    _money(a["total_debit"]),
                    _money(a["total_credit"]),
                    _money(a["closing_balance"]),
                ],
                "style": "subtotal",
            }
        )
    title = "General Ledger"
    if data.get("class_name"):
        title = f"General Ledger — {data['class_name']}"
    return {
        "title": title,
        "period": f"{data['start_date']} — {data['end_date']}",
        "columns": ["Date", "Reference", "Description", "Debit", "Credit", "Balance"],
        "rows": rows,
    }


def _company_name(db) -> str:
    from app.services.settings_service import get_all_settings

    return get_all_settings(db).get("company_name") or ""


def _csv_download(text: str, filename: str, request: Request):
    """The same Content-Disposition rule as the CSV page: inline for the
    desktop shell (which saves it itself), attachment for a browser."""
    from app.routes.csv import _csv_response

    return _csv_response(text, filename, request)


def _pdf_response(sections, db, filename: str, landscape: bool = False):
    from fastapi.responses import Response
    from app.services.pdf_service import generate_report_pdf
    from app.services.settings_service import get_all_settings

    pdf_bytes = generate_report_pdf(sections, get_all_settings(db), landscape=landscape)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


def _pl_filename(data: dict, t, ext: str) -> str:
    """profit-loss_2026-07-01_2026-07-31.pdf, with the class's slug after
    the report's when the P&L is one class's (#232)."""
    name = t.slug("Profit & Loss")
    if data.get("class_name"):
        name += "_" + (t.slug(data["class_name"]) or "class")
    return f"{name}_{data['start_date']}_{data['end_date']}.{ext}"


@router.get("/profit-loss/pdf")
def profit_loss_pdf(
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    class_id: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
    sort: str = _SORT_QUERY,
):
    # class_id is a keyword: profit_loss takes db before it
    data = profit_loss(start_date, end_date, db, class_id=class_id, sort=sort)
    t = terms_from_db(db)
    return _pdf_response([_pl_section(data, t)], db, _pl_filename(data, t, "pdf"))


@router.get("/profit-loss-by-class/pdf")
def profit_loss_by_class_pdf(
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    db: Session = Depends(get_db),
    class_ids: Optional[list[str]] = Query(default=None),
    show: str = "all",
    include_empty: bool = False,
):
    """The by-class grid, landscape; past eight classes it goes over
    several pages, each with the Account and Total columns (#232). The
    column choice (#233) rides along: class_ids, show, include_empty."""
    data = profit_loss_by_class(
        start_date,
        end_date,
        db,
        class_ids=class_ids,
        show=show,
        include_empty=include_empty,
    )
    t = terms_from_db(db)
    columns = [dict(c, name=c["class_name"]) for c in data["classes"]]
    return _pdf_response(
        _grid_sections(data, t("P&L by Class"), columns, t),
        db,
        f"{t.slug('P&L by Class')}_{data['start_date']}_{data['end_date']}.pdf",
        landscape=True,
    )


@router.get("/profit-loss-by-class/csv")
def profit_loss_by_class_csv_route(
    request: Request,
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    layout: str = Query(
        default="wide",
        description="wide: the grid, a column per class; long: one row per account and class, for a pivot table",
    ),
    db: Session = Depends(get_db),
    class_ids: Optional[list[str]] = Query(default=None),
    show: str = "all",
    include_empty: bool = False,
):
    from app.services.ledger_exports import profit_loss_by_class_csv

    data = profit_loss_by_class(
        start_date,
        end_date,
        db,
        class_ids=class_ids,
        show=show,
        include_empty=include_empty,
    )
    t = terms_from_db(db)
    columns = [dict(c, name=c["class_name"]) for c in data["classes"]]
    return _csv_download(
        profit_loss_by_class_csv(data, columns, _company_name(db), t, layout=layout),
        f"{t.slug('P&L by Class')}_{data['start_date']}_{data['end_date']}.csv",
        request,
    )


@router.get("/trial-balance/pdf")
def trial_balance_pdf(
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    db: Session = Depends(get_db),
):
    data = trial_balance(start_date, end_date, db)
    return _pdf_response(
        [_tb_section(data)],
        db,
        f"trial-balance_{data['start_date']}_{data['end_date']}.pdf",
    )


@router.get("/trial-balance/csv")
def trial_balance_csv_route(
    request: Request,
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    db: Session = Depends(get_db),
):
    from app.services.ledger_exports import trial_balance_csv

    data = trial_balance(start_date, end_date, db)
    return _csv_download(
        trial_balance_csv(data, _company_name(db)),
        f"trial-balance_{data['start_date']}_{data['end_date']}.csv",
        request,
    )


@router.get("/general-ledger/pdf")
def general_ledger_pdf(
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    account_id: int = Query(default=None),
    class_id: int = Query(default=None),
    db: Session = Depends(get_db),
):
    data = general_ledger(start_date, end_date, account_id, db, class_id=class_id)
    return _pdf_response(
        [_gl_section(data)],
        db,
        f"general-ledger_{data['start_date']}_{data['end_date']}.pdf",
    )


@router.get("/general-ledger/csv")
def general_ledger_csv_route(
    request: Request,
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    account_id: int = Query(default=None),
    class_id: int = Query(default=None),
    db: Session = Depends(get_db),
):
    from app.services.ledger_exports import general_ledger_csv

    data = general_ledger(start_date, end_date, account_id, db, class_id=class_id)
    return _csv_download(
        general_ledger_csv(data, _company_name(db), terms_from_db(db)),
        f"general-ledger_{data['start_date']}_{data['end_date']}.csv",
        request,
    )


@router.get("/profit-loss/csv")
def profit_loss_csv_route(
    request: Request,
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    class_id: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
    sort: str = _SORT_QUERY,
):
    from app.services.ledger_exports import profit_loss_csv

    data = profit_loss(start_date, end_date, db, class_id=class_id, sort=sort)
    t = terms_from_db(db)
    return _csv_download(
        profit_loss_csv(data, _company_name(db), t),
        _pl_filename(data, t, "csv"),
        request,
    )


@router.get("/balance-sheet/csv")
def balance_sheet_csv_route(
    request: Request,
    as_of_date: date = Query(default=None),
    db: Session = Depends(get_db),
    sort: str = _SORT_QUERY,
):
    from app.services.ledger_exports import balance_sheet_csv

    data = balance_sheet(as_of_date, db, sort=sort)
    t = terms_from_db(db)
    return _csv_download(
        balance_sheet_csv(data, _company_name(db), t),
        f"{t.slug('Balance Sheet')}_{data['as_of_date']}.csv",
        request,
    )


@router.get("/balance-sheet/pdf")
def balance_sheet_pdf(
    as_of_date: date = Query(default=None),
    db: Session = Depends(get_db),
    sort: str = _SORT_QUERY,
):
    data = balance_sheet(as_of_date, db, sort=sort)
    t = terms_from_db(db)
    return _pdf_response(
        [_bs_section(data, t)],
        db,
        f"{t.slug('Balance Sheet')}_{data['as_of_date']}.pdf",
    )


@router.get("/financial-statements/pdf")
def financial_statements_pdf(
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    db: Session = Depends(get_db),
):
    """The statements pack: P&L, Balance Sheet, and Trial Balance in one
    audit-ready document (each statement on its own page)."""
    pl = profit_loss(start_date, end_date, db)
    bs = balance_sheet(date.fromisoformat(pl["end_date"]), db)
    tb = trial_balance(
        date.fromisoformat(pl["start_date"]), date.fromisoformat(pl["end_date"]), db
    )
    t = terms_from_db(db)
    if t.is_nonprofit:
        from app.routes.reports.nonprofit import nonprofit_statement_sections

        sections = nonprofit_statement_sections(
            db, date.fromisoformat(pl["start_date"]), date.fromisoformat(pl["end_date"])
        ) + [_tb_section(tb)]
    else:
        sections = [_pl_section(pl, t), _bs_section(bs, t), _tb_section(tb)]
    return _pdf_response(
        sections, db, f"financial-statements_{pl['start_date']}_{pl['end_date']}.pdf"
    )
