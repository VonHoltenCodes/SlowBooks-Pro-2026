# ============================================================================
# Accrued job costs (#897): a cost booked before the vendor's bill arrives.
#
# Month-end on a job: the subcontractor finished the work, the bill has not
# come, the cost belongs in this period's WIP. A job cost entry debits the
# job's cost account (tagged to the job, code and type, so the WIP schedule
# and cost-to-complete read it like any other cost) and credits 2010 Accrued
# Job Costs, a liability, instead of Accounts Payable (which holds only bills
# and is refused on the entry). The entry carries a Reverses on date, the
# first day of the next month unless the person picks another (or clears
# it); on that date the start-up pass posts the reversing entry, DR 2010 / CR
# the cost account with the same job tags, so when the bill is entered the
# normal way the cost is in WIP once, not twice.
#
# Once only: the reversal's transaction is kept on the entry, and it is
# claimed with one guarded UPDATE (reversal_transaction_id IS NULL) before
# the commit, so a second start, or a second server process starting beside
# the first, posts nothing. The claim writes an audit row saying what it is,
# which the entry's Activity reads ("Reversed on ... by the system").
#
# Books Check sets the open accruals (entries not yet reversed) beside the
# 2010 balance (books_check.check_accrued_job_costs).
# ============================================================================

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from app.models.job_costing import JobCost
from app.models.transactions import Transaction
from app.services import control_accounts
from app.services.accounting import _q

logger = logging.getLogger(__name__)

ZERO = Decimal("0")
REVERSAL_SOURCE = "job_cost_reversal"
# the void of a reversal, when the accrual is voided after it reversed: its
# source_id is the reversal's transaction id (voids.TRANSACTION_KEYED_VOIDS)
REVERSAL_VOID_SOURCE = "job_cost_reversal_void"
# the audit row's source for the claim, so the trail says which pass wrote it
AUDIT_SOURCE = "accrual_reversal"


def account_id(db: Session) -> Optional[int]:
    """2010 Accrued Job Costs' id, or None when the chart has none (it is
    made on the first accrual, `ensure_account`)."""
    acct_id = control_accounts.find(db, control_accounts.ACCRUED_JOB_COSTS)
    if acct_id is None:
        return None
    from app.models.accounts import Account

    acct = db.get(Account, acct_id)
    if acct is None or not control_accounts.is_control(
        acct.account_number, acct.account_type, acct.name
    ):
        return None
    return acct_id


def ensure_account(db: Session) -> int:
    """2010 Accrued Job Costs' id, created in a chart that lacks it."""
    return control_accounts.ensure(db, control_accounts.ACCRUED_JOB_COSTS)


def first_of_next_month(d: date) -> date:
    return date(d.year + 1, 1, 1) if d.month == 12 else date(d.year, d.month + 1, 1)


def accrued_amount(jc: JobCost, acct_id: Optional[int]) -> Decimal:
    """What the entry credited to 2010: its lines whose offset is 2010."""
    if acct_id is None:
        return ZERO
    return _q(
        sum(
            (
                Decimal(str(ln.amount or 0))
                for ln in jc.lines
                if ln.credit_account_id == acct_id
            ),
            ZERO,
        )
    )


def is_accrual(db: Session, jc: JobCost) -> bool:
    return accrued_amount(jc, account_id(db)) > ZERO


def reversal_date(jc: JobCost) -> Optional[date]:
    """The date the posted reversal is dated, or None before it posts."""
    txn = jc.reversal_transaction
    return txn.date if txn is not None else None


def check_reverses_on(jc: JobCost, value: Optional[date]) -> None:
    """A reversal date is after the accrual's own date (a reversal on the
    same day would take the cost out of the period it was accrued for)."""
    from app.services.safe_errors import DataProblem

    if value is not None and value <= jc.date:
        raise DataProblem(
            f"The reversal date must be after the entry's date ({jc.date.isoformat()}); "
            f"the first day of the next month is {first_of_next_month(jc.date).isoformat()}."
        )


def settle_reverses_on(
    db: Session, jc: JobCost, sent: bool, value: Optional[date]
) -> None:
    """After an entry is posted: an accrual takes `value` when the request
    named one (null clears it), else the first of the next month; any other
    entry keeps none, so it never reverses."""
    if not is_accrual(db, jc):
        jc.reverses_on = None
        return
    if sent:
        check_reverses_on(jc, value)
        jc.reverses_on = value
    else:
        jc.reverses_on = first_of_next_month(jc.date)


def _reversal_lines(jc: JobCost, acct_id: int) -> list[dict]:
    lines = []
    for ln in jc.lines:
        amt = _q(Decimal(str(ln.amount or 0)))
        if amt == ZERO or ln.credit_account_id != acct_id:
            continue
        desc = (
            ln.description or (ln.cost_code.label if ln.cost_code else "") or "Job cost"
        )
        lines.append(
            {
                "account_id": acct_id,
                "debit": amt,
                "credit": ZERO,
                "description": f"Accrual reversed: {desc}",
                "job_id": None,
            }
        )
        lines.append(
            {
                "account_id": ln.debit_account_id,
                "debit": ZERO,
                "credit": amt,
                "description": f"Accrual reversed: {desc}",
                "job_id": ln.job_id or jc.job_id,
                "cost_code_id": ln.cost_code_id,
                "cost_type": ln.cost_type,
            }
        )
    return lines


def post_reversal(db: Session, jc: JobCost, acct_id: int) -> Optional[Transaction]:
    """Post the accrual's reversal, dated its Reverses on date, and claim it
    on the entry. None when another pass claimed it first (nothing is left
    posted: the caller rolls back)."""
    from app.services.accounting import create_journal_entry
    from app.services.audit import log_event

    lines = _reversal_lines(jc, acct_id)
    if not lines:
        return None
    label = jc.job.full_name if jc.job else "allocation"
    txn = create_journal_entry(
        db,
        jc.reverses_on,
        f"Reversal of accrued job cost {jc.number} - {label}",
        lines,
        source_type=REVERSAL_SOURCE,
        source_id=jc.id,
        reference=jc.number,
    )
    db.flush()
    claimed = (
        db.query(JobCost)
        .filter(JobCost.id == jc.id, JobCost.reversal_transaction_id.is_(None))
        .update({JobCost.reversal_transaction_id: txn.id}, synchronize_session=False)
    )
    if claimed != 1:
        return None
    log_event(
        db,
        "job_costs",
        jc.id,
        "UPDATE",
        old_values={"reversal_transaction_id": None},
        new_values={
            "reversal_transaction_id": txn.id,
            "reversed_on": jc.reverses_on.isoformat(),
            "reversal": "accrual reversed on its Reverses on date",
        },
        changed_fields=["reversal_transaction_id"],
        source=AUDIT_SOURCE,
    )
    return txn


def due_accruals(db: Session, today: date) -> list[JobCost]:
    return (
        db.query(JobCost)
        .filter(
            JobCost.status == "posted",
            JobCost.reverses_on.isnot(None),
            JobCost.reverses_on <= today,
            JobCost.reversal_transaction_id.is_(None),
        )
        .order_by(JobCost.reverses_on, JobCost.id)
        .all()
    )


def reverse_due_accruals(db: Session, today: Optional[date] = None) -> dict:
    """The start-up pass: post every accrual's reversal whose date has come,
    each in its own commit. Returns {"reversed": [numbers], "skipped":
    [(number, why)]}; an entry skipped (its date is in a closed period, its
    chart lost 2010) is tried again at the next start."""
    from app.services.safe_errors import safe_message

    today = today or date.today()
    out: dict = {"reversed": [], "skipped": []}
    acct_id = account_id(db)
    if acct_id is None:
        return out
    for jc in due_accruals(db, today):
        number = jc.number
        try:
            txn = post_reversal(db, jc, acct_id)
            if txn is None:
                db.rollback()
                continue
            db.commit()
            out["reversed"].append(number)
            logger.info(
                "accrued job cost %s reversed on %s (entry %s)",
                number,
                txn.date.isoformat(),
                txn.id,
            )
        except Exception as exc:  # noqa: BLE001 - one entry never stops the rest
            db.rollback()
            detail = getattr(exc, "detail", None)
            why = detail if isinstance(detail, str) else safe_message(exc)
            out["skipped"].append((number, why))
            logger.warning("accrued job cost %s not reversed: %s", number, exc)
    return out


def reversal_sentence(result: dict) -> Optional[str]:
    """The start-up log line naming what the pass did, or None."""
    parts = []
    done = result.get("reversed") or []
    if done:
        word = "accrual" if len(done) == 1 else "accruals"
        parts.append(
            f"Accrued job costs: reversed {len(done)} {word} on its date "
            f"({', '.join(done)})."
        )
    for number, why in result.get("skipped") or []:
        parts.append(
            f"Accrued job cost {number} was not reversed and is tried again at "
            f"the next start: {why}"
        )
    return " ".join(parts) or None


def void_reversal(db: Session, jc: JobCost) -> None:
    """An accrual voided after it reversed: its reversal is reversed too, so
    2010 and the job's cost both come back to where they were before it."""
    from app.services.accounting import create_journal_entry
    from app.services.accounting import void_line_memo

    txn = jc.reversal_transaction
    if txn is None:
        return
    create_journal_entry(
        db,
        jc.date if jc.date > txn.date else txn.date,
        f"VOID {txn.description or jc.number}",
        [
            {
                "account_id": ln.account_id,
                "debit": ln.credit,
                "credit": ln.debit,
                "description": void_line_memo(ln.description),
                "job_id": ln.job_id,
                "cost_code_id": ln.cost_code_id,
                "cost_type": ln.cost_type,
            }
            for ln in txn.lines
        ],
        source_type=REVERSAL_VOID_SOURCE,
        source_id=txn.id,
        reference=jc.number,
    )


def open_accruals(db: Session, as_of: date) -> list[tuple[JobCost, Decimal]]:
    """(entry, amount on 2010) for every posted accrual dated to `as_of`
    whose reversal had not posted by then: what 2010 should hold."""
    acct_id = account_id(db)
    if acct_id is None:
        return []
    from app.models.job_costing import JobCostLine

    accruals = (
        db.query(JobCostLine.job_cost_id)
        .filter(JobCostLine.credit_account_id == acct_id)
        .distinct()
    )
    out = []
    for jc in (
        db.query(JobCost)
        .filter(
            JobCost.status == "posted",
            JobCost.date <= as_of,
            JobCost.id.in_(accruals),
        )
        .order_by(JobCost.date, JobCost.id)
    ):
        amount = accrued_amount(jc, acct_id)
        if amount == ZERO:
            continue
        reversed_on = reversal_date(jc)
        if reversed_on is not None and reversed_on <= as_of:
            continue
        out.append((jc, amount))
    return out
