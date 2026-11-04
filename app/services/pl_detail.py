"""Profit & Loss Detail (#477): every posted line under each income, cost
of goods and expense account for a period, in the P&L's order.

The lines are the drill-down's (bank_register.account_register): the same
scope for a class and a job (bank_register.line_scope), the same payee,
reference, void mark and link to the document; one query across the P&L's
accounts instead of one per account. Each account carries its own running
balance from zero (a P&L account's balance is the period's) and its total;
the totals add up to the Profit & Loss for the same dates and filters, which
tests/test_pl_detail.py holds.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.accounts import Account, AccountType
from app.models.transactions import Transaction, TransactionLine
from app.services.edit_targets import edit_target
from app.services import cash_basis
from app.services.bank_register import (
    DEBIT_NORMAL,
    line_scope,
    payees_for,
    references_for,
    source_link,
    source_word,
    voided_transaction_ids,
)
from app.services.entry_text import header_text

PL_TYPES = (AccountType.INCOME, AccountType.COGS, AccountType.EXPENSE)
PL_SECTIONS = ("income", "cogs", "expenses", "other_income", "other_expense")
SPLIT_WORD = "-SPLIT-"  # QuickBooks' word for a transaction across several accounts
ZERO = Decimal("0")
_CHUNK = 500


def _splits(db: Session, txn_ids, chart: dict, basis: str = "accrual"):
    """{(transaction id, account id): the Split column}: the one other
    account on the transaction, by its number and path, or -SPLIT- when
    there are several, as QuickBooks writes it."""
    from app.routes.reports.financial import _account_words

    ids = sorted(txn_ids)
    on: dict[int, set] = defaultdict(set)
    for i in range(0, len(ids), _CHUNK):
        for txn_id, acct_id in (
            db.query(TransactionLine.transaction_id, TransactionLine.account_id)
            .filter(TransactionLine.transaction_id.in_(ids[i : i + _CHUNK]))
            .execution_options(**cash_basis.reading(basis))
        ):
            on[txn_id].add(acct_id)

    def split(txn_id, acct_id):
        others = on.get(txn_id, set()) - {acct_id}
        if not others:
            return ""
        if len(others) > 1:
            return SPLIT_WORD
        other = next(iter(others))
        return _account_words(chart, other)[1] or ""

    return split


def pl_detail_lines(
    db: Session,
    start: date,
    end: date,
    *,
    class_id: int | None = None,
    job_id: int | None = None,
    exclude_adjusting: bool = False,
    account_ids: set | None = None,
    chart: dict | None = None,
    words=None,
    basis: str = "accrual",
) -> dict:
    """{account id: block} for every P&L account with a line in the period
    (or only those in `account_ids`), each block {account_id, number,
    name, path, label, section, entries, amount, debit, credit}. An entry
    is {line_id, transaction_id, date, type, source_type, num, name, memo,
    split, debit, credit, amount, balance, source_link, voided}; `amount`
    is in the account's natural sign, as the P&L adds it, and `balance` the
    running total from the period's start. `basis` (#483) reads the ledger
    as accrual or cash; on cash an entry is the payment's recognition line."""
    from app.routes.reports.financial import (
        _account_words,
        _chart_index,
        _pl_section_of,
    )

    chart = chart if chart is not None else _chart_index(db)
    q = (
        db.query(TransactionLine, Transaction, Account.account_type)
        .join(Transaction, TransactionLine.transaction_id == Transaction.id)
        .join(Account, TransactionLine.account_id == Account.id)
        .filter(
            Account.account_type.in_(PL_TYPES),
            Transaction.date >= start,
            Transaction.date <= end,
        )
    )
    scope = line_scope(db, class_id, job_id)
    if scope is not None:
        q = q.filter(scope)
    if exclude_adjusting:
        q = q.filter(Transaction.is_adjusting == False)  # noqa: E712
    if account_ids is not None:
        if not account_ids:
            return {}
        q = q.filter(TransactionLine.account_id.in_(sorted(account_ids)))
    rows = (
        q.order_by(Transaction.date, Transaction.id, TransactionLine.id)
        .execution_options(**cash_basis.reading(basis))
        .all()
    )

    txns = {txn.id: txn for _, txn, _ in rows}
    voided = voided_transaction_ids(db, txns.keys())
    payees = payees_for(db, txns.values())
    refs = references_for(db, list(txns.values()))
    split = _splits(db, txns.keys(), chart, basis)

    blocks: dict[int, dict] = {}
    for tl, txn, acct_type in rows:
        aid = tl.account_id
        block = blocks.get(aid)
        if block is None:
            number, name = chart[aid][0], chart[aid][1]
            path, label = _account_words(chart, aid)
            block = blocks[aid] = {
                "account_id": aid,
                "account_number": number,
                "account_name": name,
                "account_path": path or name,
                "account_label": label or name,
                "section": _pl_section_of(chart[aid][3], chart[aid][4]),
                "entries": [],
                "_amount": ZERO,
                "_debit": ZERO,
                "_credit": ZERO,
            }
        dr, cr = tl.debit or ZERO, tl.credit or ZERO
        natural = (dr - cr) if acct_type in DEBIT_NORMAL else (cr - dr)
        block["_amount"] += natural
        block["_debit"] += dr
        block["_credit"] += cr
        link = source_link(txn) or f"/#/journal/{txn.id}"
        block["entries"].append(
            {
                "line_id": tl.id,
                "transaction_id": txn.id,
                "date": txn.date.isoformat(),
                "type": source_word(txn.source_type, words, txn.source_kind),
                "source_type": txn.source_type or "journal",
                "num": refs.get(txn.id, ""),
                "name": payees.get(txn.id, ""),
                "memo": tl.description or header_text(txn, tl.description),
                "split": split(txn.id, aid),
                "debit": float(dr),
                "credit": float(cr),
                "amount": float(natural),
                "balance": float(block["_amount"]),
                "source_link": link,
                "voided": txn.id in voided
                or bool((txn.source_type or "").endswith("_void")),
                # what the line's Edit opens (#689); None: Open only
                "edit": edit_target(txn, txn.id in voided),
            }
        )
    for block in blocks.values():
        block["amount"] = float(block.pop("_amount"))
        block["debit"] = float(block.pop("_debit"))
        block["credit"] = float(block.pop("_credit"))
    return blocks


# The P&L's totals a drill-down can open (#479): each a set of sections and
# the sign each section's lines carry toward the total, so the lines add up
# to the figure on the statement (Gross Profit is income less cost of goods).
SET_SECTIONS = {
    "income": {"income": 1},
    "cogs": {"cogs": 1},
    "expenses": {"expenses": 1},
    "other_income": {"other_income": 1},
    "other_expense": {"other_expense": 1},
    "gross_profit": {"income": 1, "cogs": -1},
    "net_operating_income": {"income": 1, "cogs": -1, "expenses": -1},
    "net_other_income": {"other_income": 1, "other_expense": -1},
    "net_income": {
        "income": 1,
        "cogs": -1,
        "expenses": -1,
        "other_income": 1,
        "other_expense": -1,
    },
}
# the statement's words for each total, and the P&L key that holds it
SET_TOTALS = {
    "income": ("Total Income", "total_operating_income"),
    "cogs": ("Total Cost of Goods Sold", "total_cogs"),
    "expenses": ("Total Expenses", "total_operating_expenses"),
    "other_income": ("Total Other Income", "total_other_income"),
    "other_expense": ("Total Other Expense", "total_other_expense"),
    "gross_profit": ("Gross Profit", "operating_gross_profit"),
    "net_operating_income": ("Net Operating Income", "net_operating_income"),
    "net_other_income": ("Net Other Income", "net_other_income"),
    "net_income": ("Net Income", "net_income"),
}


def family_ids(chart: dict, parent_id: int) -> set:
    """The parent and every account beneath it that prints in its family on
    the P&L: a sub-account of the same section, walking down (a child of
    another type or detail type prints elsewhere and is not counted, as
    statement_layout.family_rows leaves it out). A parent loop stops."""
    from app.routes.reports.financial import _pl_section_of

    if parent_id not in chart:
        return set()
    section = _pl_section_of(chart[parent_id][3], chart[parent_id][4])
    kids: dict = defaultdict(list)
    for aid, row in chart.items():
        if row[2] is not None:
            kids[row[2]].append(aid)
    out, todo = set(), [parent_id]
    while todo:
        node = todo.pop()
        if node in out:
            continue
        out.add(node)
        for kid in kids.get(node, ()):
            if _pl_section_of(chart[kid][3], chart[kid][4]) == section:
                todo.append(kid)
    return out


def account_set_lines(
    db: Session,
    start: date,
    end: date,
    *,
    section: str | None = None,
    family: int | None = None,
    class_id: int | None = None,
    job_id: int | None = None,
    exclude_adjusting: bool = False,
    words=None,
    basis: str = "accrual",
) -> dict:
    """The drill-down for a set of accounts (#479): a P&L total (`section`,
    a SET_SECTIONS key) or a sub-account family (`family`, its parent's
    id). Every line of the set in date order, each with its account, its
    `amount` signed toward the total and a running total from zero; the
    last running total is the statement's figure."""
    from app.routes.reports.financial import _chart_index

    chart = _chart_index(db)
    if family is not None:
        ids = family_ids(chart, family)
        signs = None
    else:
        signs = SET_SECTIONS[section]
        ids = None
    blocks = pl_detail_lines(
        db,
        start,
        end,
        class_id=class_id,
        job_id=job_id,
        exclude_adjusting=exclude_adjusting,
        account_ids=ids,
        chart=chart,
        words=words,
        basis=basis,
    )
    lines = []
    for block in blocks.values():
        sign = 1 if signs is None else signs.get(block["section"])
        if sign is None:
            continue
        for e in block["entries"]:
            lines.append(
                {
                    **e,
                    "account_id": block["account_id"],
                    "account_label": block["account_label"],
                    "amount": e["amount"] * sign,
                    "_sign": sign,
                }
            )
    lines.sort(key=lambda e: (e["date"], e["transaction_id"], e["line_id"]))
    running = ZERO
    debit = credit = ZERO
    for e in lines:
        running += Decimal(str(e["amount"]))
        debit += Decimal(str(e["debit"]))
        credit += Decimal(str(e["credit"]))
        e["running_balance"] = float(running)
        e["balance"] = float(running)
        e.pop("_sign")
    return {
        "account_ids": sorted({e["account_id"] for e in lines} | (ids or set())),
        "entries": lines,
        "period_debit": float(debit),
        "period_credit": float(credit),
        "period_net": float(running),
    }
