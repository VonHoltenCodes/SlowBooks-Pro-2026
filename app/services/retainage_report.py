# ============================================================================
# Retainage report — what owners hold from the contractor (1150 Retainage
# Receivable) and what the contractor holds from subs (2050 Retainage
# Payable), per job, as of a date.
#
# The detail comes from the documents (a pay application's invoice holds and
# releases owner retainage; a sub's bill holds it, a release bill gives it
# back); the totals are checked against the ledger. A job's ledger balance on
# the account is the figure the Balance Sheet shows for it, so any difference
# between the two (a journal entry posted straight to 1150 or 2050, a bill
# split across jobs) shows as its own "Journal entries and other" row and the
# grand totals always equal the Balance Sheet lines for the date.
# Money is Decimal, quantized to cents half-up through accounting._q.
# ============================================================================

from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from app.models.accounts import Account
from app.models.bills import Bill, BillStatus
from app.models.invoices import Invoice, InvoiceStatus
from app.models.purchase_orders import PurchaseOrder
from app.models.transactions import Transaction, TransactionLine
from app.services.accounting import _q
from app.services.contact_balances import home_amount
from app.services.job_reports import _bill_job_share, _customer_name, select_jobs
from app.services.jobs_service import job_attribution

_ZERO = Decimal("0")

RECEIVABLE_ACCOUNT = "Retainage Receivable"  # 1150, the owner holds it
PAYABLE_ACCOUNT = "Retainage Payable"  # 2050, the contractor holds it

RECEIVABLE_COLUMNS = [
    {"key": "customer_name", "label": "Customer", "kind": "text"},
    {"key": "job_name", "label": "Job", "kind": "text"},
    {"key": "held", "label": "Held by owner", "kind": "money"},
    {"key": "released", "label": "Released", "kind": "money"},
    {"key": "outstanding", "label": "Outstanding", "kind": "money"},
]

PAYABLE_COLUMNS = [
    {"key": "job_name", "label": "Job", "kind": "text"},
    {"key": "vendor_name", "label": "Vendor", "kind": "text"},
    {"key": "document", "label": "Commitment / bill", "kind": "text"},
    {"key": "held", "label": "Held from sub", "kind": "money"},
    {"key": "released", "label": "Released", "kind": "money"},
    {"key": "outstanding", "label": "Outstanding", "kind": "money"},
]

_OTHER = "Journal entries and other"


def _ledger_by_job(
    db: Session, account_name: str, as_of: date, debit_normal: bool
) -> dict[Optional[int], Decimal]:
    """The account's balance to `as_of` per job (None = no job), in its
    natural direction: the amount the Balance Sheet line is made of."""
    acct = db.query(Account).filter(Account.name == account_name).first()
    if acct is None:
        return {}
    rows = (
        db.query(
            job_attribution(),
            sqlfunc.coalesce(sqlfunc.sum(TransactionLine.debit), 0),
            sqlfunc.coalesce(sqlfunc.sum(TransactionLine.credit), 0),
        )
        .select_from(TransactionLine)
        .join(Transaction, TransactionLine.transaction_id == Transaction.id)
        .filter(TransactionLine.account_id == acct.id, Transaction.date <= as_of)
        .group_by(job_attribution())
        .all()
    )
    out = {}
    for job_id, dr, cr in rows:
        dr, cr = Decimal(str(dr)), Decimal(str(cr))
        out[job_id] = _q(dr - cr if debit_normal else cr - dr)
    return out


def _owner_side(db: Session, as_of: date) -> dict[Optional[int], dict]:
    """Per job: retainage held and released on the owner's invoices (a pay
    application's invoice is the only document that carries either)."""
    invoices = (
        db.query(Invoice)
        .filter(
            Invoice.status != InvoiceStatus.VOID,
            Invoice.date <= as_of,
            (Invoice.retainage_held != 0) | (Invoice.retainage_released != 0),
        )
        .all()
    )
    out: dict[Optional[int], dict] = {}
    for inv in invoices:
        f = out.setdefault(inv.job_id, {"held": _ZERO, "released": _ZERO})
        f["held"] += home_amount(inv.retainage_held, inv.exchange_rate)
        f["released"] += home_amount(inv.retainage_released, inv.exchange_rate)
    return out


def _sub_side(db: Session, as_of: date) -> dict[tuple, dict]:
    """Per (job, commitment or hand-entered bill): retainage held on the sub's
    bills and released by release bills. A bill split across jobs splits its
    retainage by the money on each (the share the Job Cost by Vendor report
    uses)."""
    bills = (
        db.query(Bill)
        .filter(
            Bill.status.notin_([BillStatus.VOID, BillStatus.DRAFT]),
            Bill.date <= as_of,
            (Bill.retainage_held != 0) | (Bill.retainage_released != 0),
        )
        .all()
    )
    po_numbers = dict(
        db.query(PurchaseOrder.id, PurchaseOrder.po_number).filter(
            PurchaseOrder.id.in_({b.po_id for b in bills if b.po_id})
        )
    )
    out: dict[tuple, dict] = {}
    for b in bills:
        job_ids = {b.job_id} | {ln.job_id for ln in b.lines if ln.job_id is not None}
        if not job_ids or job_ids == {None}:
            shares = {None: Decimal("1")}
        else:
            shares = {j: _bill_job_share(b, j) for j in job_ids if j is not None}
        doc = ("po", b.po_id) if b.po_id else ("bill", b.id)
        for job_id, share in shares.items():
            if share == _ZERO:
                continue
            f = out.setdefault(
                (job_id,) + doc,
                {
                    "vendor_id": b.vendor_id,
                    "vendor_name": b.vendor.name if b.vendor else "",
                    "document": po_numbers.get(b.po_id) or b.bill_number,
                    "kind": "commitment" if b.po_id else "bill",
                    "held": _ZERO,
                    "released": _ZERO,
                },
            )
            f["held"] += share * home_amount(b.retainage_held, b.exchange_rate)
            f["released"] += share * home_amount(b.retainage_released, b.exchange_rate)
    return out


def _figures(held: Decimal, released: Decimal) -> dict:
    held, released = _q(held), _q(released)
    return {
        "held": float(held),
        "released": float(released),
        "outstanding": float(_q(held - released)),
    }


def retainage_report(
    db: Session,
    as_of: Optional[date] = None,
    job_id: Optional[int] = None,
    customer_id: Optional[int] = None,
    status: Optional[str] = None,
) -> dict:
    """Retainage per job as of a date: what owners hold from the contractor
    (`receivable`, 1150) and what the contractor holds from each sub
    (`payable`, 2050, per commitment and per hand-entered bill), with job and
    grand totals. Closed jobs are included: retainage outlives the job, and
    `status` matches the status the job had on `as_of`. A job
    with no retainage held, released or on the books is left out. The grand
    totals equal the Balance Sheet's 1150 and 2050 for the date; a ledger
    amount no document explains is its own "Journal entries and other" row."""
    as_of = as_of or date.today()
    jobs = select_jobs(
        db, job_id, customer_id, status, include_closed=True, as_of=as_of
    )
    # The jobless bucket (a journal posted to 1150/2050 with no job) is only
    # part of an unfiltered run, so a run for one job or customer ties to the
    # ledger for that job alone.
    whole_company = job_id is None and customer_id is None and not status

    owner = _owner_side(db, as_of)
    subs = _sub_side(db, as_of)
    ledger_recv = _ledger_by_job(db, RECEIVABLE_ACCOUNT, as_of, True)
    ledger_pay = _ledger_by_job(db, PAYABLE_ACCOUNT, as_of, False)

    # (id, name, customer name) of every job the report lists
    listed: list[tuple[Optional[int], str, str]] = [
        (j.id, j.name, _customer_name(j)) for j in jobs
    ]
    if whole_company:
        listed.append((None, "No job", ""))

    receivable, payable, out_jobs = [], [], []
    g_recv = {"held": _ZERO, "released": _ZERO}
    g_pay = {"held": _ZERO, "released": _ZERO}
    for jid, jname, cname in listed:
        o = owner.get(jid, {"held": _ZERO, "released": _ZERO})
        r_held, r_rel = _q(o["held"]), _q(o["released"])
        book_recv = ledger_recv.get(jid, _ZERO)
        other_recv = _q(book_recv - (r_held - r_rel))
        recv_rows = []
        if r_held or r_rel:
            recv_rows.append(
                {
                    "job_id": jid,
                    "job_name": jname,
                    "customer_name": cname,
                    "kind": "invoices",
                    **_figures(r_held, r_rel),
                }
            )
        if other_recv != _ZERO:
            recv_rows.append(_other_row(jid, jname, other_recv, customer_name=cname))
            r_held += max(other_recv, _ZERO)
            r_rel += max(-other_recv, _ZERO)

        sub_rows = []
        for _key, f in sorted(
            ((k, v) for k, v in subs.items() if k[0] == jid),
            key=lambda kv: (kv[1]["vendor_name"].lower(), kv[1]["document"]),
        ):
            row = {
                "job_id": jid,
                "job_name": jname,
                "vendor_id": f["vendor_id"],
                "vendor_name": f["vendor_name"],
                "document": f["document"],
                "kind": f["kind"],
                **_figures(f["held"], f["released"]),
            }
            if row["held"] or row["released"]:
                sub_rows.append(row)
        s_held = sum((Decimal(str(r["held"])) for r in sub_rows), _ZERO)
        s_rel = sum((Decimal(str(r["released"])) for r in sub_rows), _ZERO)
        book_pay = ledger_pay.get(jid, _ZERO)
        other_pay = _q(book_pay - (s_held - s_rel))
        if other_pay != _ZERO:
            sub_rows.append(_other_row(jid, jname, other_pay, vendor=True))
            s_held += max(other_pay, _ZERO)
            s_rel += max(-other_pay, _ZERO)

        if not recv_rows and not sub_rows:
            continue
        receivable.extend(recv_rows)
        payable.extend(sub_rows)
        out_jobs.append(
            {
                "job_id": jid,
                "job_name": jname,
                "customer_name": cname,
                "receivable": _figures(r_held, r_rel),
                "payable": _figures(s_held, s_rel),
            }
        )
        g_recv["held"] += r_held
        g_recv["released"] += r_rel
        g_pay["held"] += s_held
        g_pay["released"] += s_rel

    recv_total = _figures(g_recv["held"], g_recv["released"])
    pay_total = _figures(g_pay["held"], g_pay["released"])
    # The ledger lines the totals are set beside: the whole of 1150 and 2050
    # for the date. They are the same figures when nothing is filtered out.
    books = {
        "receivable": float(_q(sum(ledger_recv.values(), _ZERO))),
        "payable": float(_q(sum(ledger_pay.values(), _ZERO))),
    }
    return {
        "as_of": as_of.isoformat(),
        "jobs": out_jobs,
        "receivable": {
            "columns": RECEIVABLE_COLUMNS,
            "rows": receivable,
            "totals": recv_total,
        },
        "payable": {
            "columns": PAYABLE_COLUMNS,
            "rows": payable,
            "totals": pay_total,
        },
        "totals": {
            "receivable": recv_total["outstanding"],
            "payable": pay_total["outstanding"],
        },
        "books": books,
        "filtered": not whole_company,
    }


def _other_row(jid, jname, amount: Decimal, customer_name="", vendor=False) -> dict:
    """The ledger amount no document explains, as a row of its own."""
    row = {
        "job_id": jid,
        "job_name": jname,
        "kind": "other",
        **_figures(max(amount, _ZERO), max(-amount, _ZERO)),
    }
    if vendor:
        row.update(vendor_id=None, vendor_name="", document=_OTHER)
    else:
        row.update(customer_name=customer_name or _OTHER)
    return row


def retainage_for_job(db: Session, job_id: int, as_of: Optional[date] = None) -> dict:
    """One job's retainage tiles: owed to the contractor (1150) and owed by it
    (2050) as of a date, the figures the job Overview shows."""
    data = retainage_report(db, as_of, job_id=job_id)
    job = next((j for j in data["jobs"] if j["job_id"] == job_id), None)
    zero = _figures(_ZERO, _ZERO)
    return {
        "as_of": data["as_of"],
        "receivable": job["receivable"] if job else zero,
        "payable": job["payable"] if job else zero,
    }
