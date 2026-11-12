# ============================================================================
# Estimate Closing Rate (#795) — how the bids are doing over a period: how
# many estimates went out, how many were won (accepted or converted), lost
# (marked lost) or are still open, by count and by dollars, split by customer
# and by job type, with the average days from sent to decision.
#
# An estimate is in the period by its date. The closing rate is won ÷
# decided (won + lost): an open bid is not counted either way. Dollars are
# the estimate's total as it stands, or with before_tax the total less its
# sales tax (the subtotal and the markups: the bid amount). "Sent" is the first time the estimate
# was emailed from SlowBooks, else its date; the decision is decided_at,
# which the status change stamps (app/models/estimates.py). An estimate
# decided before decided_at existed and with nothing to date it from is
# left out of the average and counted in `undated`. Nothing is posted.
# ============================================================================

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session, joinedload

from app.models.email_log import EmailLog
from app.models.estimates import Estimate, estimate_outcome
from app.services.accounting import _q
from app.services.fiscal_year import fiscal_year_start

_ZERO = Decimal("0")
NO_JOB_TYPE = "No job type"

COLUMNS = [
    {"key": "name", "label": "Name", "kind": "text"},
    {"key": "sent_count", "label": "Sent", "kind": "int"},
    {"key": "sent_amount", "label": "Sent $", "kind": "money"},
    {"key": "won_count", "label": "Won", "kind": "int"},
    {"key": "won_amount", "label": "Won $", "kind": "money"},
    {"key": "lost_count", "label": "Lost", "kind": "int"},
    {"key": "lost_amount", "label": "Lost $", "kind": "money"},
    {"key": "open_count", "label": "Open", "kind": "int"},
    {"key": "open_amount", "label": "Open $", "kind": "money"},
    {"key": "expired_count", "label": "Expired (open)", "kind": "int"},
    {"key": "closing_rate", "label": "Closing rate", "kind": "pct"},
    {"key": "closing_rate_amount", "label": "Closing rate $", "kind": "pct"},
    {"key": "avg_days", "label": "Avg days to decision", "kind": "num1"},
]


def _bucket() -> dict:
    return {
        "sent": [0, _ZERO],
        "won": [0, _ZERO],
        "lost": [0, _ZERO],
        "open": [0, _ZERO],
        "expired": 0,
        "days": [],
        "undated": 0,
    }


def _rate(won, decided):
    """Won as a percent of decided, or None when nothing is decided yet."""
    return round(float(won) / float(decided) * 100, 1) if decided else None


def _figures(b: dict) -> dict:
    won_n, won_amt = b["won"]
    lost_n, lost_amt = b["lost"]
    days = b["days"]
    return {
        "sent_count": b["sent"][0],
        "sent_amount": float(_q(b["sent"][1])),
        "won_count": won_n,
        "won_amount": float(_q(won_amt)),
        "lost_count": lost_n,
        "lost_amount": float(_q(lost_amt)),
        "open_count": b["open"][0],
        "open_amount": float(_q(b["open"][1])),
        "expired_count": b["expired"],
        "closing_rate": _rate(won_n, won_n + lost_n),
        "closing_rate_amount": _rate(won_amt, won_amt + lost_amt),
        "avg_days": round(sum(days) / len(days), 1) if days else None,
        "undated": b["undated"],
    }


def _local_date(dt: datetime) -> date:
    """A stored timestamp's date on this machine's clock, the clock an
    estimate's own date is kept on. SQLite hands back the UTC value naive."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone().date()


def _first_sent(db: Session, ids: list[int]) -> dict[int, date]:
    """Each estimate's first successful email from SlowBooks, as a date."""
    if not ids:
        return {}
    rows = (
        db.query(EmailLog.entity_id, sqlfunc.min(EmailLog.sent_at))
        .filter(
            EmailLog.entity_type == "estimate",
            EmailLog.status == "sent",
            EmailLog.entity_id.in_(ids),
        )
        .group_by(EmailLog.entity_id)
        .all()
    )
    return {eid: _local_date(at) for eid, at in rows if at is not None}


def estimate_closing_report(
    db: Session,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    customer_id: Optional[int] = None,
    job_type: Optional[str] = None,
    before_tax: bool = False,
) -> dict:
    """Estimates dated in the period, won, lost and open, by count and by
    dollars, in total, per customer and per job type. `job_type` "" (or the
    "No job type" label) picks the estimates with no job or no job type.
    `before_tax` counts each estimate's dollars without its sales tax."""
    today = date.today()
    start_date = start_date or fiscal_year_start(db, today)
    end_date = end_date or today
    if start_date > end_date:
        raise HTTPException(
            status_code=400, detail="The start date is after the end date."
        )
    q = (
        db.query(Estimate)
        .options(joinedload(Estimate.customer), joinedload(Estimate.job))
        .filter(Estimate.date >= start_date, Estimate.date <= end_date)
    )
    if customer_id is not None:
        q = q.filter(Estimate.customer_id == customer_id)
    estimates = q.all()
    if job_type is not None:
        want = job_type.strip()
        if want == NO_JOB_TYPE:
            want = ""
        estimates = [e for e in estimates if _job_type(e) == want]
    sent_on = _first_sent(db, [e.id for e in estimates])

    total = _bucket()
    by_customer: dict[int, dict] = {}
    names: dict[int, str] = {}
    by_type: dict[str, dict] = {}
    for e in estimates:
        amount = Decimal(str(e.total or 0))
        if before_tax:
            amount -= Decimal(str(e.tax_amount or 0))
        names[e.customer_id] = e.customer.name if e.customer else ""
        buckets = (
            total,
            by_customer.setdefault(e.customer_id, _bucket()),
            by_type.setdefault(_job_type(e) or NO_JOB_TYPE, _bucket()),
        )
        outcome = estimate_outcome(e.status) or "open"
        days = None
        if outcome != "open" and e.decided_at is not None:
            sent = sent_on.get(e.id, e.date)
            days = max((_local_date(e.decided_at) - sent).days, 0)
        expired = (
            outcome == "open"
            and e.expiration_date is not None
            and e.expiration_date < today
        )
        for b in buckets:
            b["sent"][0] += 1
            b["sent"][1] += amount
            b[outcome][0] += 1
            b[outcome][1] += amount
            b["expired"] += expired
            if days is not None:
                b["days"].append(days)
            elif outcome != "open":
                b["undated"] += 1

    customer_rows = [
        {"customer_id": cid, "name": names[cid], **_figures(b)}
        for cid, b in sorted(by_customer.items(), key=lambda kv: names[kv[0]].lower())
    ]
    type_rows = [
        {"job_type": "" if t == NO_JOB_TYPE else t, "name": t, **_figures(b)}
        for t, b in sorted(
            by_type.items(), key=lambda kv: (kv[0] == NO_JOB_TYPE, kv[0].lower())
        )
    ]
    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "as_of": today.isoformat(),
        "before_tax": before_tax,
        "columns": COLUMNS,
        "totals": _figures(total),
        "by_customer": customer_rows,
        "by_job_type": type_rows,
    }


def _job_type(e: Estimate) -> str:
    return ((e.job.job_type if e.job else None) or "").strip()
