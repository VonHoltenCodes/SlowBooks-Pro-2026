from datetime import date
from decimal import Decimal
from typing import Annotated, Optional

from fastapi import Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session
from sqlalchemy import func as sqlfunc

from app.database import get_db
from app.models.accounts import Account, AccountType, DetailType
from app.models.transactions import Transaction, TransactionLine
from app.routes.reports._router import router
from app.services.fiscal_year import fiscal_year_start
from app.services.statement_layout import (
    BS_GROUP_TOTALS,
    BS_GROUPS,
    MAX_DEPTH,
    account_chart,
    bs_group_totals,
    bs_groups,
    bs_print_details,
    mark_bs_lines,
    family_rows,
)
from app.services.terminology import Terms, terms_from_db

# Debit-normal account types. For these, natural balance = debit - credit.
# For the rest (liability, equity, income), natural balance = credit - debit.
from app.services.bank_register import DEBIT_NORMAL as _DEBIT_NORMAL  # noqa: E402
from app.services.entry_text import header_text


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
    """Every account's (number, name, parent, type, detail type), by id: one
    query a statement builds once and hands to every helper that needs the
    chart (#390 review: the compare P&L read it some sixteen times)."""
    return {
        a.id: (
            a.account_number or "",
            a.name or "",
            a.parent_id,
            a.account_type,
            a.detail_type,
        )
        for a in db.query(
            Account.id,
            Account.account_number,
            Account.name,
            Account.parent_id,
            Account.account_type,
            Account.detail_type,
        )
    }


def _chain(chart: dict, account_id) -> list:
    """The account's ids from the root down, the account itself last; stops
    at a parent loop or MAX_DEPTH, the sub-account walk's limit (#416)."""
    path, seen = [], set()
    while account_id in chart and account_id not in seen and len(path) < MAX_DEPTH:
        seen.add(account_id)
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
        number, name = chart[account_id][:2]
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


_EXCLUDE_ADJUSTING = Query(
    default=False,
    description="Leave the adjusting entries out (#341): the figures before "
    "the accountant's corrections",
)


def _flag(value) -> bool:
    """A query flag as a bool: other routes call these report functions
    directly, and then the parameter holds its Query() default, not False
    (the statements pack read every statement without its adjusting
    entries until this was caught by the package test, #343)."""
    return value is True


def _totals_by_account(
    db,
    acct_type,
    date_start=None,
    date_end=None,
    in_class=None,
    sort="number",
    exclude_adjusting=False,
    chart: dict | None = None,
):
    """Return a list of {account_id, account_name, account_number,
    detail_type, amount} rows where amount is signed by the account type's
    natural balance (always positive for a normal-balance ledger), in the
    chart's order (`sort`: number or name, see _account_order).

    `account_id` is included so the SPA can drill into /account-transactions
    for any line — Phase 11 drill-down support. `exclude_adjusting` leaves
    the adjusting entries (#341) out, for the figures before them.
    """
    q = (
        db.query(
            Account.id,
            Account.name,
            Account.account_number,
            Account.detail_type,
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
    if exclude_adjusting:
        q = q.filter(Transaction.is_adjusting == False)  # noqa: E712
    q = q.group_by(
        Account.id, Account.name, Account.account_number, Account.detail_type
    )

    rows = []
    chart = chart if chart is not None else _chart_index(db)
    for acct_id, name, number, detail, dr, cr in q.all():
        amount = (dr - cr) if acct_type in _DEBIT_NORMAL else (cr - dr)
        path, label = _account_words(chart, acct_id)
        rows.append(
            {
                "account_id": acct_id,
                "account_name": name,
                "account_number": number,
                "account_path": path or name,
                "account_label": label or name,
                "detail_type": detail,
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
    exclude_adjusting: bool = _EXCLUDE_ADJUSTING,
    compare: Annotated[
        Optional[str],
        Query(description="prior_year adds the same period one year earlier"),
    ] = None,
):
    _check_compare(compare)
    exclude_adjusting = _flag(exclude_adjusting)
    sort = sort if isinstance(sort, str) else "number"
    if not start_date:
        start_date = fiscal_year_start(db, date.today())
    if not end_date:
        end_date = date.today()
    cls, in_class = _in_class(db, class_id) if class_id is not None else (None, None)
    chart = _chart_index(db)

    def period(start, end):
        return _pl_period(db, start, end, cls, in_class, sort, exclude_adjusting, chart)

    if compare == "prior_year":
        current = period(start_date, end_date)
        prior = period(_year_earlier(start_date), _year_earlier(end_date))
        return _pl_layout(db, _pl_with_prior(db, current, prior, sort, chart), chart)
    return _pl_layout(db, period(start_date, end_date), chart)


def _pl_period(db, start_date, end_date, cls, in_class, sort, exclude_adjusting, chart):
    """One period's P&L figures, before the layout."""
    income, other_income = _split_other(
        _totals_by_account(
            db,
            AccountType.INCOME,
            start_date,
            end_date,
            in_class,
            sort,
            exclude_adjusting,
            chart,
        ),
        DetailType.OTHER_INCOME,
    )
    cogs = _totals_by_account(
        db,
        AccountType.COGS,
        start_date,
        end_date,
        in_class,
        sort,
        exclude_adjusting,
        chart,
    )
    expenses, other_expense = _split_other(
        _totals_by_account(
            db,
            AccountType.EXPENSE,
            start_date,
            end_date,
            in_class,
            sort,
            exclude_adjusting,
            chart,
        ),
        DetailType.OTHER_EXPENSE,
    )

    # The rows are floats: totals rounded to the cent, or 777.40 of sales
    # less cost answers 777.4000000000001 and stops matching the by-class
    # grid, which sums in Decimal (#413). The Other sections (#390) the same.
    total_operating_income = round(sum(i["amount"] for i in income), 2)
    total_cogs = round(sum(c["amount"] for c in cogs), 2)
    total_operating_expenses = round(sum(e["amount"] for e in expenses), 2)
    total_other_income = round(sum(i["amount"] for i in other_income), 2)
    total_other_expense = round(sum(e["amount"] for e in other_expense), 2)
    net_operating_income = round(
        total_operating_income - total_cogs - total_operating_expenses, 2
    )
    net_other_income = round(total_other_income - total_other_expense, 2)

    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "class_id": cls.id if cls else None,
        "class_name": cls.name if cls else None,
        "sort": sort,
        "exclude_adjusting": bool(exclude_adjusting),
        "income": income,
        "cogs": cogs,
        "expenses": expenses,
        "other_income": other_income,
        "other_expense": other_expense,
        # total_income / total_expenses keep their 2.27.0 meaning for API
        # clients: every income / expense account, Other included. The
        # statement prints the operating sections' own totals (#390).
        "total_income": round(total_operating_income + total_other_income, 2),
        "total_operating_income": total_operating_income,
        "total_cogs": total_cogs,
        # gross_profit keeps its 2.27.0 meaning too (all income less cost of
        # goods); the statement prints the operating one (#390)
        "gross_profit": round(
            total_operating_income + total_other_income - total_cogs, 2
        ),
        "operating_gross_profit": round(total_operating_income - total_cogs, 2),
        "total_expenses": round(total_operating_expenses + total_other_expense, 2),
        "total_operating_expenses": total_operating_expenses,
        "net_operating_income": net_operating_income,
        "total_other_income": total_other_income,
        "total_other_expense": total_other_expense,
        "net_other_income": net_other_income,
        # the same figure as before the split: a move between sections,
        # never in or out of the total (#390)
        "net_income": round(net_operating_income + net_other_income, 2),
    }


def _split_other(lines: list, other) -> tuple[list, list]:
    """(the section's lines, its Other Income / Other Expense lines), by
    the account's detail type (#390)."""
    return (
        [i for i in lines if i.get("detail_type") != other.value],
        [i for i in lines if i.get("detail_type") == other.value],
    )


def _has_other(lines) -> bool:
    """An Other Income / Other Expense line with a figure: an account that
    nets to nothing (this year and last) does not open the block, as the
    grids leave out an account with nothing in any column (#390)."""
    return any(
        abs(i.get("amount") or 0) >= 0.005 or abs(i.get("prior_amount") or 0) >= 0.005
        for i in lines
    )


def _pl_layout(db, data: dict, chart: dict) -> dict:
    """How the P&L prints, for every writer to read:

    - `has_other`: Other Income / Other Expense below Net Operating Income,
      and Net Operating Income itself, only when either has a line (#390,
      the owner's answer 4); without, it is the P&L it was in 2.26.
    - `tree`: each section's lines with sub-accounts beneath their parents,
      a family total under each parent (#416), for the screen and the PDF.
      The flat lists stay, with full paths, for the CSV and the prior-year
      pairing. A parent of another section does not gather a line."""
    # each section opens on its own figures, not whether the list has a row:
    # a section with only a $0 account does not print a heading (#390)
    data["has_other_income"] = _has_other(data.get("other_income", []))
    data["has_other_expense"] = _has_other(data.get("other_expense", []))
    data["has_other"] = data["has_other_income"] or data["has_other_expense"]
    section_of = {aid: _pl_section_of(row[3], row[4]) for aid, row in chart.items()}
    order = _account_order(db, data.get("sort") or "number", chart)
    accounts = account_chart(index=chart)
    keys = ("amount", "prior_amount") if data.get("prior") else ("amount",)
    data["tree"] = {
        key: family_rows(
            data.get(key, []),
            accounts,
            lambda aid, key=key: section_of.get(aid) == key,
            keys,
            lambda aid: order({"account_id": aid}),
        )
        for key in _PL_SECTIONS
    }
    return data


def _year_earlier(d: date) -> date:
    """The same day one year earlier. A month's last day maps to that
    month's last day a year earlier (Feb 28 2025 -> Feb 29 2024, Feb 29
    2024 -> Feb 28 2023), so a full month or quarter compares to the full
    month or quarter."""
    import calendar

    year = d.year - 1
    last_then = calendar.monthrange(year, d.month)[1]
    if d.day == calendar.monthrange(d.year, d.month)[1]:
        return date(year, d.month, last_then)
    return date(year, d.month, min(d.day, last_then))


_PL_TOTALS = (
    "total_income",
    "total_cogs",
    "gross_profit",
    "operating_gross_profit",
    "total_expenses",
    "net_income",
    "net_operating_income",
    "total_other_income",
    "total_other_expense",
    "net_other_income",
    "total_operating_income",
    "total_operating_expenses",
)
# the P&L's account sections, in print order
_PL_SECTIONS = ("income", "cogs", "expenses", "other_income", "other_expense")
# the group subtotals (#390) ride along so the prior year has them too
_BS_TOTALS = ("total_assets", "total_liabilities", "total_equity") + BS_GROUP_TOTALS


def _line_key(item: dict):
    """What lines up an account's two years: its id, or for a computed line
    with no account (the Balance Sheet's net income line) its name."""
    return item.get("account_id") or ("name", item.get("account_name"))


def _with_prior(
    db,
    current: dict,
    prior: dict,
    sections: tuple,
    totals: tuple,
    dates: tuple,
    sort: str = "number",
    chart: dict | None = None,
) -> dict:
    """The current report with the prior year's figures beside it: every
    account line gets `prior_amount` (an account with activity in only one
    of the two years shows 0 in the other), the lines keep the chart's
    order (`sort`, see _account_order) with a computed line last, and
    `prior` carries the prior year's dates and totals."""
    order = _account_order(db, sort, chart)
    out = dict(current)
    for key in sections:
        prior_by = {_line_key(i): i for i in prior[key]}
        lines = [
            dict(i, prior_amount=prior_by.pop(_line_key(i), {}).get("amount", 0.0))
            for i in current[key]
        ]
        lines += [
            dict(p, amount=0.0, prior_amount=p["amount"]) for p in prior_by.values()
        ]
        lines.sort(key=lambda i: (i.get("account_id") is None, order(i)))
        out[key] = lines
    out["compare"] = "prior_year"
    out["prior"] = {
        **{k: prior[k] for k in dates},
        **{k: prior[k] for k in totals},
    }
    return out


def _pl_with_prior(
    db, current: dict, prior: dict, sort: str = "number", chart=None
) -> dict:
    return _with_prior(
        db,
        current,
        prior,
        _PL_SECTIONS,
        _PL_TOTALS,
        ("start_date", "end_date"),
        sort,
        chart,
    )


def _bs_with_prior(
    db, current: dict, prior: dict, sort: str = "number", chart=None
) -> dict:
    return _with_prior(
        db,
        current,
        prior,
        ("assets", "liabilities", "equity"),
        _BS_TOTALS,
        ("as_of_date",),
        sort,
        chart,
    )


def _check_compare(compare):
    if compare not in (None, "", "prior_year"):
        raise HTTPException(
            status_code=422, detail="compare must be prior_year or left out"
        )


def _compare_figures(cur, before, prior, show_changes: bool) -> list:
    """A line's money cells for a report PDF: Amount alone, or Current |
    Prior Year, plus $ Change | % Change unless they are turned off."""
    if not prior:
        return [_money(cur)]
    cells = [_money(cur), _money(before)]
    if show_changes:
        pct = pct_change(cur, before)
        cells += [
            _money(float(cur or 0) - float(before or 0)),
            "—" if pct is None else f"{pct:,.1f}%",
        ]
    return cells


def _compare_columns(prior, show_changes: bool) -> list:
    if not prior:
        return ["", "Amount"]
    return ["", "Current", "Prior Year"] + (
        ["$ Change", "% Change"] if show_changes else []
    )


def pct_change(current, prior) -> Optional[float]:
    """Change from prior to current as a percent of the prior figure's
    size; None when the prior figure is zero (no meaningful percent)."""
    current, prior = float(current or 0), float(prior or 0)
    if abs(prior) < 0.005:
        return None
    return round((current - prior) / abs(prior) * 100, 1)


@router.get("/balance-sheet")
def balance_sheet(
    as_of_date: date = Query(default=None),
    db: Session = Depends(get_db),
    sort: str = _SORT_QUERY,
    compare: Annotated[
        Optional[str],
        Query(description="prior_year adds the same date one year earlier"),
    ] = None,
):
    _check_compare(compare)
    sort = sort if isinstance(sort, str) else "number"
    t = terms_from_db(db)
    if not as_of_date:
        as_of_date = date.today()
    chart = _chart_index(db)
    if compare == "prior_year":
        return _bs_layout(
            _bs_with_prior(
                db,
                _bs_period(db, as_of_date, sort, t, chart),
                _bs_period(db, _year_earlier(as_of_date), sort, t, chart),
                sort,
                chart,
            )
        )
    return _bs_layout(_bs_period(db, as_of_date, sort, t, chart))


def _bs_period(db, as_of_date, sort, t, chart) -> dict:
    """One date's Balance Sheet figures, before the layout."""

    def totals(acct_type, order="number"):
        return _totals_by_account(
            db, acct_type, date_end=as_of_date, sort=order, chart=chart
        )

    # a default-typed sub-account prints in its parent's group (2.28.0):
    # display only, its detail type and figures stay its own
    placed = bs_print_details(index=chart)
    assets = mark_bs_lines(totals(AccountType.ASSET, sort), placed)
    liabilities = mark_bs_lines(totals(AccountType.LIABILITY, sort), placed)
    equity = totals(AccountType.EQUITY, sort)

    # Round the totals to the cent as the P&L does (profit_loss above), so a
    # sum of floats never shows 777.4000000000001 beside the P&L's 777.40.
    total_assets = round(sum(a["amount"] for a in assets), 2)
    total_liabilities = round(sum(liab["amount"] for liab in liabilities), 2)
    total_equity = round(sum(e["amount"] for e in equity), 2)

    # Net income for all periods up to as_of_date flows into equity as retained
    # earnings. Without this, the balance sheet fails to balance whenever there
    # is income or expense activity that hasn't been formally closed into an
    # equity account (which is the normal state in this app — income/expense
    # accounts are never explicitly closed).
    income_rows = totals(AccountType.INCOME)
    cogs_rows = totals(AccountType.COGS)
    expense_rows = totals(AccountType.EXPENSE)
    # Rounded to the cent the same way profit_loss rounds its net income
    # (each total to the cent, then subtract and round), so the two reports
    # agree and no float noise leaks into equity (#413).
    net_income = round(
        round(sum(r["amount"] for r in income_rows), 2)
        - round(sum(r["amount"] for r in cogs_rows), 2)
        - round(sum(r["amount"] for r in expense_rows), 2),
        2,
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
        total_equity = round(total_equity + net_income, 2)

    return {
        "as_of_date": as_of_date.isoformat(),
        "sort": sort,
        "assets": assets,
        "liabilities": liabilities,
        "equity": equity,
        "total_assets": total_assets,
        "total_liabilities": total_liabilities,
        "total_equity": total_equity,
        **bs_group_totals(assets, "assets"),
        **bs_group_totals(liabilities, "liabilities"),
    }


def _bs_layout(data: dict) -> dict:
    """The Balance Sheet's groups (#390) beside its flat lists: for assets
    and liabilities, [{heading, total_key, lines}] in print order, so the
    screen, the PDF and the CSV classify nothing. The flat lists stay what
    the prior-year pairing and the drill-down read."""
    data["groups"] = {
        side: [
            {"heading": heading, "total_key": key, "lines": lines}
            for heading, key, lines in bs_groups(data[side], side)
        ]
        for side in BS_GROUPS
    }
    return data


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
        start_date = fiscal_year_start(db, date.today())
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
                "description": header_text(txn, tl.description),
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
        start_date = fiscal_year_start(db, date.today())
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
    exclude_adjusting: bool = _EXCLUDE_ADJUSTING,
):
    """Trial Balance: sum all debits/credits per account for a date range."""
    exclude_adjusting = _flag(exclude_adjusting)
    if not start_date:
        start_date = fiscal_year_start(db, date.today())
    if not end_date:
        end_date = date.today()

    q = (
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
    )
    if exclude_adjusting:
        q = q.filter(Transaction.is_adjusting == False)  # noqa: E712
    results = q.group_by(
        Account.id, Account.account_number, Account.name, Account.account_type
    ).all()

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
        "exclude_adjusting": bool(exclude_adjusting),
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
        start_date = fiscal_year_start(db, date.today())
    if not end_date:
        end_date = date.today()
    return statement_of_cash_flows(
        db, start_date, end_date, terms_from_db(db)("Net Income")
    )


_PL_TYPES = (AccountType.INCOME, AccountType.COGS, AccountType.EXPENSE)
# the grids' sections (#390): the P&L's, so the three reports tie line for
# line (the owner's answer 3)
_GRID_SECTIONS = ("income", "cogs", "expenses", "other_income", "other_expense")


def _pl_section_of(acct_type, detail) -> str:
    """The P&L section an account prints in: by base type, with Other
    Income and Other Expense apart by detail type (#390)."""
    if acct_type == AccountType.INCOME:
        return "other_income" if detail == DetailType.OTHER_INCOME.value else "income"
    if acct_type == AccountType.COGS:
        return "cogs"
    return "other_expense" if detail == DetailType.OTHER_EXPENSE.value else "expenses"


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
    chart = _chart_index(db)
    rows = (
        db.query(
            key_expr.label("k"),
            Account.id,
            Account.account_number,
            Account.name,
            Account.account_type,
            Account.detail_type,
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
            Account.detail_type,
        )
        .all()
    )

    by_key: dict = {}
    # each account's amount in each column: the rows of the report (#213)
    by_account: dict[int, dict] = {}
    for key, acct_id, number, name, acct_type, detail, dr, cr in rows:
        section = _pl_section_of(acct_type, detail)
        acct = by_account.setdefault(
            acct_id,
            {
                "id": acct_id,
                "number": number,
                "name": name,
                "section": section,
                "by": {},
            },
        )
        dr, cr = Decimal(str(dr)), Decimal(str(cr))
        natural = (cr - dr) if acct_type == AccountType.INCOME else (dr - cr)
        acct["by"][key] = acct["by"].get(key, Decimal("0")) + natural
        bucket = by_key.setdefault(key, {f: Decimal("0") for f in _GRID_SECTIONS})
        bucket[section] += natural

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
        t = {f: Decimal("0") for f in _GRID_SECTIONS}
        for k in keys:
            b = by_key.get(k)
            if b:
                for f in t:
                    t[f] += b[f]
        operating = t["income"] - t["cogs"] - t["expenses"]
        other = t["other_income"] - t["other_expense"]
        # income / expenses as in 2.27.0 (Other included); the operating
        # sections' totals beside them, for the rows the grid prints (#390)
        return {
            "income": float(t["income"] + t["other_income"]),
            "operating_income": float(t["income"]),
            "cogs": float(t["cogs"]),
            "gross_profit": float(t["income"] + t["other_income"] - t["cogs"]),
            "operating_gross_profit": float(t["income"] - t["cogs"]),
            "expenses": float(t["expenses"] + t["other_expense"]),
            "operating_expenses": float(t["expenses"]),
            "net_operating_income": float(operating),
            "other_income": float(t["other_income"]),
            "other_expense": float(t["other_expense"]),
            "net_other_income": float(other),
            "net_income": float(operating + other),
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
    # a sub-account right after its parent, and full paths where a leaf name
    # repeats, as the P&L flat list does (#416)
    row_order = _account_order(db, "number", chart)
    accounts: dict[str, list] = {f: [] for f in _GRID_SECTIONS}
    for a in sorted(
        by_account.values(), key=lambda a: row_order({"account_id": a["id"]})
    ):
        amounts = [a["by"].get(k, Decimal("0")) for k in order]
        if not any(amounts):
            continue
        path, label = _account_words(chart, a["id"])
        accounts[a["section"]].append(
            {
                "account_id": a["id"],
                "account_number": a["number"],
                "account_name": a["name"],
                "account_path": path or a["name"],
                "account_label": label or a["name"],
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
        "total_operating_income": shown["operating_income"],
        "total_cogs": shown["cogs"],
        "total_gross_profit": shown["gross_profit"],
        "total_operating_gross_profit": shown["operating_gross_profit"],
        "total_expenses": shown["expenses"],
        "total_operating_expenses": shown["operating_expenses"],
        "total_net_operating_income": shown["net_operating_income"],
        "total_other_income": shown["other_income"],
        "total_other_expense": shown["other_expense"],
        "total_net_other_income": shown["net_other_income"],
        "total_net_income": shown["net_income"],
        # the Other Income / Other Expense rows print, as on the P&L, only
        # when an account in them has a figure in the columns shown (#390)
        # an account with nothing in every column is not in `accounts`
        "has_other": bool(accounts["other_income"] or accounts["other_expense"]),
        "filtered": filtered,
        "columns_total": len(with_activity),
        "chosen_empty": chosen_empty,
        "unfiltered": totals_of(with_activity),
    }


def _period(db, start_date, end_date):
    if not start_date:
        start_date = fiscal_year_start(db, date.today())
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

    start_date, end_date = _period(db, start_date, end_date)
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
            "operating_income": c["operating_income"],
            "cogs": c["cogs"],
            "gross_profit": c["gross_profit"],
            "operating_gross_profit": c["operating_gross_profit"],
            "expenses": c["expenses"],
            "operating_expenses": c["operating_expenses"],
            "net_operating_income": c["net_operating_income"],
            "other_income": c["other_income"],
            "other_expense": c["other_expense"],
            "net_other_income": c["net_other_income"],
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

    start_date, end_date = _period(db, start_date, end_date)
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
            "operating_income": c["operating_income"],
            "cogs": c["cogs"],
            "gross_profit": c["gross_profit"],
            "operating_gross_profit": c["operating_gross_profit"],
            "expenses": c["expenses"],
            "operating_expenses": c["operating_expenses"],
            "net_operating_income": c["net_operating_income"],
            "other_income": c["other_income"],
            "other_expense": c["other_expense"],
            "net_other_income": c["net_other_income"],
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
        start_date = fiscal_year_start(db, date.today())
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


def _period_words(data: dict) -> str:
    """The section's period line; a statement run without the adjusting
    entries (#341) says so, so the page is not mistaken for the books as
    they stand."""
    period = f"{data['start_date']} — {data['end_date']}"
    if data.get("exclude_adjusting"):
        period += " (adjusting entries left out)"
    return period


def _row_words(item: dict) -> str:
    """A statement row's words on the PDF: its number and path (W-8)."""
    return item.get("account_label") or item["account_name"]


def _pl_section(data: dict, t=None, show_changes: bool = True) -> dict:
    """P&L rows for the PDF; t (Terms) picks the company's words —
    "Statement of Activities" / "Revenue & Support" for a nonprofit. One
    class's P&L (class_id on the data, #232) is titled with the class and
    says it is that class only. With a prior-year comparison (`prior` on
    the data) the columns are Current | Prior Year, and $ Change | % Change
    unless show_changes is off."""
    t = t or Terms()
    prior = data.get("prior")
    columns = _compare_columns(prior, show_changes)
    blank = [""] * (len(columns) - 1)

    def line(item):
        return {
            "cells": [f"  {_row_words(item)}"]
            + _compare_figures(
                item["amount"], item.get("prior_amount", 0), prior, show_changes
            )
        }

    def tree_row(r):
        """A printed row of the section's tree (#416): an account, a
        parent's heading, its direct postings or its family's total."""
        indent = r["depth"] + 1
        if r["kind"] == "heading":
            return {
                "cells": [f"  {r['label']}"] + blank,
                "style": "group",
                "indent": indent,
            }
        figures = _compare_figures(
            r["amount"], r.get("prior_amount", 0), prior, show_changes
        )
        row = {"cells": [f"  {r['label']}"] + figures, "indent": indent}
        if r["kind"] == "total":
            row["style"] = "subtotal"
        return row

    tree = data.get("tree") or {}

    def total(label, key, style="subtotal"):
        return {
            "cells": [label]
            + _compare_figures(
                data[key], (prior or {}).get(key, 0), prior, show_changes
            ),
            "style": style,
        }

    def section(label, key, total_key):
        rows.append({"cells": [label] + blank, "style": "subtotal"})
        if key in tree:
            rows.extend(tree_row(r) for r in tree[key])
        else:
            rows.extend(line(item) for item in data.get(key, []))
        rows.append(total(f"Total {label}", total_key))

    rows = []
    section(t("Income"), "income", "total_operating_income")
    section("Cost of Goods Sold", "cogs", "total_cogs")
    rows.append(total("Gross Profit", "operating_gross_profit"))
    section("Expenses", "expenses", "total_operating_expenses")
    if data.get("has_other"):
        # below Net Operating Income, only when there is something (#390)
        rows.append(total(t("Net Operating Income"), "net_operating_income"))
        if data.get("has_other_income"):
            section(t("Other Income"), "other_income", "total_other_income")
        if data.get("has_other_expense"):
            section("Other Expense", "other_expense", "total_other_expense")
        rows.append(total(t("Net Other Income"), "net_other_income"))
    rows.append(total(t("Net Income"), "net_income", "grand-total"))
    cls = data.get("class_name")
    period = _period_words(data)
    if prior:
        period += f" · compared with {prior['start_date']} — {prior['end_date']}"
    return {
        "title": t("Profit & Loss") + (f" — {cls}" if cls else ""),
        "period": period
        + (f" · {t('Class')}: {cls} only, not the company total" if cls else ""),
        "columns": columns,
        "rows": rows,
    }


# A page holds this many class or job columns beside Account and Total;
# past it the grid goes over several pages, each with the Account column
# and the same Total column (the total across every column shown).
GRID_COLUMNS_PER_PAGE = 8


def _grid_layout(data: dict, t) -> list[tuple]:
    """The P&L by Class / by Job grid's rows above Net Income, in print
    order, for the screen's twin in the PDF and the CSV: (section heading,
    accounts key or None for a summary row, subtotal label, column key,
    total key). Other Income / Other Expense below Net Operating Income
    when the grid has either (#390), as the P&L prints them."""
    out = [
        (
            t("Income"),
            "income",
            t("Total Income"),
            "operating_income",
            "total_operating_income",
        ),
        (
            "Cost of Goods Sold",
            "cogs",
            "Gross Profit",
            "operating_gross_profit",
            "total_operating_gross_profit",
        ),
        (
            "Expenses",
            "expenses",
            "Total Expenses",
            "operating_expenses",
            "total_operating_expenses",
        ),
    ]
    if data.get("has_other"):
        out.append(
            (
                None,
                None,
                t("Net Operating Income"),
                "net_operating_income",
                "total_net_operating_income",
            )
        )
        accounts = data.get("accounts") or {}
        if accounts.get("other_income"):
            out.append(
                (
                    t("Other Income"),
                    "other_income",
                    t("Total Other Income"),
                    "other_income",
                    "total_other_income",
                )
            )
        if accounts.get("other_expense"):
            out.append(
                (
                    "Other Expense",
                    "other_expense",
                    "Total Other Expense",
                    "other_expense",
                    "total_other_expense",
                )
            )
        out.append(
            (
                None,
                None,
                t("Net Other Income"),
                "net_other_income",
                "total_net_other_income",
            )
        )
    return out


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
            # the number and full path, as the P&L PDF prints a flat line (#416)
            return row(
                f"  {_row_words(a)}",
                [_money(a["amounts"][i]) if a["amounts"][i] else "" for i in idx]
                + [_money(a["total"])],
            )

        def sum_row(label, key, total, style):
            return row(label, [_money(c[key]) for c in cols] + [_money(total)], style)

        rows = []
        for label, key, sub_label, sub_key, total_key in _grid_layout(data, t):
            if key:
                rows.append(row(label, [""] * (len(cols) + 1), "subtotal"))
                rows.extend(account_row(a) for a in data["accounts"][key])
            rows.append(sum_row(sub_label, sub_key, data[total_key], "subtotal"))
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


def _bs_section(data: dict, t=None, show_changes: bool = True) -> dict:
    """Balance Sheet rows for the PDF. Assets and liabilities print under
    their groups (#390: Current / Fixed / Other Assets, Current / Long Term
    Liabilities), each with its subtotal. With a prior-year comparison
    (`prior` on the data) the columns are Current | Prior Year, and
    $ Change | % Change unless show_changes is off."""
    t = t or Terms()
    prior = data.get("prior")
    columns = _compare_columns(prior, show_changes)
    blank = [""] * (len(columns) - 1)
    p = prior or {}
    groups = data.get("groups") or {}

    def figures(cur, before):
        return _compare_figures(cur, before, prior, show_changes)

    def line(item, indent):
        return {
            "cells": [f"  {_row_words(item)}"]
            + figures(item["amount"], item.get("prior_amount", 0)),
            "indent": indent,
        }

    rows = []
    for label, key, total_key in (
        ("Assets", "assets", "total_assets"),
        ("Liabilities", "liabilities", "total_liabilities"),
        (t("Equity"), "equity", "total_equity"),
    ):
        rows.append({"cells": [label] + blank, "style": "subtotal"})
        if key in groups:
            for g in groups[key]:
                rows.append(
                    {"cells": [g["heading"]] + blank, "style": "group", "indent": 1}
                )
                rows.extend(line(item, 2) for item in g["lines"])
                rows.append(
                    {
                        "cells": [f"Total {g['heading']}"]
                        + figures(data[g["total_key"]], p.get(g["total_key"], 0)),
                        "style": "subtotal",
                        "indent": 1,
                    }
                )
        else:
            rows.extend(line(item, 1) for item in data[key])
        rows.append(
            {
                "cells": [f"Total {label}"]
                + figures(data[total_key], p.get(total_key, 0)),
                "style": "subtotal",
            }
        )
    rows.append(
        {
            "cells": [t("Liabilities + Equity")]
            + figures(
                data["total_liabilities"] + data["total_equity"],
                p.get("total_liabilities", 0) + p.get("total_equity", 0),
            ),
            "style": "grand-total",
        }
    )
    period = f"As of {data['as_of_date']}"
    if prior:
        period += f" · compared with {prior['as_of_date']}"
    return {
        "title": t("Balance Sheet"),
        "period": period,
        "columns": columns,
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
        "period": _period_words(data),
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
    exclude_adjusting: bool = _EXCLUDE_ADJUSTING,
    compare: Optional[str] = Query(default=None),
    hide_changes: bool = Query(default=False),
):
    # class_id is a keyword: profit_loss takes db before it
    data = profit_loss(
        start_date,
        end_date,
        db,
        class_id=class_id,
        sort=sort,
        exclude_adjusting=exclude_adjusting,
        compare=compare,
    )
    t = terms_from_db(db)
    return _pdf_response(
        [_pl_section(data, t, show_changes=not hide_changes)],
        db,
        _pl_filename(data, t, "pdf"),
    )


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
    exclude_adjusting: bool = _EXCLUDE_ADJUSTING,
):
    data = trial_balance(start_date, end_date, db, exclude_adjusting=exclude_adjusting)
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
    exclude_adjusting: bool = _EXCLUDE_ADJUSTING,
):
    from app.services.ledger_exports import trial_balance_csv

    data = trial_balance(start_date, end_date, db, exclude_adjusting=exclude_adjusting)
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


def _account_transactions_section(data: dict, t) -> dict:
    """The drill-down's rows for the PDF (#333)."""
    acct = data.get("account") or {}
    head = f"{acct.get('number') or ''} {acct.get('name') or ''}".strip()
    rows = [
        {
            "cells": [
                "",
                "",
                "Balance brought forward",
                "",
                "",
                _money(data.get("opening_balance", 0)),
            ],
            "style": "subtotal",
        }
    ]
    for e in data.get("entries", []):
        rows.append(
            {
                "cells": [
                    e.get("date", ""),
                    (e.get("payee") or "")[:40],
                    (
                        (e.get("description") or "")
                        + (" (void)" if e.get("voided") else "")
                    )[:60],
                    _money(e["debit"]) if e.get("debit") else "",
                    _money(e["credit"]) if e.get("credit") else "",
                    _money(e.get("running_balance", 0)),
                ]
            }
        )
    entries = data.get("entries", [])
    closing = (
        entries[-1]["running_balance"] if entries else data.get("opening_balance", 0)
    )
    rows.append(
        {
            "cells": [
                "",
                "",
                "Period total",
                _money(data.get("period_debit", 0)),
                _money(data.get("period_credit", 0)),
                _money(closing),
            ],
            "style": "subtotal",
        }
    )
    period = f"{data['start_date']} — {data['end_date']}"
    if data.get("class_name"):
        period += f" · {t('Class')}: {data['class_name']} only"
    if data.get("job_name"):
        period += f" · {t('Job')}: {data['job_name']}"
    return {
        "title": f"Account detail — {head}",
        "period": period,
        "columns": ["Date", "Payee", "Description", "Debit", "Credit", "Running"],
        "rows": rows,
    }


@router.get("/account-transactions/csv")
def account_transactions_csv_route(
    request: Request,
    account_id: int = Query(...),
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    class_id: Optional[int] = Query(default=None),
    job_id: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
):
    """The drill-down as a CSV, the figures on the screen (#333)."""
    from app.services.ledger_exports import account_transactions_csv

    data = account_transactions(
        account_id, start_date, end_date, db, class_id=class_id, job_id=job_id
    )
    t = terms_from_db(db)
    acct = data.get("account") or {}
    slug = t.slug(f"{acct.get('number') or ''} {acct.get('name') or 'account'}".strip())
    return _csv_download(
        account_transactions_csv(data, _company_name(db), t),
        f"AccountDetail_{slug}_{data['start_date']}_{data['end_date']}.csv",
        request,
    )


@router.get("/account-transactions/pdf")
def account_transactions_pdf_route(
    account_id: int = Query(...),
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    class_id: Optional[int] = Query(default=None),
    job_id: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
):
    """The drill-down as a PDF (#333)."""
    data = account_transactions(
        account_id, start_date, end_date, db, class_id=class_id, job_id=job_id
    )
    t = terms_from_db(db)
    acct = data.get("account") or {}
    slug = t.slug(f"{acct.get('number') or ''} {acct.get('name') or 'account'}".strip())
    return _pdf_response(
        [_account_transactions_section(data, t)],
        db,
        f"AccountDetail_{slug}_{data['start_date']}_{data['end_date']}.pdf",
    )


@router.get("/profit-loss/csv")
def profit_loss_csv_route(
    request: Request,
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    class_id: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
    sort: str = _SORT_QUERY,
    exclude_adjusting: bool = _EXCLUDE_ADJUSTING,
    compare: Optional[str] = Query(default=None),
    hide_changes: bool = Query(default=False),
):
    from app.services.ledger_exports import profit_loss_csv

    data = profit_loss(
        start_date,
        end_date,
        db,
        class_id=class_id,
        sort=sort,
        exclude_adjusting=exclude_adjusting,
        compare=compare,
    )
    t = terms_from_db(db)
    return _csv_download(
        profit_loss_csv(data, _company_name(db), t, show_changes=not hide_changes),
        _pl_filename(data, t, "csv"),
        request,
    )


@router.get("/balance-sheet/csv")
def balance_sheet_csv_route(
    request: Request,
    as_of_date: date = Query(default=None),
    db: Session = Depends(get_db),
    sort: str = _SORT_QUERY,
    compare: Optional[str] = Query(default=None),
    hide_changes: bool = Query(default=False),
):
    from app.services.ledger_exports import balance_sheet_csv

    data = balance_sheet(as_of_date, db, sort=sort, compare=compare)
    t = terms_from_db(db)
    return _csv_download(
        balance_sheet_csv(data, _company_name(db), t, show_changes=not hide_changes),
        f"{t.slug('Balance Sheet')}_{data['as_of_date']}.csv",
        request,
    )


@router.get("/balance-sheet/pdf")
def balance_sheet_pdf(
    as_of_date: date = Query(default=None),
    db: Session = Depends(get_db),
    sort: str = _SORT_QUERY,
    compare: Optional[str] = Query(default=None),
    hide_changes: bool = Query(default=False),
):
    data = balance_sheet(as_of_date, db, sort=sort, compare=compare)
    t = terms_from_db(db)
    return _pdf_response(
        [_bs_section(data, t, show_changes=not hide_changes)],
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


# ── Adjusting Journal Entries (#341) ─────────────────────────────────────
# The accountant's corrections for a period, each with its lines and who
# posted it; the totals tie to the General Ledger's adjusting lines. The
# report QuickBooks accountants ask for by this name.


def _range_or_year(db, start_date, end_date):
    if not start_date:
        start_date = fiscal_year_start(db, date.today())
    if not end_date:
        end_date = date.today()
    return start_date, end_date


@router.get("/adjusting-entries")
def adjusting_entries(
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    db: Session = Depends(get_db),
):
    """Every journal entry marked adjusting in the period (#341), oldest
    first: its lines, who posted it, whether it was voided (a voided one
    and its reversal both list, each marked), and the period's totals."""
    from app.routes.journal import _line_dict
    from app.services.qbo_documents import REVERSALS

    start_date, end_date = _range_or_year(db, start_date, end_date)
    txns = (
        db.query(Transaction)
        .filter(
            Transaction.is_adjusting == True,  # noqa: E712
            Transaction.date >= start_date,
            Transaction.date <= end_date,
        )
        .order_by(Transaction.date, Transaction.id)
        .all()
    )
    accounts = {a.id: a for a in db.query(Account).all()}
    voided = {
        source_id
        for (source_id,) in db.query(Transaction.source_id).filter(
            Transaction.source_type.in_(REVERSALS), Transaction.source_id.isnot(None)
        )
    }
    entries = []
    total_debit = total_credit = Decimal("0")
    for txn in txns:
        debit = sum((line.debit for line in txn.lines), Decimal("0"))
        credit = sum((line.credit for line in txn.lines), Decimal("0"))
        total_debit += debit
        total_credit += credit
        entries.append(
            {
                "id": txn.id,
                "date": txn.date.isoformat(),
                "reference": txn.reference or "",
                "description": header_text(txn),
                "posted_by": txn.posted_by or "",
                "source_type": txn.source_type or "",
                "voided": txn.id in voided,
                "is_reversal": bool(
                    txn.source_type and txn.source_type.endswith("_void")
                ),
                "lines": [
                    _line_dict(line, accounts.get(line.account_id))
                    for line in txn.lines
                ],
                "total_debit": float(debit),
                "total_credit": float(credit),
            }
        )
    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "entries": entries,
        "count": len(entries),
        "total_debit": float(total_debit),
        "total_credit": float(total_credit),
    }


def _adjusting_section(data: dict) -> dict:
    rows = []
    for e in data["entries"]:
        head = f"#{e['id']}  {e['date']}  {e['description']}"
        if e["reference"]:
            head += f"  ({e['reference']})"
        if e["posted_by"]:
            head += f"  posted by {e['posted_by']}"
        if e["voided"]:
            head += "  VOIDED"
        rows.append({"cells": [head, "", ""], "style": "subtotal"})
        for ln in e["lines"]:
            name = ln["account_name"]
            if ln["account_number"]:
                name = f"{ln['account_number']} {name}"
            if ln["description"]:
                name += f" — {ln['description']}"
            rows.append(
                {
                    "cells": [
                        "    " + name,
                        _money(ln["debit"]) if ln["debit"] else "",
                        _money(ln["credit"]) if ln["credit"] else "",
                    ]
                }
            )
    rows.append(
        {
            "cells": [
                f"Total ({data['count']} entries)",
                _money(data["total_debit"]),
                _money(data["total_credit"]),
            ],
            "style": "grand-total",
        }
    )
    return {
        "title": "Adjusting Journal Entries",
        "period": f"{data['start_date']} — {data['end_date']}",
        "columns": ["Entry", "Debit", "Credit"],
        "rows": rows,
    }


@router.get("/adjusting-entries/csv")
def adjusting_entries_csv_route(
    request: Request,
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    db: Session = Depends(get_db),
):
    from app.services.ledger_exports import adjusting_entries_csv

    data = adjusting_entries(start_date, end_date, db)
    return _csv_download(
        adjusting_entries_csv(data, _company_name(db)),
        f"adjusting-entries_{data['start_date']}_{data['end_date']}.csv",
        request,
    )


@router.get("/adjusting-entries/pdf")
def adjusting_entries_pdf_route(
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    db: Session = Depends(get_db),
):
    data = adjusting_entries(start_date, end_date, db)
    return _pdf_response(
        [_adjusting_section(data)],
        db,
        f"adjusting-entries_{data['start_date']}_{data['end_date']}.pdf",
    )


# ── Closing Date Exceptions (#342) ───────────────────────────────────────


@router.get("/closing-date-exceptions")
def closing_date_exceptions_route(
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    tz_offset: int = Query(default=0, ge=-900, le=900),
    db: Session = Depends(get_db),
):
    """Every change made into a closed period with the closing-date
    password, in the period the changes were made: when, who, the closing
    date in force, and each row it touched with its fields before and
    after. Ordinary posting after the closing date is not here.
    ``tz_offset`` is the page's clock offset from UTC in minutes (what
    JavaScript's getTimezoneOffset gives): the period's days and every
    time shown are the person's own."""
    from app.services.closing_date_exceptions import closing_date_exceptions

    start_date, end_date = _range_or_year(db, start_date, end_date)
    return closing_date_exceptions(db, start_date, end_date, tz_offset)


@router.get("/closing-date-exceptions/csv")
def closing_date_exceptions_csv_route(
    request: Request,
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    tz_offset: int = Query(default=0, ge=-900, le=900),
    db: Session = Depends(get_db),
):
    from app.services.closing_date_exceptions import exceptions_csv

    data = closing_date_exceptions_route(start_date, end_date, tz_offset, db)
    return _csv_download(
        exceptions_csv(data, _company_name(db)),
        f"closing-date-exceptions_{data['start_date']}_{data['end_date']}.csv",
        request,
    )


@router.get("/closing-date-exceptions/pdf")
def closing_date_exceptions_pdf_route(
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    tz_offset: int = Query(default=0, ge=-900, le=900),
    db: Session = Depends(get_db),
):
    from app.services.closing_date_exceptions import exceptions_section

    data = closing_date_exceptions_route(start_date, end_date, tz_offset, db)
    return _pdf_response(
        [exceptions_section(data)],
        db,
        f"closing-date-exceptions_{data['start_date']}_{data['end_date']}.pdf",
        landscape=True,
    )
