# ============================================================================
# Vendor payment history (#569) — what was paid to a vendor, in one list
# ----------------------------------------------------------------------------
# Three kinds of money out name a vendor:
#   - bill payments (BillPayment rows) — the rows the 1099 totals are
#     summed from (form_1099.paid_bill_payments: not voided, in the range)
#   - direct expenses (a Transaction of source_type "expense" whose
#     source_id is the vendor), voided by an "expense_void" reversal
#   - bank-feed entries that name the vendor (#491, Transaction.vendor_id)
# The 1099-NEC counts bill payments and the expense and cost-of-goods lines of
# register entries paid from a bank account (#590; form_1099's
# paid_bill_payments and register_payment_totals); direct expenses and card
# charges are left out. The history lists every kind and subtotals them, so
# the bank-entry subtotal can exceed what the 1099 counts of it.
# ============================================================================

from datetime import date
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.bills import BillPayment
from app.models.transactions import Transaction
from app.services.bank_register import source_link, voided_transaction_ids
from app.services.form_1099 import paid_bill_payments, register_entries

KINDS = ("bill_payment", "expense", "bank_entry")


def _money(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.01"))


def _bill_payment_rows(db: Session, vendor_id: int, start, end) -> list[dict]:
    """Every bill payment to the vendor in the range, voided ones marked
    so the list is honest about what was entered and taken back."""
    q = db.query(BillPayment).filter(BillPayment.vendor_id == vendor_id)
    if start:
        q = q.filter(BillPayment.date >= start)
    if end:
        q = q.filter(BillPayment.date <= end)
    from app.models.accounts import Account
    from app.services.check_numbers import _paying_account_id

    out = []
    for p in q.order_by(BillPayment.date, BillPayment.id).all():
        # Pay From left blank is paid from Checking, as the server posted
        # it, and says so here rather than a blank (#813)
        paid_from = p.pay_from_account
        if paid_from is None:
            default = _paying_account_id(db, p)
            paid_from = db.get(Account, default) if default else None
        out.append(
            {
                "kind": "bill_payment",
                "id": p.id,
                "date": p.date.isoformat(),
                "method": p.method or "",
                "reference": p.check_number or "",
                "paid_from": paid_from.name if paid_from else "",
                "memo": (p.notes or "").replace("\n", " "),
                "amount": str(_money(p.amount)),
                "is_voided": bool(p.is_voided),
                "link": f"/#/bill-payments/{p.id}",
            }
        )
    return out


def _expense_rows(db: Session, vendor_id: int, start, end) -> list[dict]:
    """Direct expenses entered for the vendor (Enter Expenses): the amount
    is what left the paid-from account, the memo its expense line's words."""
    q = db.query(Transaction).filter(
        Transaction.source_type == "expense", Transaction.source_id == vendor_id
    )
    if start:
        q = q.filter(Transaction.date >= start)
    if end:
        q = q.filter(Transaction.date <= end)
    txns = q.order_by(Transaction.date, Transaction.id).all()
    voided = voided_transaction_ids(db, [t.id for t in txns])
    out = []
    for t in txns:
        paid_from = next((ln for ln in t.lines if (ln.credit or 0) > 0), None)
        charged = next((ln for ln in t.lines if (ln.debit or 0) > 0), None)
        amount = sum((Decimal(str(ln.credit or 0)) for ln in t.lines), Decimal("0"))
        out.append(
            {
                "kind": "expense",
                "id": t.id,
                "date": t.date.isoformat(),
                "method": "Expense",
                "reference": t.reference or "",
                "paid_from": (
                    paid_from.account.name if paid_from and paid_from.account else ""
                ),
                "memo": ((charged.description if charged else "") or "").replace(
                    "\n", " "
                ),
                "amount": str(_money(amount)),
                "is_voided": t.id in voided,
                "link": source_link(t),
            }
        )
    return out


def bank_entries_for(
    db: Session,
    vendor_id: int,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[dict]:
    """The register entries that name this vendor (#491) — lines added
    from a bank feed — oldest first. `amount` is what was paid: money out
    of the bank or onto the card is positive, a refund back negative."""
    out = []
    for t, paid, voided in register_entries(db, start_date, end_date, vendor_id):
        bank = next(ln for ln in t.lines if ln.account and ln.account.bank_kind)
        memo = next((ln.description for ln in t.lines if ln.description), "") or ""
        out.append(
            {
                "transaction_id": t.id,
                "date": t.date.isoformat(),
                "reference": t.reference or "",
                "memo": memo,
                "account_id": bank.account_id,
                "account_name": bank.account.name,
                "amount": str(paid),
                "is_voided": voided,
                "link": source_link(t),
            }
        )
    return out


def _bank_entry_rows(db: Session, vendor_id: int, start, end) -> list[dict]:
    return [
        {
            "kind": "bank_entry",
            "id": e["transaction_id"],
            "date": e["date"],
            "method": "Bank entry",
            "reference": e["reference"],
            "paid_from": e["account_name"],
            "memo": e["memo"].replace("\n", " "),
            "amount": str(_money(e["amount"])),
            "is_voided": e["is_voided"],
            "link": e["link"],
        }
        for e in bank_entries_for(db, vendor_id, start, end)
    ]


def vendor_payment_history(
    db: Session,
    vendor_id: int,
    start: date | None = None,
    end: date | None = None,
) -> dict:
    """Every payment to the vendor in the range, oldest first, each row with
    the running total so far; voided rows are listed, marked, and left out
    of the totals. `totals` is by kind — the bill-payment figure summed by
    the 1099 query itself, and the bank-entry figure from the 1099's own
    register source, so the two together are the vendor's 1099 total for a year —
    and `total` the sum of the three."""
    rows = (
        _bill_payment_rows(db, vendor_id, start, end)
        + _expense_rows(db, vendor_id, start, end)
        + _bank_entry_rows(db, vendor_id, start, end)
    )
    rows.sort(key=lambda r: (r["date"], KINDS.index(r["kind"]), r["id"]))
    running = Decimal("0.00")
    totals = {kind: Decimal("0.00") for kind in KINDS}
    for r in rows:
        if not r["is_voided"]:
            running += Decimal(r["amount"])
            totals[r["kind"]] += Decimal(r["amount"])
        r["running_total"] = str(running)
    paid = (
        paid_bill_payments(db, start, end, vendor_id)
        .with_entities(func.sum(BillPayment.amount))
        .scalar()
    )
    totals["bill_payment"] = _money(paid)
    return {
        "vendor_id": vendor_id,
        "start_date": start.isoformat() if start else None,
        "end_date": end.isoformat() if end else None,
        "rows": rows,
        "totals": {kind: str(totals[kind]) for kind in KINDS},
        "total": str(running),
    }
