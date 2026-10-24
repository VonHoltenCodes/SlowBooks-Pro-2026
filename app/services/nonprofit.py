"""Nonprofit mode — posting logic for the fund-accounting documents.

Release from restriction
    A restricted fund (class) spent money for its purpose; that much moves
    from Net Assets With Donor Restrictions to Net Assets Without. Posted
    as DR 3400 / CR 3300, both lines tagged to the fund, so the fund
    balance report can show what came in, what was released and what is
    still held. The suggested amount is the fund's expenses in the period
    less what was already released for it.

Allocation rules and the functional allocation
    Rent, utilities, the office manager's payroll: one shared cost that
    belongs partly to programs, partly to management, partly to
    fundraising. A rule says how to split it (by percent, square feet, or
    hours on grants). At entry time a Split button expands one line into
    the rule's shares; at period end a functional allocation re-runs the
    rule on everything still unassigned (function IS NULL) on the source
    account and reclasses it — same natural account, DR the targets, CR
    the source — so the P&L is untouched, the Statement of Functional
    Expenses has its columns, and running the same period twice finds an
    empty pool.

Both documents void with a reversing entry like every other posting.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from app.models.accounts import Account, AccountType
from app.models.classes import TxnClass, is_restricted
from app.models.nonprofit import (
    AllocationRule,
    FunctionalAllocation,
    FunctionalAllocationLine,
    RestrictionRelease,
)
from app.models.time_entries import TimeEntry
from app.models.transactions import Transaction, TransactionLine
from app.services.accounting import (
    _q,
    create_journal_entry,
    get_net_assets_with_restriction_id,
    get_net_assets_without_restriction_id,
    reversing_lines,
)
from app.services.classes_service import class_attribution, uncategorized_class_id
from app.services.numbering import next_document_number

_PL_EXPENSE_TYPES = (AccountType.EXPENSE, AccountType.COGS)


# ── numbering ────────────────────────────────────────────────────────────


def next_release_number(db: Session) -> str:
    return next_document_number(
        db, RestrictionRelease.number, prefix="RL-", first=1, pad=6
    )


def next_allocation_number(db: Session) -> str:
    return next_document_number(
        db, FunctionalAllocation.number, prefix="FA-", first=1, pad=6
    )


# ── release from restriction ─────────────────────────────────────────────


def default_period(db: Session, end: Optional[date] = None) -> tuple[date, date]:
    """Fiscal year to date, ending on `end` (today by default). Follows the
    first-month-of-fiscal-year setting (#444)."""
    from app.services.fiscal_year import fiscal_year_start

    end = end or date.today()
    return fiscal_year_start(db, end), end


def class_expenses(db: Session, class_id: int, start: date, end: date) -> Decimal:
    """Expenses + COGS attributed to the fund in the period (line class first,
    header class second) — what the fund spent, the evidence for a release."""
    uncat = uncategorized_class_id(db)
    total = (
        db.query(
            sqlfunc.coalesce(
                sqlfunc.sum(TransactionLine.debit - TransactionLine.credit), 0
            )
        )
        .select_from(Transaction)
        .join(TransactionLine, TransactionLine.transaction_id == Transaction.id)
        .join(Account, TransactionLine.account_id == Account.id)
        .filter(
            Account.account_type.in_(_PL_EXPENSE_TYPES),
            class_attribution(uncat) == class_id,
            Transaction.date >= start,
            Transaction.date <= end,
        )
        .scalar()
    )
    return _q(Decimal(str(total or 0)))


def class_releases(db: Session, class_id: int, start: date, end: date) -> Decimal:
    """What has already been released for the fund in the period: net
    debits on Net Assets With Donor Restrictions tagged to it (a voided
    release credits it back, so voids fall out naturally)."""
    with_id = get_net_assets_with_restriction_id(db)
    uncat = uncategorized_class_id(db)
    total = (
        db.query(
            sqlfunc.coalesce(
                sqlfunc.sum(TransactionLine.debit - TransactionLine.credit), 0
            )
        )
        .select_from(Transaction)
        .join(TransactionLine, TransactionLine.transaction_id == Transaction.id)
        .filter(
            TransactionLine.account_id == with_id,
            class_attribution(uncat) == class_id,
            Transaction.date >= start,
            Transaction.date <= end,
        )
        .scalar()
    )
    return _q(Decimal(str(total or 0)))


def suggested_release(
    db: Session, class_id: int, start: Optional[date], end: Optional[date]
) -> dict:
    fund = db.get(TxnClass, class_id)
    if fund is None:
        raise ValueError("Fund not found")
    if start is None or end is None:
        d_start, d_end = default_period(db, end)
        start = start or d_start
        end = end or d_end
    expenses = class_expenses(db, class_id, start, end)
    released = class_releases(db, class_id, start, end)
    return {
        "class_id": class_id,
        "class_name": fund.name,
        "period_start": start,
        "period_end": end,
        "expenses": expenses,
        "released": released,
        "suggested": max(expenses - released, Decimal("0")),
    }


def post_release(
    db: Session,
    *,
    txn_date: date,
    class_id: int,
    amount: Optional[Decimal],
    period_start: Optional[date],
    period_end: Optional[date],
    memo: Optional[str],
) -> RestrictionRelease:
    fund = db.get(TxnClass, class_id)
    if fund is None:
        raise ValueError("Fund not found")
    if not is_restricted(fund.restriction):
        raise ValueError(
            f"'{fund.name}' carries no donor restriction — nothing to release"
        )
    suggestion = suggested_release(db, class_id, period_start, period_end)
    if amount is None:
        amount = suggestion["suggested"]
    amount = _q(Decimal(str(amount)))
    if amount <= 0:
        raise ValueError("Nothing to release: the fund has no unreleased spending")

    with_id = get_net_assets_with_restriction_id(db)
    without_id = get_net_assets_without_restriction_id(db)
    rel = RestrictionRelease(
        number=next_release_number(db),
        date=txn_date,
        class_id=class_id,
        amount=amount,
        period_start=suggestion["period_start"],
        period_end=suggestion["period_end"],
        memo=memo,
        status="posted",
    )
    db.add(rel)
    db.flush()
    desc = f"Release from restriction {rel.number} - {fund.name}"
    txn = create_journal_entry(
        db,
        txn_date,
        desc,
        [
            {
                "account_id": with_id,
                "debit": amount,
                "credit": Decimal("0"),
                "description": desc,
                "class_id": class_id,
                "function": None,
            },
            {
                "account_id": without_id,
                "debit": Decimal("0"),
                "credit": amount,
                "description": desc,
                "class_id": class_id,
                "function": None,
            },
        ],
        source_type="restriction_release",
        source_id=rel.id,
        reference=rel.number,
        class_id=class_id,
    )
    rel.transaction_id = txn.id
    return rel


def void_release(db: Session, rel: RestrictionRelease) -> None:
    if rel.status == "void":
        raise ValueError("Release is already void")
    txn = rel.transaction
    if txn is not None:
        create_journal_entry(
            db,
            rel.date,
            f"VOID {txn.description or rel.number}",
            reversing_lines(txn.lines),
            source_type="restriction_release_void",
            source_id=rel.id,
            reference=rel.number,
            class_id=rel.class_id,
        )
    rel.status = "void"


# ── allocation rules ─────────────────────────────────────────────────────


def _spread(total: Decimal, weights: dict) -> dict:
    """Split `total` over weights (key → Decimal), cents-exact: the largest
    weight absorbs the rounding remainder (same rule as job allocations)."""
    denom = sum(weights.values(), Decimal("0"))
    if denom <= 0 or total == 0:
        return {}
    out = {k: _q(total * w / denom) for k, w in weights.items()}
    diff = _q(total - sum(out.values(), Decimal("0")))
    if diff:
        top = max(weights, key=lambda k: weights[k])
        out[top] = _q(out[top] + diff)
    return out


def allocation_weights(
    db: Session, rule: AllocationRule, start: Optional[date], end: Optional[date]
) -> dict[int, Decimal]:
    """Weight per target id. Percent and square feet are the stored
    weights; hours are the time entries on each target's job in the
    period."""
    if rule.basis != "hours":
        return {
            t.id: Decimal(str(t.weight))
            for t in rule.targets
            if Decimal(str(t.weight)) > 0
        }
    out: dict[int, Decimal] = {}
    for t in rule.targets:
        if t.job_id is None:
            continue
        q = db.query(
            sqlfunc.coalesce(
                sqlfunc.sum(
                    TimeEntry.hours_regular
                    + TimeEntry.hours_overtime
                    + TimeEntry.hours_doubletime
                ),
                0,
            )
        ).filter(TimeEntry.job_id == t.job_id)
        if start:
            q = q.filter(TimeEntry.date >= start)
        if end:
            q = q.filter(TimeEntry.date <= end)
        hours = Decimal(str(q.scalar() or 0))
        if hours > 0:
            out[t.id] = hours
    return out


def split_amount(
    db: Session,
    rule: AllocationRule,
    amount: Decimal,
    start: Optional[date],
    end: Optional[date],
    pool_class_id: Optional[int] = None,
) -> list[dict]:
    """The rule applied to one amount: [{class_id, class_name, function,
    weight, amount}], cents-exact. A target with no fund of its own lands
    in the rule's source fund, else the fund the money came from."""
    amount = _q(Decimal(str(amount)))
    weights = allocation_weights(db, rule, start, end)
    if not weights:
        raise ValueError(
            "Nothing to split on: the rule's targets carry no weight for that period"
        )
    shares = _spread(amount, weights)
    by_id = {t.id: t for t in rule.targets}
    names = {c.id: c.name for c in db.query(TxnClass.id, TxnClass.name).all()}
    lines = []
    for tid, share in shares.items():
        t = by_id[tid]
        class_id = t.class_id or rule.source_class_id or pool_class_id
        lines.append(
            {
                "class_id": class_id,
                "class_name": names.get(class_id),
                "function": t.function,
                "weight": Decimal(str(weights[tid])),
                "amount": share,
            }
        )
    return lines


def allocation_pool(
    db: Session, rule: AllocationRule, start: date, end: date
) -> list[dict]:
    """What the rule would move: unassigned (function IS NULL) expense on
    the source account / fund in the period, grouped by account and by
    the fund it sits in. Empty after a run for the same period — the
    built-in guard against allocating twice."""
    uncat = uncategorized_class_id(db)
    cls = class_attribution(uncat).label("cls")
    q = (
        db.query(
            Account.id,
            Account.name,
            Account.account_number,
            cls,
            sqlfunc.coalesce(
                sqlfunc.sum(TransactionLine.debit - TransactionLine.credit), 0
            ),
        )
        .select_from(Transaction)
        .join(TransactionLine, TransactionLine.transaction_id == Transaction.id)
        .join(Account, TransactionLine.account_id == Account.id)
        .filter(
            Account.account_type.in_(_PL_EXPENSE_TYPES),
            TransactionLine.function.is_(None),
            Transaction.date >= start,
            Transaction.date <= end,
        )
    )
    if rule.source_account_id:
        q = q.filter(TransactionLine.account_id == rule.source_account_id)
    if rule.source_class_id:
        q = q.filter(class_attribution(uncat) == rule.source_class_id)
    rows = []
    for acct_id, name, number, cls_id, amt in q.group_by(
        Account.id, Account.name, Account.account_number, cls
    ).all():
        amt = _q(Decimal(str(amt or 0)))
        if amt == 0:
            continue
        rows.append(
            {
                "account_id": acct_id,
                "account_name": name,
                "account_number": number,
                "class_id": cls_id,
                "amount": amt,
            }
        )
    return rows


def post_functional_allocation(
    db: Session,
    *,
    rule: AllocationRule,
    txn_date: date,
    start: date,
    end: date,
    memo: Optional[str],
) -> FunctionalAllocation:
    pool = allocation_pool(db, rule, start, end)
    if not pool:
        raise ValueError(
            "Nothing to allocate: no unassigned expense on the rule's source for that period"
        )
    fa = FunctionalAllocation(
        number=next_allocation_number(db),
        date=txn_date,
        rule_id=rule.id,
        period_start=start,
        period_end=end,
        memo=memo or f"{rule.name} — {start} to {end}",
        status="posted",
    )
    db.add(fa)
    db.flush()

    je_lines: list[dict] = []
    order = 0
    total = Decimal("0")
    for row in pool:
        amt = row["amount"]
        total += amt
        shares = split_amount(db, rule, amt, start, end, pool_class_id=row["class_id"])
        for sh in shares:
            desc = f"{rule.name}: {sh['function'] or sh['class_name'] or 'share'}"
            fa.lines.append(
                FunctionalAllocationLine(
                    allocation_id=fa.id,
                    account_id=row["account_id"],
                    class_id=sh["class_id"],
                    function=sh["function"],
                    weight=sh["weight"],
                    amount=sh["amount"],
                    description=desc,
                    line_order=order,
                )
            )
            order += 1
            line = {
                "account_id": row["account_id"],
                "debit": sh["amount"],
                "credit": Decimal("0"),
                "description": desc,
                "class_id": sh["class_id"],
            }
            # A target that names a function pins it; a fund-only target
            # takes the fund's default (the normal posting rule).
            if sh["function"]:
                line["function"] = sh["function"]
            je_lines.append(line)
        # The credit takes the cost off the unassigned pool of the fund it
        # came from — explicitly unassigned so it never re-enters the pool
        # and never lands in a function column.
        je_lines.append(
            {
                "account_id": row["account_id"],
                "debit": Decimal("0"),
                "credit": amt,
                "description": f"{rule.name}: allocated",
                "class_id": row["class_id"],
                "function": None,
            }
        )
    txn = create_journal_entry(
        db,
        txn_date,
        f"Functional allocation {fa.number} - {rule.name}",
        je_lines,
        source_type="functional_allocation",
        source_id=fa.id,
        reference=fa.number,
    )
    fa.transaction_id = txn.id
    fa.total = _q(total)
    return fa


def void_functional_allocation(db: Session, fa: FunctionalAllocation) -> None:
    if fa.status == "void":
        raise ValueError("Allocation is already void")
    txn = fa.transaction
    if txn is not None:
        create_journal_entry(
            db,
            fa.date,
            f"VOID {txn.description or fa.number}",
            reversing_lines(txn.lines),
            source_type="functional_allocation_void",
            source_id=fa.id,
            reference=fa.number,
        )
    fa.status = "void"
