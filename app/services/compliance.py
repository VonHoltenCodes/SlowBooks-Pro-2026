# ============================================================================
# Sub vendor compliance (#553) — the paperwork flags the bills list, Pay Bills
# and the job Buyout tab read. Warn-only: nothing here blocks a payment.
# ============================================================================

from datetime import date as _date


def vendor_compliance(vendor, today: _date | None = None) -> dict:
    """What a Sub vendor's paperwork looks like at pay time: whether a W-9 is
    on file and whether the certificate of insurance is in hand. coi_expired
    is true for a Sub whose COI is past OR missing; coi_expires is an ISO
    string (or null) so the shape is the same for JSON and for the bills
    schema. A non-Sub vendor is never flagged."""
    today = today or _date.today()
    is_sub = bool(vendor and vendor.vendor_type == "sub")
    coi = vendor.coi_expires if vendor else None
    coi_expired = is_sub and (coi is None or today > coi)
    return {
        "is_sub": is_sub,
        "w9_on_file": bool(vendor and vendor.w9_on_file),
        "coi_expires": coi.isoformat() if coi else None,
        "coi_expired": coi_expired,
    }


# ── The Compliance report ────────────────────────────────────────────────

COMPLIANCE_WINDOW_DAYS = 30

COMPLIANCE_COLUMNS = [
    {"key": "vendor_name", "label": "Sub", "kind": "text"},
    {"key": "issue", "label": "Issue", "kind": "text"},
    {"key": "detail", "label": "Detail", "kind": "text"},
    {"key": "date", "label": "Date", "kind": "text"},
    {"key": "amount", "label": "Amount", "kind": "money"},
]

# issue key → the words the report prints
COMPLIANCE_ISSUES = {
    "coi_expired": "COI expired",
    "coi_expiring": "COI expiring",
    "coi_missing": "No COI on file",
    "no_w9": "No W-9 on file",
    "lien_waiver": "Lien waiver missing on a paid bill",
}


def compliance_report(db, as_of: _date | None = None) -> dict:
    """The subs a GC should chase, as of a date: a certificate of insurance
    expired or expiring within 30 days (or with no date at all), no W-9 on
    file, and a paid bill whose lien waiver is missing or still pending.
    Nothing here blocks a payment; it is the list to work through."""
    from datetime import timedelta

    from sqlalchemy import func

    from app.models.bills import (
        Bill,
        BillPayment,
        BillPaymentAllocation,
        BillStatus,
    )
    from app.models.contacts import Vendor

    as_of = as_of or _date.today()
    horizon = as_of + timedelta(days=COMPLIANCE_WINDOW_DAYS)
    subs = (
        db.query(Vendor)
        .filter(Vendor.vendor_type == "sub", Vendor.is_active.is_(True))
        .order_by(Vendor.name)
        .all()
    )
    rows = []

    def add(vendor, issue, detail="", when=None, amount=None):
        rows.append(
            {
                "vendor_id": vendor.id,
                "vendor_name": vendor.name,
                "issue": COMPLIANCE_ISSUES[issue],
                "issue_key": issue,
                "detail": detail,
                "date": when.isoformat() if when else "",
                "amount": float(amount) if amount is not None else None,
            }
        )

    for v in subs:
        coi = v.coi_expires
        if coi is None:
            add(v, "coi_missing", "No certificate of insurance date")
        elif coi < as_of:
            add(v, "coi_expired", f"Expired {(as_of - coi).days} days ago", coi)
        elif coi <= horizon:
            days = (coi - as_of).days
            add(v, "coi_expiring", f"Expires in {days} days", coi)
        if not v.w9_on_file:
            add(v, "no_w9", "W-9 not on file")

    # the first payment on each bill, by the date, to say when it was paid
    first_paid = dict(
        db.query(BillPaymentAllocation.bill_id, func.min(BillPayment.date))
        .join(BillPayment, BillPaymentAllocation.bill_payment_id == BillPayment.id)
        .filter(BillPayment.is_voided.is_(False), BillPayment.date <= as_of)
        .group_by(BillPaymentAllocation.bill_id)
        .all()
    )
    sub_ids = {v.id for v in subs}
    if first_paid and sub_ids:
        vendors = {v.id: v for v in subs}
        bills = (
            db.query(Bill)
            .filter(
                Bill.id.in_(list(first_paid)),
                Bill.vendor_id.in_(sub_ids),
                Bill.status != BillStatus.VOID,
                Bill.date <= as_of,
            )
            .order_by(Bill.date, Bill.id)
            .all()
        )
        for b in bills:
            if b.lien_waiver_status not in (None, "pending"):
                continue
            state = "pending" if b.lien_waiver_status == "pending" else "none"
            job = b.job.name if getattr(b, "job", None) else ""
            add(
                vendors[b.vendor_id],
                "lien_waiver",
                f"Bill {b.bill_number}"
                + (f" ({job})" if job else "")
                + f", waiver {state}",
                first_paid[b.id],
                b.total,
            )

    order = {k: i for i, k in enumerate(COMPLIANCE_ISSUES)}
    rows.sort(
        key=lambda r: (r["vendor_name"].lower(), order[r["issue_key"]], r["date"])
    )
    counts = {k: sum(1 for r in rows if r["issue_key"] == k) for k in COMPLIANCE_ISSUES}
    return {
        "as_of": as_of.isoformat(),
        "window_days": COMPLIANCE_WINDOW_DAYS,
        "rows": rows,
        "columns": COMPLIANCE_COLUMNS,
        "totals": {
            **counts,
            "subs": len({r["vendor_id"] for r in rows}),
            "rows": len(rows),
        },
    }
