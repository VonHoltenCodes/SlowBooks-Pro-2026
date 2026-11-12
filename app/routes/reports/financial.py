from datetime import date
from decimal import Decimal
from typing import Annotated, Optional

from fastapi import Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session
from sqlalchemy import func as sqlfunc, or_

from app.database import get_db
from app.models.accounts import Account, AccountType, DetailType
from app.models.transactions import Transaction, TransactionLine
from app.routes.reports._router import router
from app.schemas.common import BIGINT_MAX, BIGINT_MIN
from app.services import cash_basis
from app.services.fiscal_year import fiscal_year_start
from app.services.account_label import account_label
from app.services.settings_service import use_account_numbers
from app.services.statement_layout import (
    BS_GROUP_TOTALS,
    BS_GROUPS,
    MAX_DEPTH,
    account_chart,
    bs_group_of,
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
from app.routes._helpers import refuse_unknown_filters


def _in_class(db, class_id):
    """The class `class_id` (404 if there's none) and the SQL test for a
    posted line belonging to it: the line's own class, else its
    transaction's, else Uncategorized, as P&L by Class groups them (#213)."""
    from app.models.classes import TxnClass
    from app.services.classes_service import (
        class_attribution,
        find_uncategorized_class_id,
    )

    cls = db.get(TxnClass, class_id)
    if cls is None:
        raise HTTPException(status_code=404, detail="Class not found")
    uncat_id = find_uncategorized_class_id(db)
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


class ChartIndex(dict):
    """{id: (number, name, parent, type, detail type)} plus `numbered`: whether
    the company uses account numbers (#567). A plain dict built by hand (a
    test) reads as numbered."""

    numbered: bool = True


def _chart_index(db) -> ChartIndex:
    """Every account's (number, name, parent, type, detail type), by id: one
    query a statement builds once and hands to every helper that needs the
    chart (#390 review: the compare P&L read it some sixteen times)."""
    from app.services.settings_service import use_account_numbers

    chart = ChartIndex(
        (
            a.id,
            (
                a.account_number or "",
                a.name or "",
                a.parent_id,
                a.account_type,
                a.detail_type,
            ),
        )
        for a in db.query(
            Account.id,
            Account.account_number,
            Account.name,
            Account.parent_id,
            Account.account_type,
            Account.detail_type,
        )
    )
    chart.numbered = use_account_numbers(db)
    return chart


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
    # a company that does not use account numbers lists by name (#567)
    by_name = sort == "name" or not getattr(chart, "numbered", True)

    def level(account_id):
        number, name = chart[account_id][:2]
        name = name.lower()
        if by_name:
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
    return path, account_label(number, path, getattr(chart, "numbered", True))


_EXCLUDE_ADJUSTING = Query(
    default=False,
    description="Leave the adjusting entries out (#341): the figures before "
    "the accountant's corrections",
)
# Which basis the statement reads (#483): accrual, cash, or left out for
# the company default (Settings → Company, `report_basis`).
_BASIS_QUERY = Query(
    default=None,
    description="accrual or cash; omitted, the company's default report basis",
)


def _flag(value) -> bool:
    """A query flag as a bool: other routes call these report functions
    directly, and then the parameter holds its Query() default, not False
    (the statements pack read every statement without its adjusting
    entries until this was caught by the package test, #343)."""
    return value is True


# Hide zero balances (#625, from Discussion #354): an account that had
# activity but nets to nothing for the period, or a balance-sheet account
# that ends at zero, is left off the report; the totals are the same.
_HIDE_ZERO = Query(
    default=False,
    description="Leave out an account whose figures for the period are zero "
    "(#625); the totals are unchanged",
)


def _with_figures(lines: list, keys=("amount",)) -> list:
    """The lines with a figure in any of `keys` (Hide zero balances, #625):
    one that rounds to nothing in every column goes. Run on a section's
    flat lines after its totals are added up and before the layout, so a
    parent whose every sub-account is zero loses its heading too and the
    totals are the same either way."""
    return [i for i in lines if any(abs(i.get(k) or 0) >= 0.005 for k in keys)]


def _totals_by_account(
    db,
    acct_type,
    date_start=None,
    date_end=None,
    in_class=None,
    sort="number",
    exclude_adjusting=False,
    chart: dict | None = None,
    basis: str = "accrual",
):
    """Return a list of {account_id, account_name, account_number,
    detail_type, amount} rows where amount is signed by the account type's
    natural balance (always positive for a normal-balance ledger), in the
    chart's order (`sort`: number or name, see _account_order).

    `account_id` is included so the SPA can drill into /account-transactions
    for any line — Phase 11 drill-down support. `exclude_adjusting` leaves
    the adjusting entries (#341) out, for the figures before them. `basis`
    picks the reading of the ledger (#483): accrual, or cash.
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
    ).execution_options(**cash_basis.reading(basis))

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
        Optional[int],
        Query(
            description="Only this class's lines (#213); omit for all",
            ge=BIGINT_MIN,
            le=BIGINT_MAX,
        ),
    ] = None,
    sort: str = _SORT_QUERY,
    exclude_adjusting: bool = _EXCLUDE_ADJUSTING,
    compare: Annotated[
        Optional[str],
        Query(description="prior_year adds the same period one year earlier"),
    ] = None,
    columns: Annotated[
        Optional[str],
        Query(
            description="month or quarter: one column per month or quarter of "
            "the period, and a Total column (#478)"
        ),
    ] = None,
    basis: str = _BASIS_QUERY,
    hide_zero: bool = _HIDE_ZERO,
):
    _check_compare(compare)
    basis = cash_basis.resolve_basis(db, basis if isinstance(basis, str) else None)
    hide_zero = _flag(hide_zero)
    columns = columns if isinstance(columns, str) and columns else None
    if columns not in (None, "month", "quarter"):
        raise HTTPException(
            status_code=422, detail="columns must be month, quarter or left out"
        )
    if columns and compare:
        raise HTTPException(
            status_code=400,
            detail="A P&L by month or quarter has no prior-year comparison",
        )
    exclude_adjusting = _flag(exclude_adjusting)
    sort = sort if isinstance(sort, str) else "number"
    if not start_date:
        start_date = fiscal_year_start(db, date.today())
    if not end_date:
        end_date = date.today()
    cls, in_class = _in_class(db, class_id) if class_id is not None else (None, None)
    chart = _chart_index(db)

    def period(start, end):
        return _pl_period(
            db,
            start,
            end,
            cls,
            in_class,
            sort,
            exclude_adjusting,
            chart,
            basis,
            hide_zero,
        )

    if columns:
        return _pl_columns(db, start_date, end_date, columns, period, sort, chart)
    if compare == "prior_year":
        current = period(start_date, end_date)
        prior = period(_year_earlier(start_date), _year_earlier(end_date))
        return _pl_layout(db, _pl_with_prior(db, current, prior, sort, chart), chart)
    return _pl_layout(db, period(start_date, end_date), chart)


# A P&L by month or quarter (#478) has at most this many columns: five
# years of months. Past it the request is refused, not a grid nobody reads.
MAX_PERIOD_COLUMNS = 60
# the grid's column keys (P&L by Class's, _pl_pivot) and the P&L's own
_COLUMN_KEYS = {
    "income": "total_income",
    "operating_income": "total_operating_income",
    "cogs": "total_cogs",
    "gross_profit": "gross_profit",
    "operating_gross_profit": "operating_gross_profit",
    "expenses": "total_expenses",
    "operating_expenses": "total_operating_expenses",
    "net_operating_income": "net_operating_income",
    "other_income": "total_other_income",
    "other_expense": "total_other_expense",
    "net_other_income": "net_other_income",
    "net_income": "net_income",
}


def _period_columns(db, start: date, end: date, columns: str) -> list[dict]:
    """The period cut into months, or quarters of the fiscal year (#444),
    the first and last clipped to the dates asked: [{key, name, start_date,
    end_date}]. A whole month reads "Jan 2026", a whole quarter "Jan–Mar
    2026", a clipped one by its own dates."""
    import calendar

    from app.services.fiscal_year import start_month

    step = 1 if columns == "month" else 3
    first_month = start_month(db) if columns == "quarter" else 1
    # the column the start date falls in: its month, or the fiscal quarter
    m = start.month
    if step == 3:
        m = start.month - ((start.month - first_month) % 3)
    y = start.year if m <= start.month else start.year - 1
    if m < 1:
        m += 12
        y -= 1
    out = []
    while True:
        col_start = date(y, m, 1)
        em, ey = m + step - 1, y
        while em > 12:
            em -= 12
            ey += 1
        col_end = date(ey, em, calendar.monthrange(ey, em)[1])
        if col_start > end:
            break
        lo, hi = max(col_start, start), min(col_end, end)
        whole = (lo, hi) == (col_start, col_end)
        if not whole:
            name = (
                f"{lo:%b} {lo.day}–{hi.day}, {hi.year}"
                if (lo.year, lo.month) == (hi.year, hi.month)
                else f"{lo:%b} {lo.day}–{hi:%b} {hi.day}, {hi.year}"
            )
        elif step == 1:
            name = f"{lo:%b} {lo.year}"
        else:
            name = f"{lo:%b}–{hi:%b} {hi.year}"
        out.append(
            {
                "key": f"{lo.isoformat()}_{hi.isoformat()}",
                "name": name,
                "start_date": lo.isoformat(),
                "end_date": hi.isoformat(),
            }
        )
        if len(out) > MAX_PERIOD_COLUMNS:
            raise HTTPException(
                status_code=400,
                detail=f"At most {MAX_PERIOD_COLUMNS} columns: choose a shorter period"
                + (" or quarters" if step == 1 else ""),
            )
        m += step
        while m > 12:
            m -= 12
            y += 1
    return out


def _pl_columns(db, start_date, end_date, columns, period, sort, chart) -> dict:
    """The P&L with one column per month or quarter and a Total (#478),
    in P&L by Class's grid shape (_pl_pivot), so its screen, PDF and CSV are
    the grid's: `columns` with each period's totals, `accounts` by section
    with amounts[] across them and the account's total. The P&L runs once
    per column and once for the whole period, which is the Total column:
    it is the plain P&L's figures to the cent. `tree` is each section as
    the P&L prints it, sub-accounts beneath their parents with a total per
    family in every column (#416)."""
    cols = _period_columns(db, start_date, end_date, columns)
    whole = period(start_date, end_date)
    runs = [
        period(date.fromisoformat(c["start_date"]), date.fromisoformat(c["end_date"]))
        for c in cols
    ]
    out_cols = []
    for c, run in zip(cols, runs):
        out_cols.append(
            {
                **c,
                "inactive": False,
                "empty": not any(run[k] for k in _PL_SECTIONS),
                **{k: run[v] for k, v in _COLUMN_KEYS.items()},
            }
        )
    # every account with a figure in some column, in the P&L's order
    by_column = [
        {i["account_id"]: i["amount"] for key in _PL_SECTIONS for i in run[key]}
        for run in runs
    ]
    total_of = {i["account_id"]: i for key in _PL_SECTIONS for i in whole[key]}
    section_of = {aid: _pl_section_of(row[3], row[4]) for aid, row in chart.items()}
    order = _account_order(db, sort, chart)
    accounts = {key: [] for key in _PL_SECTIONS}
    seen = set(total_of)
    for amounts in by_column:
        seen |= set(amounts)
    for aid in sorted(seen, key=lambda a: order({"account_id": a})):
        amounts = [round(m.get(aid, 0.0), 2) for m in by_column]
        if not any(amounts) and not (total_of.get(aid) or {}).get("amount"):
            continue
        path, label = _account_words(chart, aid)
        number, name = chart[aid][0], chart[aid][1]
        accounts[section_of[aid]].append(
            {
                "account_id": aid,
                "account_number": number,
                "account_name": name,
                "account_path": path or name,
                "account_label": label or name,
                "amounts": amounts,
                "total": (total_of.get(aid) or {}).get("amount", 0.0),
            }
        )
    tree = {
        key: _grid_tree(accounts[key], len(cols), chart, section_of, order, key)
        for key in _PL_SECTIONS
    }
    data = {
        "start_date": whole["start_date"],
        "end_date": whole["end_date"],
        "class_id": whole["class_id"],
        "class_name": whole["class_name"],
        "sort": sort,
        "exclude_adjusting": whole["exclude_adjusting"],
        "basis": whole["basis"],
        "basis_note": whole["basis_note"],
        "hide_zero": whole["hide_zero"],
        "columns_by": columns,
        "columns": out_cols,
        "accounts": accounts,
        "tree": tree,
        **{f"total_{k}": whole[v] for k, v in _COLUMN_KEYS.items()},
        "has_other": bool(accounts["other_income"] or accounts["other_expense"]),
        "filtered": False,
        "columns_total": len(out_cols),
        "chosen_empty": [],
    }
    # the P&L's own keys too, for a reader that knows the plain P&L
    for v in _COLUMN_KEYS.values():
        data.setdefault(v, whole[v])
    return data


def _grid_tree(lines, n, chart, section_of, order, key) -> list[dict]:
    """A grid section's accounts as the P&L prints them (#416, #478):
    sub-accounts beneath their parents, a parent's own postings as "Direct
    postings to …" and a "Total …" per family, in every column and the
    Total; each row's amounts[] across the columns and its total."""
    keys = tuple(f"c{i}" for i in range(n)) + ("total",)
    flat = [{**a, **{f"c{i}": x for i, x in enumerate(a["amounts"])}} for a in lines]
    rows = family_rows(
        flat,
        account_chart(index=chart),
        lambda aid: section_of.get(aid) == key,
        keys,
        lambda aid: order({"account_id": aid}),
    )
    out = []
    for r in rows:
        row = {
            k: r.get(k)
            for k in (
                "kind",
                "depth",
                "label",
                "account_id",
                "account_number",
                "account_name",
                "account_path",
                "account_label",
            )
            if k in r
        }
        if r["kind"] != "heading":
            row["amounts"] = [round(r.get(f"c{i}") or 0.0, 2) for i in range(n)]
            row["total"] = round(r.get("total") or 0.0, 2)
        out.append(row)
    return out


def _pl_period(
    db,
    start_date,
    end_date,
    cls,
    in_class,
    sort,
    exclude_adjusting,
    chart,
    basis="accrual",
    hide_zero=False,
):
    """One period's P&L figures, before the layout. `hide_zero` (#625)
    leaves out an account that nets to nothing for the period, after the
    totals are added up, so they are the same either way."""

    def totals(acct_type):
        return _totals_by_account(
            db,
            acct_type,
            start_date,
            end_date,
            in_class,
            sort,
            exclude_adjusting,
            chart,
            basis,
        )

    income, other_income = _split_other(
        totals(AccountType.INCOME), DetailType.OTHER_INCOME
    )
    cogs = totals(AccountType.COGS)
    expenses, other_expense = _split_other(
        totals(AccountType.EXPENSE), DetailType.OTHER_EXPENSE
    )

    out = {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "class_id": cls.id if cls else None,
        "class_name": cls.name if cls else None,
        "sort": sort,
        "exclude_adjusting": bool(exclude_adjusting),
        "basis": basis,
        "basis_note": cash_basis.receivable_note(db, end_date, basis),
        "hide_zero": bool(hide_zero),
        "income": income,
        "cogs": cogs,
        "expenses": expenses,
        "other_income": other_income,
        "other_expense": other_expense,
        **pl_totals(income, cogs, expenses, other_income, other_expense),
    }
    if hide_zero:
        for key in _PL_SECTIONS:
            out[key] = _with_figures(out[key])
    return out


def pl_totals(income, cogs, expenses, other_income, other_expense) -> dict:
    """The P&L's totals from its sections' lines (each a dict with an
    `amount`): the statement's and every report that ties to it (the P&L
    Detail, #477) add up the same way."""
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


# the Balance Sheet's computed equity lines, in print order (#708)
_COMPUTED_ORDER = {"retained_earnings": 1, "net_income": 2}


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
        # the computed lines last, Retained Earnings before Net Income (#708)
        lines.sort(
            key=lambda i: (
                i.get("account_id") is None,
                _COMPUTED_ORDER.get(i.get("line"), 0),
                order(i),
            )
        )
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
    basis: str = _BASIS_QUERY,
    hide_zero: bool = _HIDE_ZERO,
):
    _check_compare(compare)
    basis = cash_basis.resolve_basis(db, basis if isinstance(basis, str) else None)
    hide_zero = _flag(hide_zero)
    sort = sort if isinstance(sort, str) else "number"
    t = terms_from_db(db)
    if not as_of_date:
        as_of_date = date.today()
    chart = _chart_index(db)

    def period(as_of):
        return _bs_period(db, as_of, sort, t, chart, basis, hide_zero)

    if compare == "prior_year":
        data = _bs_with_prior(
            db,
            period(as_of_date),
            period(_year_earlier(as_of_date)),
            sort,
            chart,
        )
    else:
        data = period(as_of_date)
    return _bs_layout(db, _name_prior_years(data, t), chart)


# The computed line of earlier years' net income beside a posted Retained
# Earnings account (#708, macbase1): with 3200 Retained Earnings holding a
# balance on the sheet, a second line also called Retained Earnings could
# not be told from it. It is then named for what it is, the earnings of
# earlier fiscal years not yet closed to Retained Earnings; with no posted
# balance it stays Retained Earnings, as QuickBooks prints it. A nonprofit's
# words (Net Assets from Prior Years) are distinct either way. The line's
# key, "retained_earnings", never changes.
RETAINED_EARNINGS_NUMBER = "3200"
PRIOR_YEARS_WORDS = "Net Income, prior years"


def _name_prior_years(data: dict, t) -> dict:
    if t("Equity") != "Equity":  # a nonprofit: its own words
        return data
    equity = data["equity"]
    posted = any(
        e.get("account_id") is not None
        and e.get("account_number") == RETAINED_EARNINGS_NUMBER
        and (
            round(float(e.get("amount") or 0), 2)
            or round(float(e.get("prior_amount") or 0), 2)
        )
        for e in equity
    )
    if not posted:
        return data
    data["equity"] = [
        (
            dict(
                e,
                account_name=PRIOR_YEARS_WORDS,
                account_path=PRIOR_YEARS_WORDS,
                account_label=PRIOR_YEARS_WORDS,
            )
            if e.get("account_id") is None and e.get("line") == "retained_earnings"
            else e
        )
        for e in equity
    ]
    return data


def _bs_period(
    db, as_of_date, sort, t, chart, basis="accrual", hide_zero=False
) -> dict:
    """One date's Balance Sheet figures, before the layout. `hide_zero`
    (#625) leaves out an account that stands at zero on the date, after
    the totals (the groups' too) are added up, so they are the same either
    way; a group whose every account is zero then prints no heading, as
    one with no account never did."""

    def totals(acct_type, order="number"):
        return _totals_by_account(
            db, acct_type, date_end=as_of_date, sort=order, chart=chart, basis=basis
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

    # Net income for all periods up to as_of_date flows into equity.
    # Without this, the balance sheet fails to balance whenever there is
    # income or expense activity that hasn't been formally closed into an
    # equity account (which is the normal state in this app — income/expense
    # accounts are never explicitly closed). It prints as two lines (#708),
    # as QuickBooks prints them: Retained Earnings, the net income of every
    # fiscal year before the one the as-of date falls in, and Net Income,
    # that fiscal year's to the date. A posted retained-earnings account
    # keeps its own line, as it always has.
    def net_income_between(date_start=None, date_end=as_of_date):
        def total(acct_type):
            rows = _totals_by_account(
                db,
                acct_type,
                date_start=date_start,
                date_end=date_end,
                chart=chart,
                basis=basis,
            )
            return round(sum(r["amount"] for r in rows), 2)

        # Rounded to the cent the same way profit_loss rounds its net income
        # (each total to the cent, then subtract and round), so the two
        # reports agree and no float noise leaks into equity (#413).
        return round(
            total(AccountType.INCOME)
            - total(AccountType.COGS)
            - total(AccountType.EXPENSE),
            2,
        )

    net_income = net_income_between()
    year_net = net_income_between(fiscal_year_start(db, as_of_date))
    # the years before: what is left of the whole, so the two lines add up
    # to the one they replace to the cent and total equity is unchanged
    prior_net = round(net_income - year_net, 2)
    nonprofit = t("Equity") != "Equity"
    computed = (
        (
            "retained_earnings",
            "Net Assets from Prior Years" if nonprofit else "Retained Earnings",
            prior_net,
        ),
        ("net_income", t("Net Income"), year_net),
    )
    for line, words, amount in computed:
        if amount == 0:
            continue
        equity = list(equity) + [
            {
                "account_id": None,
                "line": line,
                "account_name": words,
                "account_number": None,
                "account_path": words,
                "account_label": words,
                "amount": float(amount),
            }
        ]
    total_equity = round(total_equity + net_income, 2)

    group_totals = {
        **bs_group_totals(assets, "assets"),
        **bs_group_totals(liabilities, "liabilities"),
    }
    if hide_zero:
        assets = _with_figures(assets)
        liabilities = _with_figures(liabilities)
        equity = _with_figures(equity)

    return {
        "as_of_date": as_of_date.isoformat(),
        "sort": sort,
        "basis": basis,
        "basis_note": cash_basis.receivable_note(db, as_of_date, basis),
        "hide_zero": bool(hide_zero),
        "assets": assets,
        "liabilities": liabilities,
        "equity": equity,
        "total_assets": total_assets,
        "total_liabilities": total_liabilities,
        "total_equity": total_equity,
        **group_totals,
    }


def _bs_layout(db, data: dict, chart: dict) -> dict:
    """The Balance Sheet's groups (#390) beside its flat lists: for assets
    and liabilities, [{heading, total_key, lines, tree}] in print order, so
    the screen, the PDF and the CSV classify nothing. The flat lists stay
    what the prior-year pairing and the drill-down read.

    `tree` is a group's lines with sub-accounts beneath their parents, a
    "Direct postings to …" line and a "Total …" per family (#565, as the
    P&L has since #416), and `tree.equity` the equity side's. A parent
    gathers only the sub-accounts that print in its own group: one placed
    elsewhere by its detail type prints flat in that group, by its full
    path, the way #416 treats a child of another P&L section. Built after
    the prior-year pairing, so a family total has every column. The
    synthetic Net Income line (no account) prints last, as it came."""
    order = _account_order(db, data.get("sort") or "number", chart)
    accounts = account_chart(index=chart)
    keys = ("amount", "prior_amount") if data.get("prior") else ("amount",)
    placed = bs_print_details(index=chart)
    spot = {
        aid: bs_group_of(row[3], placed.get(aid) or row[4])
        for aid, row in chart.items()
    }

    def tree(lines, side, group=None):
        """The rows of one group (or of the equity side, group None)."""
        return family_rows(
            lines,
            accounts,
            lambda aid: spot.get(aid, (None, None))[0] == side
            and (group is None or spot[aid][1][0] == group),
            keys,
            # the detail types' order inside the group holds (Bank before
            # Other Current Asset), then the sort choice
            lambda aid: (spot.get(aid, (None, (0, 0)))[1], order({"account_id": aid})),
        )

    data["groups"] = {}
    for side in BS_GROUPS:
        headings = [heading for heading, _key, _details in BS_GROUPS[side]]
        data["groups"][side] = [
            {
                "heading": heading,
                "total_key": key,
                "lines": lines,
                "tree": tree(lines, side, headings.index(heading)),
            }
            for heading, key, lines in bs_groups(data[side], side)
        ]
    data["tree"] = {"equity": tree(data["equity"], "equity")}
    return data


@router.get("/general-ledger")
def general_ledger(
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    account_id: int = Query(default=None, ge=BIGINT_MIN, le=BIGINT_MAX),
    db: Session = Depends(get_db),
    # after db, and a plain None by default: the csv and pdf routes call
    # this directly with db as the fourth argument
    class_id: Annotated[
        Optional[int],
        Query(
            description="Only this class's lines (#236); omit for all",
            ge=BIGINT_MIN,
            le=BIGINT_MAX,
        ),
    ] = None,
    basis: str = _BASIS_QUERY,
):
    """General Ledger detail report. With `class_id`, only the lines of
    that class (the line's own, else its transaction's, else Uncategorized,
    as P&L by Class groups them), the balance brought forward included —
    computed under the same test, or the running balances would be wrong
    (#236). `basis` (#483) reads the ledger as accrual or cash, the balance
    brought forward on the same one."""
    basis = cash_basis.resolve_basis(db, basis if isinstance(basis, str) else None)
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
    results = q.execution_options(**cash_basis.reading(basis)).all()

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
        before = before.group_by(TransactionLine.account_id).execution_options(
            **cash_basis.reading(basis)
        )
        for acct_id, dr, cr in before.all():
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
    from app.services.bank_register import source_link, voided_transaction_ids
    from app.services.edit_targets import edit_target

    gl_voided = voided_transaction_ids(db, {txn.id for _, txn, _ in results})
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
                "source_kind": txn.source_kind,
                "class_name": class_names.get(tl.class_id or txn.class_id)
                or uncat_name,
                # the line opens its transaction and edits it (#689), as
                # the drill-down's lines do
                "line_id": tl.id,
                "transaction_id": txn.id,
                "source_link": source_link(txn) or f"/#/journal/{txn.id}",
                "voided": txn.id in gl_voided
                or bool((txn.source_type or "").endswith("_void")),
                "edit": edit_target(txn, txn.id in gl_voided),
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
        "basis": basis,
        "basis_note": cash_basis.receivable_note(db, end_date, basis),
        "accounts": accounts_list,
    }


@router.get("/account-transactions")
def account_transactions(
    account_id: int = Query(
        default=None,
        description="Account to drill into; or section / family for several",
        ge=BIGINT_MIN,
        le=BIGINT_MAX,
    ),
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    db: Session = Depends(get_db),
    # after db, and a plain None by default: other routes call this
    # directly, with db as the third argument
    class_id: Annotated[
        Optional[int],
        Query(
            description="Only this class's lines (#213); omit for all",
            ge=BIGINT_MIN,
            le=BIGINT_MAX,
        ),
    ] = None,
    job_id: Annotated[
        Optional[int],
        Query(
            description="Only this job's lines (#242); 0 for the lines with no job",
            ge=BIGINT_MIN,
            le=BIGINT_MAX,
        ),
    ] = None,
    section: Annotated[
        Optional[str],
        Query(
            description="A P&L total's accounts in place of one account (#479)",
            pattern="^(income|cogs|expenses|other_income|other_expense|gross_profit"
            "|net_operating_income|net_other_income|net_income)$",
        ),
    ] = None,
    family: Annotated[
        Optional[int],
        Query(
            description="A parent account and its sub-accounts (#479)",
            ge=BIGINT_MIN,
            le=BIGINT_MAX,
        ),
    ] = None,
    exclude_adjusting: bool = _EXCLUDE_ADJUSTING,
    basis: str = _BASIS_QUERY,
):
    """Phase 11: drill-down support. Every journal entry line hitting a
    given account in the date range, with source document linkage so the
    UI can jump from a P&L row straight to the underlying invoice/bill/JE.
    The register service (bank_register.account_register) does the work —
    the bank register is the same view.

    In place of one account, a set of them (#479): `section` is one of
    the P&L's totals (income, cogs, expenses, other_income, other_expense,
    gross_profit, net_operating_income, net_other_income, net_income) and
    `family` a parent account with its sub-accounts. Every line of the set,
    each with its account, signed toward the total, with a running total
    from zero that ends on the statement's figure (`period_net`), for the
    same dates, class, job and adjusting-entries choice. `basis` (#483):
    accrual or cash, else the company's default."""
    from app.services.bank_register import account_register

    basis = cash_basis.resolve_basis(db, basis if isinstance(basis, str) else None)
    section = section if isinstance(section, str) else None
    family = family if isinstance(family, int) else None
    exclude_adjusting = _flag(exclude_adjusting)
    asked = [x for x in (account_id, section, family) if x is not None]
    if len(asked) != 1:
        raise HTTPException(
            status_code=400,
            detail="Give one of account_id, section or family",
        )
    if not start_date:
        start_date = fiscal_year_start(db, date.today())
    if not end_date:
        end_date = date.today()
    if account_id is None:
        return _account_set(
            db,
            start_date,
            end_date,
            section,
            family,
            class_id,
            job_id,
            exclude_adjusting,
            basis,
        )
    acct = db.query(Account).filter(Account.id == account_id).first()
    if not acct:
        raise HTTPException(status_code=404, detail="Account not found")
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
        db, acct, start_date, end_date, class_id=class_id, job_id=job_id, basis=basis
    )
    out["basis"] = basis
    out["basis_note"] = cash_basis.receivable_note(db, end_date, basis)
    out["start_date"] = start_date.isoformat()
    out["end_date"] = end_date.isoformat()
    out["class_id"] = cls.id if cls else None
    out["class_name"] = cls.name if cls else None
    out["job_id"] = job_id
    out["job_name"] = job_name
    return out


def _job_words(db, job_id) -> Optional[str]:
    """The job's name for a drill-down's title, "No job" for 0 (#242)."""
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


def _account_set(
    db,
    start_date,
    end_date,
    section,
    family,
    class_id,
    job_id,
    exclude_adjusting,
    basis="accrual",
) -> dict:
    """The drill-down for a P&L total or a sub-account family (#479)."""
    from app.services.pl_detail import SET_TOTALS, account_set_lines

    t = terms_from_db(db)
    chart = _chart_index(db)
    if family is not None:
        if family not in chart:
            raise HTTPException(status_code=404, detail="Account not found")
        if chart[family][3] not in _PL_TYPES:
            raise HTTPException(
                status_code=400,
                detail="A family is a Profit & Loss account and its sub-accounts",
            )
        label = f"Total {_account_words(chart, family)[1]}"
        total_key = None
    else:
        words, total_key = SET_TOTALS[section]
        label = t(words)
    cls = _in_class(db, class_id)[0] if class_id is not None else None
    job_name = _job_words(db, job_id)
    out = account_set_lines(
        db,
        start_date,
        end_date,
        section=section,
        family=family,
        class_id=class_id,
        job_id=job_id,
        exclude_adjusting=exclude_adjusting,
        words=t,
        basis=basis,
    )
    out.update(
        {
            "set": {
                "kind": "family" if family is not None else "section",
                "section": section,
                "family": family,
                "label": label,
                "total_key": total_key,
            },
            "account": None,
            "opening_balance": 0.0,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "class_id": cls.id if cls else None,
            "class_name": cls.name if cls else None,
            "job_id": job_id,
            "job_name": job_name,
            "exclude_adjusting": bool(exclude_adjusting),
            "basis": basis,
            "basis_note": cash_basis.receivable_note(db, end_date, basis),
        }
    )
    return out


# ============================================================================
# Phase 10: Quick Wins — Trial Balance, Cash Flow, Batch Email,
#            Collection Letters, 1099 Summary
# ============================================================================


# read as of the end date on the Trial Balance; the other types, for the period
_BALANCE_SHEET_TYPES = (AccountType.ASSET, AccountType.LIABILITY, AccountType.EQUITY)


@router.get("/first-date")
def first_date(db: Session = Depends(get_db)):
    """The date of the first transaction in the books, or null with none:
    where the reports' All dates period starts (#707). A report route reads
    a missing start date as the fiscal year's first day, so All dates sends
    this date as its start."""
    first = db.query(sqlfunc.min(Transaction.date)).scalar()
    return {"first_date": first.isoformat() if first else None}


# The Trial Balance's end date under the Balance Sheet's name (#806): a
# client that sends as_of_date, as it would to the Balance Sheet, gets the
# trial balance as of that day rather than as of today.
_TB_AS_OF = Query(
    default=None,
    description=(
        "The same as end_date (the Balance Sheet's name for it); when both "
        "are sent they must be the same day"
    ),
)


def _tb_end_date(end_date, as_of_date):
    end_date = end_date if isinstance(end_date, date) else None
    as_of_date = as_of_date if isinstance(as_of_date, date) else None
    if end_date and as_of_date and end_date != as_of_date:
        raise HTTPException(
            status_code=400,
            detail=(
                "The end date and the as-of date are different days. "
                "Send one of them, or the same day in both."
            ),
        )
    return end_date or as_of_date


@router.get("/trial-balance")
def trial_balance(
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    db: Session = Depends(get_db),
    exclude_adjusting: bool = _EXCLUDE_ADJUSTING,
    basis: str = _BASIS_QUERY,
    hide_zero: bool = _HIDE_ZERO,
    as_of_date: date = _TB_AS_OF,
):
    """Trial Balance as QuickBooks reads it: asset, liability and equity
    accounts at their balance as of the end date (one debit or credit
    figure), income, cost and expense accounts at their activity from the
    start date. The two readings differ by the net income of the periods
    before the start, so that sits in one derived equity row and the report
    still foots. `basis` (#483): accrual or cash, else the company's default.
    `hide_zero` (#625) leaves out a row with nothing in either column (a
    balance-sheet account that stands at zero); a P&L account whose debits
    and credits offset keeps both figures, so each column still adds up to
    its total. `as_of_date` (#806) is the end date under the Balance Sheet's
    name: either may be sent; when both are, they must be the same day."""
    basis = cash_basis.resolve_basis(db, basis if isinstance(basis, str) else None)
    exclude_adjusting = _flag(exclude_adjusting)
    hide_zero = _flag(hide_zero)
    end_date = _tb_end_date(end_date, as_of_date)
    if not end_date:
        end_date = date.today()
    if not start_date:
        # the fiscal year the end date falls in, not today's: an end date
        # alone asks for that year's trial balance
        start_date = fiscal_year_start(db, end_date)
    if start_date > end_date:
        raise HTTPException(
            status_code=400, detail="The start date is after the end date."
        )

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
        .filter(
            Transaction.date <= end_date,
            # a balance-sheet account counts from the beginning, the rest
            # from the start date
            or_(
                Account.account_type.in_(_BALANCE_SHEET_TYPES),
                Transaction.date >= start_date,
            ),
        )
    )
    if exclude_adjusting:
        q = q.filter(Transaction.is_adjusting == False)  # noqa: E712
    results = (
        q.group_by(
            Account.id, Account.account_number, Account.name, Account.account_type
        )
        .execution_options(**cash_basis.reading(basis))
        .all()
    )

    items = []
    total_debit = Decimal(0)
    total_credit = Decimal(0)
    chart = _chart_index(db)
    for acct_id, acct_num, acct_name, acct_type, debit, credit in results:
        if acct_type in _BALANCE_SHEET_TYPES:
            # the balance as of the end date, on its normal side
            debit, credit = max(debit - credit, Decimal(0)), max(
                credit - debit, Decimal(0)
            )
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

    # net income of the periods before the start date, which the balance-sheet
    # accounts carry but the income and expense rows leave out (the Balance
    # Sheet's "Net Income" line, split at the start date); bounded by the end
    # date like every other figure, since the balance-sheet rows stop there
    prior = (
        db.query(
            sqlfunc.coalesce(sqlfunc.sum(TransactionLine.debit), 0)
            - sqlfunc.coalesce(sqlfunc.sum(TransactionLine.credit), 0)
        )
        .join(Account, TransactionLine.account_id == Account.id)
        .join(Transaction, TransactionLine.transaction_id == Transaction.id)
        .filter(
            Account.account_type.notin_(_BALANCE_SHEET_TYPES),
            Transaction.date < start_date,
            Transaction.date <= end_date,
        )
    )
    if exclude_adjusting:
        prior = prior.filter(Transaction.is_adjusting == False)  # noqa: E712
    prior_net = Decimal(
        prior.execution_options(**cash_basis.reading(basis)).scalar() or 0
    )
    if prior_net != 0:
        words = f"{terms_from_db(db)('Net Income')} (prior periods)"
        debit, credit = max(prior_net, Decimal(0)), max(-prior_net, Decimal(0))
        total_debit += debit
        total_credit += credit
        items.append(
            {
                "account_id": None,
                "account_number": "",
                "account_name": words,
                "account_path": words,
                "account_label": words,
                "account_type": AccountType.EQUITY.value,
                "total_debit": float(debit),
                "total_credit": float(credit),
                "net_balance": float(debit - credit),
            }
        )

    if hide_zero:
        items = _with_figures(items, ("total_debit", "total_credit"))

    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "exclude_adjusting": bool(exclude_adjusting),
        "basis": basis,
        "basis_note": cash_basis.receivable_note(db, end_date, basis),
        "hide_zero": bool(hide_zero),
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
    basis: str = "accrual",
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
    `basis` (#483) reads the ledger as accrual or cash.
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
        .execution_options(**cash_basis.reading(basis))
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
        "basis": basis,
        "basis_note": cash_basis.receivable_note(db, end_date, basis),
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
    basis: str = _BASIS_QUERY,
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
    from app.services.classes_service import (
        UNCATEGORIZED_NAME,
        class_attribution,
        find_uncategorized_class_id,
    )

    basis = cash_basis.resolve_basis(db, basis if isinstance(basis, str) else None)
    start_date, end_date = _period(db, start_date, end_date)
    if show not in ("all", "active"):
        raise HTTPException(status_code=400, detail="show must be all or active")
    chosen = _ids_param(class_ids, "class_ids")
    uncat_id = find_uncategorized_class_id(db)
    meta = {
        c.id: {"name": c.name, "inactive": bool(c.is_archived)}
        for c in db.query(TxnClass).all()
    }
    if uncat_id is None:
        # no default class yet: untagged lines are still the Uncategorized
        # column, without a row to save for it (#662)
        meta[None] = {"name": UNCATEGORIZED_NAME, "inactive": False}
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
        basis=basis,
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


@router.get("/profit-loss-by-job", dependencies=[Depends(refuse_unknown_filters)])
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
        Optional[int],
        Query(description="Only this customer's jobs", ge=BIGINT_MIN, le=BIGINT_MAX),
    ] = None,
    show: Annotated[
        str, Query(description="all (inactive jobs too) or active")
    ] = "all",
    include_empty: Annotated[
        bool, Query(description="A column for every job, activity or not")
    ] = False,
    basis: str = _BASIS_QUERY,
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

    basis = cash_basis.resolve_basis(db, basis if isinstance(basis, str) else None)
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
        basis=basis,
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


def _by_job_data(
    db, start_date, end_date, job_ids, customer_id, show, include_empty, basis=None
):
    data = profit_loss_by_job(
        start_date,
        end_date,
        db,
        job_ids=job_ids,
        customer_id=customer_id,
        show=show,
        include_empty=include_empty,
        basis=basis,
    )
    t = terms_from_db(db)
    title = f"{t('P&L')} by {t('Job')}"
    columns = [dict(c, name=c["job_name"]) for c in data["jobs"]]
    data["report_name"] = title
    data["dimension"] = t("Job")
    return data, title, columns, t


@router.get("/profit-loss-by-job/pdf", dependencies=[Depends(refuse_unknown_filters)])
def profit_loss_by_job_pdf(
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    db: Session = Depends(get_db),
    job_ids: Optional[list[str]] = Query(default=None),
    customer_id: Optional[int] = Query(default=None, ge=BIGINT_MIN, le=BIGINT_MAX),
    show: str = "all",
    include_empty: bool = False,
    basis: str = _BASIS_QUERY,
):
    data, title, columns, t = _by_job_data(
        db, start_date, end_date, job_ids, customer_id, show, include_empty, basis
    )
    return _pdf_response(
        _grid_sections(data, title, columns, t),
        db,
        f"{t.slug('P&L')}-by-{t.slug('Job')}_{data['start_date']}_{data['end_date']}.pdf",
        landscape=True,
    )


@router.get("/profit-loss-by-job/csv", dependencies=[Depends(refuse_unknown_filters)])
def profit_loss_by_job_csv_route(
    request: Request,
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    db: Session = Depends(get_db),
    job_ids: Optional[list[str]] = Query(default=None),
    customer_id: Optional[int] = Query(default=None, ge=BIGINT_MIN, le=BIGINT_MAX),
    show: str = "all",
    include_empty: bool = False,
    layout: str = Query(default="wide"),
    basis: str = _BASIS_QUERY,
):
    from app.services.ledger_exports import profit_loss_by_class_csv

    data, title, columns, t = _by_job_data(
        db, start_date, end_date, job_ids, customer_id, show, include_empty, basis
    )
    return _csv_download(
        profit_loss_by_class_csv(data, columns, _company_name(db), t, layout=layout),
        f"{t.slug('P&L')}-by-{t.slug('Job')}_{data['start_date']}_{data['end_date']}.csv",
        request,
    )


def _contracts_as_of(db: Session, rows: list[dict], end_date: date) -> None:
    """A contractor's Job Profitability reads each contract as it stood on the
    end date, as the WIP schedule does; other companies keep today's."""
    from app.models.jobs import Job
    from app.services.job_costing import _contract_float, _is_construction

    if not _is_construction(db):
        return
    for r in rows:
        job = db.get(Job, r["job_id"]) if r.get("job_id") else None
        if job is not None:
            r["contract_amount"] = _contract_float(db, job, end_date, True)


@router.get("/job-profitability", dependencies=[Depends(refuse_unknown_filters)])
def job_profitability_report(
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    customer_id: int = Query(default=None, ge=BIGINT_MIN, le=BIGINT_MAX),
    db: Session = Depends(get_db),
    basis: str = _BASIS_QUERY,
):
    """Income, costs and margin per job from posted lines.

    A line's job is its own job_id, else its transaction's; untagged
    activity is the "No job" row, so the totals equal the plain Profit &
    Loss for the same period (the same reconciliation promise as P&L by
    Class). `basis` (#483): accrual or cash, else the company's default.
    """
    from app.services.jobs_service import job_profitability

    basis = cash_basis.resolve_basis(db, basis if isinstance(basis, str) else None)
    if not start_date:
        start_date = fiscal_year_start(db, date.today())
    if not end_date:
        end_date = date.today()
    rows = job_profitability(
        db, start_date, end_date, customer_id=customer_id, basis=basis
    )
    _contracts_as_of(db, rows, end_date)
    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "basis": basis,
        "basis_note": cash_basis.receivable_note(db, end_date, basis),
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
    if data.get("basis") == "cash":
        period += " (cash basis)"
    if data.get("hide_zero"):
        period += " (zero balances hidden)"
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
        "note": data.get("basis_note"),
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

        def tree_row(r):
            """A family's rows (#416, #478): a parent's heading, its
            accounts indented, its direct postings, its total."""
            indent = r["depth"] + 1
            if r["kind"] == "heading":
                out = row(f"  {r['label']}", [""] * (len(cols) + 1), "group")
            else:
                out = row(
                    f"  {r['label']}",
                    [_money(r["amounts"][i]) if r["amounts"][i] else "" for i in idx]
                    + [_money(r["total"])],
                    "subtotal" if r["kind"] == "total" else None,
                )
            out["indent"] = indent
            return out

        tree = data.get("tree")
        rows = []
        for label, key, sub_label, sub_key, total_key in _grid_layout(data, t):
            if key:
                rows.append(row(label, [""] * (len(cols) + 1), "subtotal"))
                if tree is not None:
                    rows.extend(tree_row(r) for r in tree.get(key, []))
                else:
                    rows.extend(account_row(a) for a in data["accounts"][key])
            rows.append(sum_row(sub_label, sub_key, data[total_key], "subtotal"))
        rows.append(
            sum_row(
                t("Net Income"), "net_income", data["total_net_income"], "grand-total"
            )
        )
        period = f"{data['start_date']} — {data['end_date']}"
        if data.get("basis") == "cash":
            period += " (cash basis)"
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
                "note": data.get("basis_note"),
            }
        )
    return sections


def _pl_columns_title(data: dict, t) -> str:
    """ "Profit & Loss by Month", one class's with its name (#478)."""
    by = "Month" if data.get("columns_by") == "month" else "Quarter"
    cls = data.get("class_name")
    return f"{t('Profit & Loss')} by {by}" + (f" — {cls}" if cls else "")


def _bs_section(data: dict, t=None, show_changes: bool = True) -> dict:
    """Balance Sheet rows for the PDF. Assets and liabilities print under
    their groups (#390: Current / Fixed / Other Assets, Current / Long Term
    Liabilities), each with its subtotal, and inside a group (or the equity
    side) sub-accounts beneath their parents with a total per family
    (#565, the group's `tree`; the flat lines when a caller built none).
    With a prior-year comparison (`prior` on the data) the columns are
    Current | Prior Year, and $ Change | % Change unless show_changes is
    off."""
    t = t or Terms()
    prior = data.get("prior")
    columns = _compare_columns(prior, show_changes)
    blank = [""] * (len(columns) - 1)
    p = prior or {}
    groups = data.get("groups") or {}
    tree = data.get("tree") or {}

    def figures(cur, before):
        return _compare_figures(cur, before, prior, show_changes)

    def line(item, indent):
        return {
            "cells": [f"  {_row_words(item)}"]
            + figures(item["amount"], item.get("prior_amount", 0)),
            "indent": indent,
        }

    def tree_row(r, indent):
        """A printed row of a family tree (#416): an account, a parent's
        heading, its direct postings or its family's total, `indent` the
        level of the tree's top."""
        indent += r["depth"]
        if r["kind"] == "heading":
            return {
                "cells": [f"  {r['label']}"] + blank,
                "style": "group",
                "indent": indent,
            }
        row = {
            "cells": [f"  {r['label']}"]
            + figures(r["amount"], r.get("prior_amount", 0)),
            "indent": indent,
        }
        if r["kind"] == "total":
            row["style"] = "subtotal"
        return row

    def lines(items, rows, indent):
        if rows is not None:
            return [tree_row(r, indent) for r in rows]
        return [line(item, indent) for item in items]

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
                rows.extend(lines(g["lines"], g.get("tree"), 2))
                rows.append(
                    {
                        "cells": [f"Total {g['heading']}"]
                        + figures(data[g["total_key"]], p.get(g["total_key"], 0)),
                        "style": "subtotal",
                        "indent": 1,
                    }
                )
        else:
            rows.extend(lines(data[key], tree.get(key), 1))
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
    if data.get("basis") == "cash":
        period += " (cash basis)"
    if data.get("hide_zero"):
        period += " (zero balances hidden)"
    return {
        "title": t("Balance Sheet"),
        "period": period,
        "columns": columns,
        "rows": rows,
        "note": data.get("basis_note"),
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
        "note": data.get("basis_note"),
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
        "period": f"{data['start_date']} — {data['end_date']}"
        + (" (cash basis)" if data.get("basis") == "cash" else ""),
        "columns": ["Date", "Reference", "Description", "Debit", "Credit", "Balance"],
        "rows": rows,
        "note": data.get("basis_note"),
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


def _pl_filename(data: dict, t, ext: str, by: str | None = None) -> str:
    """profit-loss_2026-07-01_2026-07-31.pdf, with the class's slug after
    the report's when the P&L is one class's (#232), and -by-month or
    -by-quarter for the column layouts (#478)."""
    name = t.slug("Profit & Loss")
    if by:
        name += f"-by-{by}"
    if data.get("class_name"):
        name += "_" + (t.slug(data["class_name"]) or "class")
    return f"{name}_{data['start_date']}_{data['end_date']}.{ext}"


@router.get("/profit-loss/pdf")
def profit_loss_pdf(
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    class_id: Optional[int] = Query(default=None, ge=BIGINT_MIN, le=BIGINT_MAX),
    db: Session = Depends(get_db),
    sort: str = _SORT_QUERY,
    exclude_adjusting: bool = _EXCLUDE_ADJUSTING,
    compare: Optional[str] = Query(default=None),
    hide_changes: bool = Query(default=False),
    columns: Optional[str] = Query(default=None),
    basis: str = _BASIS_QUERY,
    hide_zero: bool = _HIDE_ZERO,
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
        columns=columns,
        basis=basis,
        hide_zero=hide_zero,
    )
    t = terms_from_db(db)
    if data.get("columns_by"):
        # by month or quarter (#478): the by-class grid's landscape pages
        return _pdf_response(
            _grid_sections(data, _pl_columns_title(data, t), data["columns"], t),
            db,
            _pl_filename(data, t, "pdf", data["columns_by"]),
            landscape=True,
        )
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
    basis: str = _BASIS_QUERY,
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
        basis=basis,
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
    basis: str = _BASIS_QUERY,
):
    from app.services.ledger_exports import profit_loss_by_class_csv

    data = profit_loss_by_class(
        start_date,
        end_date,
        db,
        class_ids=class_ids,
        show=show,
        include_empty=include_empty,
        basis=basis,
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
    basis: str = _BASIS_QUERY,
    hide_zero: bool = _HIDE_ZERO,
    as_of_date: date = _TB_AS_OF,
):
    data = trial_balance(
        start_date,
        end_date,
        db,
        exclude_adjusting=exclude_adjusting,
        basis=basis,
        hide_zero=hide_zero,
        as_of_date=as_of_date,
    )
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
    basis: str = _BASIS_QUERY,
    hide_zero: bool = _HIDE_ZERO,
    as_of_date: date = _TB_AS_OF,
):
    from app.services.ledger_exports import trial_balance_csv

    data = trial_balance(
        start_date,
        end_date,
        db,
        exclude_adjusting=exclude_adjusting,
        basis=basis,
        hide_zero=hide_zero,
        as_of_date=as_of_date,
    )
    return _csv_download(
        trial_balance_csv(data, _company_name(db)),
        f"trial-balance_{data['start_date']}_{data['end_date']}.csv",
        request,
    )


@router.get("/general-ledger/pdf")
def general_ledger_pdf(
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    account_id: int = Query(default=None, ge=BIGINT_MIN, le=BIGINT_MAX),
    class_id: int = Query(default=None, ge=BIGINT_MIN, le=BIGINT_MAX),
    db: Session = Depends(get_db),
    basis: str = _BASIS_QUERY,
):
    data = general_ledger(
        start_date, end_date, account_id, db, class_id=class_id, basis=basis
    )
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
    account_id: int = Query(default=None, ge=BIGINT_MIN, le=BIGINT_MAX),
    class_id: int = Query(default=None, ge=BIGINT_MIN, le=BIGINT_MAX),
    db: Session = Depends(get_db),
    basis: str = _BASIS_QUERY,
):
    from app.services.ledger_exports import general_ledger_csv

    data = general_ledger(
        start_date, end_date, account_id, db, class_id=class_id, basis=basis
    )
    return _csv_download(
        general_ledger_csv(data, _company_name(db), terms_from_db(db)),
        f"general-ledger_{data['start_date']}_{data['end_date']}.csv",
        request,
    )


def _account_transactions_section(data: dict, t, numbered: bool = True) -> dict:
    """The drill-down's rows for the PDF (#333). A P&L total's or a
    family's (#479) names each line's account where the payee is, the
    payee after it, and its Amount (signed toward the total) and running
    total in place of the debit, credit and balance."""
    if data.get("set"):
        return _account_set_section(data, t)
    acct = data.get("account") or {}
    head = account_label(acct.get("number"), acct.get("name"), numbered, " ")
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
    if data.get("basis") == "cash":
        period += " (cash basis)"
    return {
        "title": f"Account detail — {head}",
        "period": period,
        "columns": ["Date", "Payee", "Description", "Debit", "Credit", "Running"],
        "rows": rows,
        "note": data.get("basis_note"),
    }


def _account_set_section(data: dict, t) -> dict:
    rows = []
    for e in data.get("entries", []):
        rows.append(
            {
                "cells": [
                    e.get("date", ""),
                    (e.get("account_label") or "")[:40],
                    (e.get("payee") or e.get("name") or "")[:30],
                    ((e.get("memo") or "") + (" (void)" if e.get("voided") else ""))[
                        :50
                    ],
                    _money(e.get("amount", 0)),
                    _money(e.get("running_balance", 0)),
                ]
            }
        )
    rows.append(
        {
            "cells": ["", "", "", data["set"]["label"], _money(data["period_net"]), ""],
            "style": "subtotal",
        }
    )
    period = f"{data['start_date']} — {data['end_date']}"
    if data.get("class_name"):
        period += f" · {t('Class')}: {data['class_name']} only"
    if data.get("job_name"):
        period += f" · {t('Job')}: {data['job_name']}"
    if data.get("exclude_adjusting"):
        period += " · adjusting entries left out"
    if data.get("basis") == "cash":
        period += " (cash basis)"
    return {
        "title": f"Account detail — {data['set']['label']}",
        "period": period,
        "columns": ["Date", "Account", "Name", "Memo", "Amount", "Running"],
        "text_columns": 4,
        "rows": rows,
        "note": data.get("basis_note"),
    }


def _drill_slug(data: dict, t, numbered: bool = True) -> str:
    """The file name's words: the account's number and name, or the set's
    label ("Total Income", "Total 7000 - Travel", #479)."""
    if data.get("set"):
        return t.slug(data["set"]["label"]) or "accounts"
    acct = data.get("account") or {}
    return t.slug(
        account_label(acct.get("number"), acct.get("name") or "account", numbered, " ")
    )


def _drill_data(
    db,
    account_id,
    start_date,
    end_date,
    class_id,
    job_id,
    section,
    family,
    exclude_adjusting,
    basis=None,
):
    return account_transactions(
        account_id,
        start_date,
        end_date,
        db,
        class_id=class_id,
        job_id=job_id,
        section=section,
        family=family,
        exclude_adjusting=exclude_adjusting,
        basis=basis,
    )


@router.get("/account-transactions/csv")
def account_transactions_csv_route(
    request: Request,
    account_id: int = Query(default=None, ge=BIGINT_MIN, le=BIGINT_MAX),
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    class_id: Optional[int] = Query(default=None, ge=BIGINT_MIN, le=BIGINT_MAX),
    job_id: Optional[int] = Query(default=None, ge=BIGINT_MIN, le=BIGINT_MAX),
    db: Session = Depends(get_db),
    section: Optional[str] = Query(default=None),
    family: Optional[int] = Query(default=None, ge=BIGINT_MIN, le=BIGINT_MAX),
    exclude_adjusting: bool = _EXCLUDE_ADJUSTING,
    basis: str = _BASIS_QUERY,
):
    """The drill-down as a CSV, the figures on the screen (#333); a P&L
    total's or a family's too, with each line's account (#479)."""
    from app.services.ledger_exports import account_transactions_csv

    data = _drill_data(
        db,
        account_id,
        start_date,
        end_date,
        class_id,
        job_id,
        section,
        family,
        exclude_adjusting,
        basis,
    )
    t = terms_from_db(db)
    return _csv_download(
        account_transactions_csv(data, _company_name(db), t),
        f"AccountDetail_{_drill_slug(data, t, use_account_numbers(db))}_{data['start_date']}_{data['end_date']}.csv",
        request,
    )


@router.get("/account-transactions/pdf")
def account_transactions_pdf_route(
    account_id: int = Query(default=None, ge=BIGINT_MIN, le=BIGINT_MAX),
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    class_id: Optional[int] = Query(default=None, ge=BIGINT_MIN, le=BIGINT_MAX),
    job_id: Optional[int] = Query(default=None, ge=BIGINT_MIN, le=BIGINT_MAX),
    db: Session = Depends(get_db),
    section: Optional[str] = Query(default=None),
    family: Optional[int] = Query(default=None, ge=BIGINT_MIN, le=BIGINT_MAX),
    exclude_adjusting: bool = _EXCLUDE_ADJUSTING,
    basis: str = _BASIS_QUERY,
):
    """The drill-down as a PDF (#333); a P&L total's or a family's too
    (#479)."""
    data = _drill_data(
        db,
        account_id,
        start_date,
        end_date,
        class_id,
        job_id,
        section,
        family,
        exclude_adjusting,
        basis,
    )
    t = terms_from_db(db)
    numbered = use_account_numbers(db)
    return _pdf_response(
        [_account_transactions_section(data, t, numbered)],
        db,
        f"AccountDetail_{_drill_slug(data, t, numbered)}_{data['start_date']}_{data['end_date']}.pdf",
    )


@router.get("/profit-loss/csv")
def profit_loss_csv_route(
    request: Request,
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    class_id: Optional[int] = Query(default=None, ge=BIGINT_MIN, le=BIGINT_MAX),
    db: Session = Depends(get_db),
    sort: str = _SORT_QUERY,
    exclude_adjusting: bool = _EXCLUDE_ADJUSTING,
    compare: Optional[str] = Query(default=None),
    hide_changes: bool = Query(default=False),
    columns: Optional[str] = Query(default=None),
    layout: str = Query(
        default="wide",
        description="with columns: wide, the grid; long, one row per account and column",
    ),
    basis: str = _BASIS_QUERY,
    hide_zero: bool = _HIDE_ZERO,
):
    from app.services.ledger_exports import profit_loss_by_class_csv, profit_loss_csv

    data = profit_loss(
        start_date,
        end_date,
        db,
        class_id=class_id,
        sort=sort,
        exclude_adjusting=exclude_adjusting,
        compare=compare,
        columns=columns,
        basis=basis,
        hide_zero=hide_zero,
    )
    t = terms_from_db(db)
    if data.get("columns_by"):
        # by month or quarter (#478): the by-class grid's spreadsheet
        data["report_name"] = _pl_columns_title(data, t)
        data["dimension"] = "Month" if data["columns_by"] == "month" else "Quarter"
        return _csv_download(
            profit_loss_by_class_csv(
                data, data["columns"], _company_name(db), t, layout=layout
            ),
            _pl_filename(data, t, "csv", data["columns_by"]),
            request,
        )
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
    basis: str = _BASIS_QUERY,
    hide_zero: bool = _HIDE_ZERO,
):
    from app.services.ledger_exports import balance_sheet_csv

    data = balance_sheet(
        as_of_date, db, sort=sort, compare=compare, basis=basis, hide_zero=hide_zero
    )
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
    basis: str = _BASIS_QUERY,
    hide_zero: bool = _HIDE_ZERO,
):
    data = balance_sheet(
        as_of_date, db, sort=sort, compare=compare, basis=basis, hide_zero=hide_zero
    )
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
    basis: str = _BASIS_QUERY,
):
    """The statements pack: P&L, Balance Sheet, and Trial Balance in one
    audit-ready document (each statement on its own page). It takes the
    same `basis` the screens do, so a P&L switched to cash saves a cash pack."""
    pl = profit_loss(start_date, end_date, db, basis=basis)
    bs = balance_sheet(date.fromisoformat(pl["end_date"]), db, basis=basis)
    tb = trial_balance(
        date.fromisoformat(pl["start_date"]),
        date.fromisoformat(pl["end_date"]),
        db,
        basis=pl["basis"],
    )
    t = terms_from_db(db)
    if t.is_nonprofit:
        from app.routes.reports.nonprofit import nonprofit_statement_sections

        sections = nonprofit_statement_sections(
            db,
            date.fromisoformat(pl["start_date"]),
            date.fromisoformat(pl["end_date"]),
            pl["basis"],
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


def _adjusting_section(data: dict, numbered: bool = True) -> dict:
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
            name = account_label(
                ln["account_number"], ln["account_name"], numbered, " "
            )
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
        [_adjusting_section(data, use_account_numbers(db))],
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
