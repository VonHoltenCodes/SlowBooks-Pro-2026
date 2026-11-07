# ============================================================================
# Posting the WIP schedule's adjusting entry. The schedule (job_reports) only
# computes the entry; Post books it as an adjusting journal dated the as-of
# date, with an automatic reversal dated the next day, so each period's entry
# carries that period's balances and the next one starts from zero.
#
# What was posted is recorded on the journals themselves, no table of its
# own: the entry is source_type "wip_adjustment" with source_id the as-of date
# as yyyymmdd, its reversal "wip_reversal" with source_id the entry's id. Void
# reverses both ("..._void", source_id the voided journal's id); only then
# may the same date be posted again.
#
# The lines name the job in their memo and carry no job tag: a tagged loss
# provision or revenue adjustment would come back into the job's own cost to
# date and posted revenue, the very figures the schedule is computed from.
# ============================================================================

from datetime import date, timedelta

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.transactions import BASIS_ACCRUAL, Transaction
from app.services.accounting import (
    _q,
    all_lines,
    create_journal_entry,
    ensure_construction_accounts,
    fit_line_memo,
    reversing_lines,
)

SOURCE_ENTRY = "wip_adjustment"
SOURCE_REVERSAL = "wip_reversal"
WIP_SOURCES = (SOURCE_ENTRY, SOURCE_REVERSAL)


def _date_key(as_of: date) -> int:
    return as_of.year * 10000 + as_of.month * 100 + as_of.day


def _voided(db: Session, txn: Transaction) -> bool:
    return (
        db.query(Transaction.id)
        .filter(
            Transaction.source_type == f"{txn.source_type}_void",
            Transaction.source_id == txn.id,
        )
        .first()
        is not None
    )


def posted_entry(db: Session, as_of: date):
    """(entry, reversal) standing for `as_of`, or None when nothing is posted
    for that date or what was posted has been voided."""
    for txn in (
        db.query(Transaction)
        .filter(
            Transaction.source_type == SOURCE_ENTRY,
            Transaction.source_id == _date_key(as_of),
        )
        .order_by(Transaction.id)
    ):
        if _voided(db, txn):
            continue
        reversal = (
            db.query(Transaction)
            .filter(
                Transaction.source_type == SOURCE_REVERSAL,
                Transaction.source_id == txn.id,
            )
            .first()
        )
        return txn, reversal
    return None


def posted_entry_info(db: Session, as_of: date):
    """What the schedule says about a posted entry for `as_of` (None if none)."""
    found = posted_entry(db, as_of)
    if found is None:
        return None
    txn, reversal = found
    return {
        "transaction_id": txn.id,
        "date": txn.date.isoformat(),
        "reference": txn.reference,
        "posted_by": txn.posted_by,
        "reversal_id": reversal.id if reversal else None,
        "reversal_date": reversal.date.isoformat() if reversal else None,
    }


def _journal_lines(entry: dict, accounts: dict, as_of: date) -> list[dict]:
    """The computed entry's per-job lines as journal lines, accrual only (a
    WIP adjustment has no cash side), each memo naming its job."""
    out = []
    for job in entry["jobs"]:
        for ln in job["lines"]:
            out.append(
                {
                    "account_id": accounts[ln["account"]].id,
                    "debit": _q(ln["debit"]),
                    "credit": _q(ln["credit"]),
                    "description": fit_line_memo(
                        f"WIP {as_of.isoformat()}: {job['job_name']}"
                    ),
                    "basis": BASIS_ACCRUAL,
                }
            )
    return out


def post_wip_entry(db: Session, as_of: date, acting: str) -> dict:
    """Post the WIP adjusting entry for `as_of` (the whole schedule, as the
    Report Center shows it with no filter) and its reversal the next day.
    Refused when an entry for the date stands, when there is nothing to post,
    and on a closed period (create_journal_entry checks both dates). The
    caller commits."""
    from app.services.job_reports import WIP_ACCOUNTS, wip_schedule

    if posted_entry(db, as_of) is not None:
        raise HTTPException(
            status_code=409,
            detail=f"A WIP adjusting entry is already posted for "
            f"{as_of.isoformat()}. Void it first (Void posted entry on the WIP "
            "schedule), then post again.",
        )
    entry = wip_schedule(db, as_of)["adjusting_entry"]
    if not entry["jobs"]:
        raise HTTPException(
            status_code=422,
            detail=f"Nothing to post as of {as_of.isoformat()}: no job is over "
            "or under billed and none carries a loss provision.",
        )
    by_name = {a.name: a for a in ensure_construction_accounts(db).values()}
    accounts = {name: by_name[name] for name in WIP_ACCOUNTS}
    lines = _journal_lines(entry, accounts, as_of)
    reference = f"WIP-{as_of.isoformat()}"
    txn = create_journal_entry(
        db,
        as_of,
        f"WIP adjusting entry as of {as_of.isoformat()}",
        lines,
        source_type=SOURCE_ENTRY,
        source_id=_date_key(as_of),
        reference=reference,
        is_adjusting=True,
        posted_by=acting,
    )
    next_day = as_of + timedelta(days=1)
    reversal = create_journal_entry(
        db,
        next_day,
        f"Reversal of the WIP adjusting entry as of {as_of.isoformat()}",
        [{**ln, "debit": ln["credit"], "credit": ln["debit"]} for ln in lines],
        source_type=SOURCE_REVERSAL,
        source_id=txn.id,
        reference=reference,
        is_adjusting=True,
        posted_by=acting,
    )
    db.flush()
    return {
        "transaction_id": txn.id,
        "reversal_id": reversal.id,
        "date": as_of.isoformat(),
        "reversal_date": next_day.isoformat(),
        "total_debits": entry["total_debits"],
        "total_credits": entry["total_credits"],
    }


def void_wip_entry(db: Session, as_of: date, acting: str) -> dict:
    """Void the WIP entry posted for `as_of` and its reversal, each by a
    mirror image on its own date. Refused when nothing stands for the date
    and on a closed period. The caller commits."""
    found = posted_entry(db, as_of)
    if found is None:
        raise HTTPException(
            status_code=404,
            detail=f"No WIP adjusting entry is posted for {as_of.isoformat()}.",
        )
    voids = []
    for txn in found:
        if txn is None or _voided(db, txn):
            continue
        voids.append(
            create_journal_entry(
                db,
                txn.date,
                f"VOID adjusting entry: {txn.description}",
                reversing_lines(all_lines(db, txn.id)),
                source_type=f"{txn.source_type}_void",
                source_id=txn.id,
                reference=txn.reference,
                is_adjusting=True,
                posted_by=acting,
            )
        )
    db.flush()
    return {"voided": [t.source_id for t in voids], "date": as_of.isoformat()}
