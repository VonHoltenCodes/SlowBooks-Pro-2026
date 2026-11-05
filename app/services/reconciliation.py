"""Reconciliation over the ledger's lines (issue #114): the statement's
ending balance against the bank account's cleared lines, with the prior
completed statement as the beginning balance. A matched statement line
arrives already cleared; a completed reconciliation stamps its lines so a
void cannot undo a closed month.
"""

from datetime import date, datetime
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.accounts import Account
from app.models.banking import BankTransaction, Reconciliation, ReconciliationStatus
from app.models.transactions import Transaction, TransactionLine
from app.services.account_label import account_label
from app.services.bank_posting import require_bank_account
from app.services.bank_register import (
    balance_as_of,
    date_text,
    is_debit_normal,
    money_text,
    payees_for,
    references_for,
    source_link,
)
from app.services.entry_text import header_text

TOLERANCE = Decimal("0.005")


def start(
    db: Session, account_id: int, statement_date: date, statement_balance: Decimal
) -> Reconciliation:
    acct = require_bank_account(db, account_id)
    open_one = (
        db.query(Reconciliation)
        .filter(
            Reconciliation.account_id == acct.id,
            Reconciliation.status == ReconciliationStatus.IN_PROGRESS,
        )
        .first()
    )
    if open_one:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "A reconciliation is already in progress for this account",
                "existing_id": open_one.id,
            },
        )
    last = _require_after_last(db, acct, statement_date)
    recon = Reconciliation(
        account_id=acct.id,
        statement_date=statement_date,
        max_statement_date=statement_date,
        statement_balance=Decimal(str(statement_balance)),
        beginning_balance=(
            Decimal(str(last.statement_balance)) if last else Decimal("0")
        ),
        status=ReconciliationStatus.IN_PROGRESS,
    )
    db.add(recon)
    db.flush()
    return recon


def _last_completed(db: Session, account_id: int, exclude_id=None):
    q = db.query(Reconciliation).filter(
        Reconciliation.account_id == account_id,
        Reconciliation.status == ReconciliationStatus.COMPLETED,
    )
    if exclude_id is not None:
        q = q.filter(Reconciliation.id != exclude_id)
    return q.order_by(
        Reconciliation.statement_date.desc(), Reconciliation.id.desc()
    ).first()


def _require_after_last(
    db: Session, acct: Account, statement_date: date, exclude_id=None
):
    """A statement on or before the last reconciled one would start from
    that later statement's balance and offer lines it already closed — a
    Sep 1 statement after the Sep 26 one was accepted (exploratory 2.17.3,
    W-M20)."""
    last = _last_completed(db, acct.id, exclude_id)
    if last and statement_date <= last.statement_date:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{acct.name} is reconciled through {date_text(last.statement_date)}. "
                "Start the next reconciliation with a later statement date."
            ),
        )
    return last


def update(
    db: Session,
    recon: Reconciliation,
    statement_date: date | None = None,
    statement_balance: Decimal | None = None,
) -> Reconciliation:
    """Correct the statement's date or ending balance mid-reconciliation
    (#389). The ending balance only moves the difference, which the next
    session recomputes from the lines already ticked. The date moves the
    session's window: a later date brings more lines in, an earlier one
    leaves the lines after it out, their ticks kept (a tick is a fact about
    the line, as Abandon keeps them). Refused once completed, and refused
    on or before the last completed statement, as a fresh start is."""
    _open(recon)
    if statement_date is not None and statement_date != recon.statement_date:
        acct = db.query(Account).filter(Account.id == recon.account_id).first()
        if not acct:
            raise HTTPException(
                status_code=400,
                detail="This reconciliation predates 2.10 and has no ledger account",
            )
        _require_after_last(db, acct, statement_date, exclude_id=recon.id)
        # Keep the session's high-water date so a later move earlier can tell
        # which lines were in reach before (#400 review).
        prior_high = recon.max_statement_date or recon.statement_date
        recon.statement_date = statement_date
        recon.max_statement_date = max(prior_high, statement_date)
    if statement_balance is not None:
        recon.statement_balance = Decimal(str(statement_balance))
    return recon


def ticked_after_statement_date(db: Session, recon: Reconciliation) -> int:
    """How many ticked lines this session left behind by moving its date
    earlier (#400 review). Only lines that were in reach of an earlier,
    later statement date count — those dated after the current date but on
    or before the session's high-water date. A line cleared by a bank-feed
    match or a register clear, dated after the date without the date ever
    moving, is not one of these: it was never in reach of this session, so a
    fresh reconciliation reports 0. Completing stamps only lines on or before
    the date, so these are left ticked and carry into the next session."""
    high = recon.max_statement_date or recon.statement_date
    if high <= recon.statement_date:
        return 0
    return (
        db.query(TransactionLine)
        .join(Transaction, TransactionLine.transaction_id == Transaction.id)
        .filter(TransactionLine.account_id == recon.account_id)
        .filter(TransactionLine.cleared.is_(True))
        .filter(TransactionLine.reconciliation_id.is_(None))
        .filter(Transaction.date > recon.statement_date)
        .filter(Transaction.date <= high)
        .count()
    )


def _candidates(db: Session, recon: Reconciliation):
    return (
        db.query(TransactionLine, Transaction)
        .join(Transaction, TransactionLine.transaction_id == Transaction.id)
        .filter(TransactionLine.account_id == recon.account_id)
        .filter(Transaction.date <= recon.statement_date)
        .filter(
            (TransactionLine.reconciliation_id.is_(None))
            | (TransactionLine.reconciliation_id == recon.id)
        )
        .order_by(Transaction.date, Transaction.id, TransactionLine.id)
        .all()
    )


def session(db: Session, recon: Reconciliation) -> dict:
    acct = db.query(Account).filter(Account.id == recon.account_id).first()
    if not acct:
        raise HTTPException(
            status_code=400,
            detail="This reconciliation predates 2.10 and has no ledger account",
        )
    debit_normal = is_debit_normal(acct)
    rows = _candidates(db, recon)
    matched_lines = {
        r[0]
        for r in db.query(BankTransaction.transaction_line_id)
        .filter(
            BankTransaction.transaction_line_id.in_([tl.id for tl, _ in rows] or [0])
        )
        .all()
    }
    payees = payees_for(db, [txn for _, txn in rows])
    refs = references_for(db, list({txn.id: txn for _, txn in rows}.values()))
    cleared_total = Decimal("0")
    uncleared_total = Decimal("0")
    out_rows = []
    for tl, txn in rows:
        dr = Decimal(str(tl.debit or 0))
        cr = Decimal(str(tl.credit or 0))
        amount = (dr - cr) if debit_normal else (cr - dr)
        if tl.cleared:
            cleared_total += amount
        else:
            uncleared_total += amount
        out_rows.append(
            {
                "id": tl.id,
                "transaction_id": txn.id,
                "date": txn.date.isoformat(),
                "payee": payees.get(txn.id, ""),
                "description": header_text(txn, tl.description),
                "reference": refs.get(txn.id, ""),
                "check_number": refs.get(txn.id) or None,
                "amount": float(amount),
                "reconciled": bool(tl.cleared),
                "matched": tl.id in matched_lines,
                "source_type": txn.source_type,
                "source_link": source_link(txn),
            }
        )
    statement = Decimal(str(recon.statement_balance or 0))
    beginning = Decimal(str(recon.beginning_balance or 0))
    difference = statement - (beginning + cleared_total)
    return {
        "reconciliation_id": recon.id,
        "account_id": recon.account_id,
        "statement_date": recon.statement_date.isoformat(),
        "statement_balance": float(statement),
        "beginning_balance": float(beginning),
        "cleared_total": float(cleared_total),
        "uncleared_total": float(uncleared_total),
        "difference": float(difference),
        "status": recon.status.value,
        "ticked_after_date": ticked_after_statement_date(db, recon),
        "transactions": out_rows,
    }


def out_of_balance_text(difference: Decimal) -> str:
    return (
        f"Not finished: the difference is {money_text(difference)}, and it must be "
        "$0.00. Tick the lines that are on your statement, or check the "
        "statement's ending balance."
    )


def _open(recon: Reconciliation) -> None:
    if recon.status == ReconciliationStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="Reconciliation already completed")


def toggle(db: Session, recon: Reconciliation, line_id: int) -> TransactionLine:
    _open(recon)
    line = db.query(TransactionLine).filter(TransactionLine.id == line_id).first()
    if not line:
        raise HTTPException(status_code=404, detail="Ledger line not found")
    if line.account_id != recon.account_id:
        raise HTTPException(status_code=400, detail="That line is not on this account")
    if line.reconciliation_id and line.reconciliation_id != recon.id:
        raise HTTPException(
            status_code=400, detail="That line is in a completed reconciliation"
        )
    txn = db.query(Transaction).filter(Transaction.id == line.transaction_id).first()
    if txn.date > recon.statement_date:
        raise HTTPException(
            status_code=400, detail="That line is after the statement date"
        )
    line.cleared = not line.cleared
    return line


def set_register_cleared(
    db: Session, account_id: int, line_id: int, cleared: bool
) -> TransactionLine:
    """The ✓ column of the register, set by hand (#715), as QuickBooks
    Desktop's register does for someone who ticks each entry off against
    online banking and never runs a reconciliation. It is the same flag a
    bank-feed match or a reconciliation tick sets, so the next
    reconciliation opens with the line ticked (and counted in its cleared
    figure) when it is dated on or before the statement date, and unticking
    it there clears it here too. A line a completed reconciliation stamped
    keeps its mark; a voided entry, or the reversal that voided it, is not
    marked cleared (unmarking one a reconciliation ticked is allowed). The
    change is in the audit log through the ORM hook (an UPDATE of
    transaction_lines naming `cleared`)."""
    from app.services.bank_register import voided_transaction_ids

    acct = require_bank_account(db, account_id)
    line = db.query(TransactionLine).filter(TransactionLine.id == line_id).first()
    if not line or line.account_id != acct.id:
        raise HTTPException(
            status_code=404, detail=f"That line is not in the {acct.name} register."
        )
    if line.reconciliation_id:
        recon = db.get(Reconciliation, line.reconciliation_id)
        when = (
            f" of the statement dated {date_text(recon.statement_date)}"
            if recon and recon.statement_date
            else ""
        )
        raise HTTPException(
            status_code=400,
            detail=(
                f"This line is in the completed reconciliation{when}, which "
                "holds its cleared mark."
            ),
        )
    txn = db.get(Transaction, line.transaction_id)
    voided = (txn.source_type or "").endswith("_void") or bool(
        voided_transaction_ids(db, [txn.id])
    )
    if cleared and voided:
        raise HTTPException(
            status_code=400, detail="A voided entry cannot be marked cleared."
        )
    if bool(line.cleared) != cleared:
        line.cleared = cleared
    return line


def mark_all(db: Session, recon: Reconciliation, cleared: bool, through=None) -> int:
    """Tick, or untick, every line the session shows at once (#258): the
    lines on the account up to the statement date (and up to `through`,
    when given, so a statement that ends mid-period is ticked through its
    own date), none of them in a completed reconciliation. A quiet month,
    or a first reconciliation where the statement matches the books, was
    hundreds of single ticks. Returns how many lines changed."""
    _open(recon)
    if through is not None and through > recon.statement_date:
        raise HTTPException(
            status_code=400, detail="That date is after the statement date"
        )
    changed = 0
    for tl, txn in _candidates(db, recon):
        if through is not None and txn.date > through:
            continue
        if bool(tl.cleared) != cleared:
            tl.cleared = cleared
            changed += 1
    return changed


def complete(db: Session, recon: Reconciliation) -> dict:
    _open(recon)
    data = session(db, recon)
    difference = Decimal(str(data["difference"]))
    if abs(difference) > TOLERANCE:
        raise HTTPException(
            status_code=400,
            detail=out_of_balance_text(difference),
        )
    n = 0
    for tl, _ in _candidates(db, recon):
        if tl.cleared:
            tl.reconciliation_id = recon.id
            n += 1
    recon.cleared_total = Decimal(str(data["cleared_total"]))
    recon.status = ReconciliationStatus.COMPLETED
    recon.completed_at = datetime.utcnow()
    return {"status": "completed", "reconciliation_id": recon.id, "cleared_count": n}


def abandon(db: Session, recon: Reconciliation) -> None:
    """Drop an in-progress reconciliation; cleared ticks stay (they are
    facts about the lines, as in QuickBooks)."""
    _open(recon)
    db.delete(recon)


# ---------------------------------------------------------------------------
# The reconciliation report — what a completed reconciliation leaves behind
# to keep: the beginning and ending balances, every item it cleared, what
# was still outstanding on the statement date, and the register's balance
# then. There was none (exploratory 2.17.3, W-M20).
# ---------------------------------------------------------------------------


def _labels(acct: Account) -> dict:
    if acct.bank_kind == "credit_card":
        return {"increase": "Charges and fees", "decrease": "Payments and credits"}
    return {"increase": "Deposits and other credits", "decrease": "Checks and payments"}


def report(db: Session, recon: Reconciliation) -> dict:
    if recon.status != ReconciliationStatus.COMPLETED:
        raise HTTPException(
            status_code=400,
            detail=(
                "This reconciliation isn't finished yet. Its report is kept "
                "once it is completed."
            ),
        )
    acct = db.query(Account).filter(Account.id == recon.account_id).first()
    if not acct:
        raise HTTPException(
            status_code=400,
            detail="This reconciliation predates 2.10 and has no ledger account",
        )
    debit_normal = is_debit_normal(acct)
    # Lines an earlier statement closed are that statement's business.
    earlier = [
        r.id
        for r in db.query(Reconciliation)
        .filter(
            Reconciliation.account_id == acct.id,
            Reconciliation.status == ReconciliationStatus.COMPLETED,
            Reconciliation.id != recon.id,
        )
        .all()
        if (r.statement_date, r.id) < (recon.statement_date, recon.id)
    ]
    q = (
        db.query(TransactionLine, Transaction)
        .join(Transaction, TransactionLine.transaction_id == Transaction.id)
        .filter(TransactionLine.account_id == acct.id)
        .filter(Transaction.date <= recon.statement_date)
    )
    if earlier:
        q = q.filter(
            (TransactionLine.reconciliation_id.is_(None))
            | (~TransactionLine.reconciliation_id.in_(earlier))
        )
    rows = q.order_by(Transaction.date, Transaction.id, TransactionLine.id).all()
    txns = list({txn.id: txn for _, txn in rows}.values())
    payees = payees_for(db, txns)
    refs = references_for(db, txns)

    def _group():
        return {"items": [], "total": Decimal("0"), "count": 0}

    groups = {
        "cleared": {"increase": _group(), "decrease": _group()},
        "uncleared": {"increase": _group(), "decrease": _group()},
    }
    for tl, txn in rows:
        dr = Decimal(str(tl.debit or 0))
        cr = Decimal(str(tl.credit or 0))
        amount = (dr - cr) if debit_normal else (cr - dr)
        state = "cleared" if tl.reconciliation_id == recon.id else "uncleared"
        g = groups[state]["increase" if amount >= 0 else "decrease"]
        g["items"].append(
            {
                "line_id": tl.id,
                "date": txn.date.isoformat(),
                "reference": refs.get(txn.id, ""),
                "payee": payees.get(txn.id, ""),
                "description": header_text(txn, tl.description),
                "amount": float(amount),
                "source_type": txn.source_type,
            }
        )
        g["total"] += amount
        g["count"] += 1
    for state in groups.values():
        for g in state.values():
            g["total"] = float(g["total"])

    beginning = Decimal(str(recon.beginning_balance or 0))
    ending = Decimal(str(recon.statement_balance or 0))
    cleared_total = Decimal(str(groups["cleared"]["increase"]["total"])) + Decimal(
        str(groups["cleared"]["decrease"]["total"])
    )
    register, _ = balance_as_of(db, acct, recon.statement_date)
    return {
        "reconciliation_id": recon.id,
        "account_id": acct.id,
        "account_name": acct.name,
        "account_number": acct.account_number,
        "bank_kind": acct.bank_kind,
        "statement_date": recon.statement_date.isoformat(),
        "completed_at": (
            recon.completed_at.isoformat() if recon.completed_at else None
        ),
        "beginning_balance": float(beginning),
        "ending_balance": float(ending),
        "cleared_total": float(cleared_total),
        "cleared_balance": float(beginning + cleared_total),
        "difference": float(ending - (beginning + cleared_total)),
        "register_balance": float(register),
        "labels": _labels(acct),
        "cleared": groups["cleared"],
        "uncleared": groups["uncleared"],
    }


def report_sections(data: dict, numbered: bool = True) -> list[dict]:
    """The report as sections for the shared report PDF renderer. `numbered`
    is the company's use-account-numbers setting (#567); the PDF passes it,
    the CSV keeps the number."""
    labels = data["labels"]
    acct = account_label(data["account_number"], data["account_name"], numbered, " ")
    period = f"{acct} · statement ending {date_text(date.fromisoformat(data['statement_date']))}"

    def _count(state, side):
        g = data[state][side]
        return f"{labels[side]} ({g['count']})"

    summary = [
        {"cells": ["Beginning balance", money_text(data["beginning_balance"])]},
        {
            "cells": [
                f"Cleared {_count('cleared', 'increase').lower()}",
                money_text(data["cleared"]["increase"]["total"]),
            ]
        },
        {
            "cells": [
                f"Cleared {_count('cleared', 'decrease').lower()}",
                money_text(data["cleared"]["decrease"]["total"]),
            ]
        },
        {
            "cells": ["Cleared balance", money_text(data["cleared_balance"])],
            "style": "subtotal",
        },
        {"cells": ["Statement ending balance", money_text(data["ending_balance"])]},
        {
            "cells": ["Difference", money_text(data["difference"])],
            "style": "grand-total",
        },
        {
            "cells": [
                f"Uncleared {_count('uncleared', 'increase').lower()}",
                money_text(data["uncleared"]["increase"]["total"]),
            ]
        },
        {
            "cells": [
                f"Uncleared {_count('uncleared', 'decrease').lower()}",
                money_text(data["uncleared"]["decrease"]["total"]),
            ]
        },
        {
            "cells": [
                "Register balance as of "
                + date_text(date.fromisoformat(data["statement_date"])),
                money_text(data["register_balance"]),
            ],
            "style": "subtotal",
        },
    ]
    detail = []
    for state, title in (("cleared", "Cleared"), ("uncleared", "Uncleared")):
        for side in ("increase", "decrease"):
            g = data[state][side]
            if not g["items"]:
                continue
            detail.append(
                {"cells": [f"{title} {labels[side].lower()}", ""], "style": "subtotal"}
            )
            for item in g["items"]:
                words = " · ".join(
                    part
                    for part in (
                        date_text(date.fromisoformat(item["date"])),
                        item["reference"],
                        item["payee"] or item["description"],
                    )
                    if part
                )
                detail.append({"cells": [f"  {words}", money_text(item["amount"])]})
            detail.append(
                {
                    "cells": [
                        f"Total {title.lower()} {labels[side].lower()}",
                        money_text(g["total"]),
                    ],
                    "style": "subtotal",
                }
            )
    if not detail:
        detail.append({"cells": ["Nothing was on or before the statement date", ""]})
    return [
        {
            "title": "Reconciliation Summary",
            "period": period,
            "columns": ["", "Amount"],
            "rows": summary,
        },
        {
            "title": "Reconciliation Detail",
            "period": period,
            "columns": ["Date · Ref # · Payee", "Amount"],
            "rows": detail,
        },
    ]
