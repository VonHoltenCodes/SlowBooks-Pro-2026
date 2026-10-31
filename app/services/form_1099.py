# ============================================================================
# Form 1099-NEC / 1096 Service — contractor tax-form generation
# ----------------------------------------------------------------------------
# Sums vendor payments (bill_payments) per calendar year and produces:
#   - 1099-NEC data / PDF for each eligible vendor
#   - 1096 transmittal summary / PDF aggregating the NEC forms
#
# A vendor crosses the IRS 1099-NEC reporting threshold at $600 of payments
# in a calendar year through 2025, and at $2,000 from 2026 (the One Big
# Beautiful Bill Act, section 70433, for payments made after December 31,
# 2025; indexed for inflation after 2026). Vendor payments are recorded as
# BillPayment rows (vendor_id, date, amount) and register entries naming the
# vendor.
#
# DISCLAIMER: IRS thresholds and form layouts change. Verify against the
# current-year IRS instructions before filing.
# ============================================================================

from datetime import date
from decimal import Decimal

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models.accounts import AccountType
from app.models.contacts import Vendor
from app.models.bills import BillPayment
from app.models.transactions import Transaction
from app.services.bank_register import voided_transaction_ids
from app.services.accounting import _q
from app.services.pdf_service import _jinja_env, render_pdf

# IRS 1099-NEC reporting threshold for nonemployee compensation, by the
# year the payments were made.
NEC_THRESHOLD = Decimal("600.00")
NEC_THRESHOLD_2026 = Decimal("2000.00")


def nec_threshold(year: int) -> Decimal:
    """The reporting threshold for payments made in `year`: $600 through
    2025, $2,000 from 2026 (2.31.3). The 2027+ inflation adjustment is
    published by the IRS each fall; until it is, the 2026 figure stands."""
    return NEC_THRESHOLD_2026 if year >= 2026 else NEC_THRESHOLD


def is_1099_vendor():
    """Vendors flagged for 1099 reporting — one rule for the 1099 Summary
    report and the 1099-NEC / 1096. The vendor form's "1099 Vendor: Yes"
    sets is_1099_vendor; this service read is_1099_eligible, which nothing
    sets, so the 1099-NEC listed no vendors (2.17.3, macbase1 F19). The old
    column is still read, so a vendor flagged through it is not dropped."""
    return or_(Vendor.is_1099_vendor.is_(True), Vendor.is_1099_eligible.is_(True))


def clear_type_unless_1099(vendor: Vendor) -> None:
    """A 1099 type describes a 1099 vendor; on any other vendor it is
    cleared rather than kept as a type the forms would never use."""
    if not vendor.is_1099_vendor:
        vendor.vendor_1099_type = None


def _files_nec(vendor: Vendor) -> bool:
    """A 1099-NEC is for nonemployee compensation: NEC vendors, and 1099
    vendors with no type (the default the 1099 Summary shows). MISC, INT
    and DIV payments go on other forms."""
    return (vendor.vendor_1099_type or "NEC").strip().upper() == "NEC"


def _vendor_address(vendor: Vendor) -> str:
    """Format a vendor's mailing address as a single string."""
    parts = []
    if vendor.address1:
        parts.append(vendor.address1)
    if vendor.address2:
        parts.append(vendor.address2)
    locality = " ".join(
        p
        for p in [
            f"{vendor.city}," if vendor.city else "",
            vendor.state or "",
            vendor.zip or "",
        ]
        if p
    ).strip()
    if locality:
        parts.append(locality)
    return ", ".join(parts)


def paid_bill_payments(
    db: Session,
    start: date | None = None,
    end: date | None = None,
    vendor_id: int | None = None,
):
    """The one definition of a bill payment that counts as money paid to a
    vendor: not voided (#287), dated in the range. The 1099 totals below
    and a vendor's payment history (#569) are both built on it, so the
    history's bill-payment subtotal for a year is the vendor's 1099 figure.
    Returns a query, for callers to sum or list."""
    q = db.query(BillPayment).filter(BillPayment.is_voided.isnot(True))
    if start:
        q = q.filter(BillPayment.date >= start)
    if end:
        q = q.filter(BillPayment.date <= end)
    if vendor_id is not None:
        q = q.filter(BillPayment.vendor_id == vendor_id)
    return q


def register_entries(
    db: Session,
    start: date | None = None,
    end: date | None = None,
    vendor_id: int | None = None,
) -> list[tuple]:
    """The register entries that name a vendor (#590, and bank-feed lines
    added to one, #491), oldest first, as (transaction, amount paid, voided).
    Only a bank_entry carries a vendor, so a transfer is never here. The
    amount is what left the bank or card: money out positive, a refund
    back negative."""
    q = db.query(Transaction).filter(
        Transaction.vendor_id.isnot(None), Transaction.source_type == "bank_entry"
    )
    if vendor_id is not None:
        q = q.filter(Transaction.vendor_id == vendor_id)
    if start:
        q = q.filter(Transaction.date >= start)
    if end:
        q = q.filter(Transaction.date <= end)
    txns = q.order_by(Transaction.date, Transaction.id).all()
    voided = voided_transaction_ids(db, [t.id for t in txns])
    out = []
    for t in txns:
        bank = next((ln for ln in t.lines if ln.account and ln.account.bank_kind), None)
        if bank is None:
            continue
        paid = Decimal(str(bank.credit or 0)) - Decimal(str(bank.debit or 0))
        out.append((t, paid, t.id in voided))
    return out


def reportable_amount(t: Transaction) -> Decimal | None:
    """What a register entry adds to a vendor's 1099-NEC, or None when it
    adds nothing. Only an entry paid from a bank account counts: a card
    charge is reported by the card processor on a 1099-K. And only the
    entry's expense and cost-of-goods lines count, not the bank amount, so
    a payment to A/P, sales tax or a loan is not income to the vendor and
    a split counts its expense part. A refund nets off (a credit line)."""
    banks = [ln.account for ln in t.lines if ln.account and ln.account.bank_kind]
    if not banks or any(a.bank_kind == "credit_card" for a in banks):
        return None
    return sum(
        (
            Decimal(str(ln.debit or 0)) - Decimal(str(ln.credit or 0))
            for ln in t.lines
            if ln.account
            and ln.account.account_type in (AccountType.EXPENSE, AccountType.COGS)
        ),
        Decimal("0"),
    )


def register_payment_totals(db: Session, year: int) -> dict[int, Decimal]:
    """Sum the year's register entries per vendor (see reportable_amount),
    voided ones left out: the second source of money paid to a vendor
    beside bill payments."""
    totals: dict[int, Decimal] = {}
    for t, _paid, voided in register_entries(db, date(year, 1, 1), date(year, 12, 31)):
        amount = None if voided else reportable_amount(t)
        if amount is not None:
            totals[t.vendor_id] = totals.get(t.vendor_id, Decimal("0")) + amount
    return totals


def _vendor_payment_totals(db: Session, year: int) -> dict[int, Decimal]:
    """Sum payments per vendor for the given calendar year: bill payments
    plus register entries that name the vendor (#590)."""
    rows = (
        paid_bill_payments(db, date(year, 1, 1), date(year, 12, 31))
        .with_entities(BillPayment.vendor_id, func.sum(BillPayment.amount))
        .group_by(BillPayment.vendor_id)
        .all()
    )
    totals = {vid: _q(total) for vid, total in rows if vid is not None}
    for vid, paid in register_payment_totals(db, year).items():
        totals[vid] = _q(totals.get(vid, Decimal("0")) + paid)
    return totals


def compute_1099_data(db: Session, year: int) -> list[dict]:
    """Return 1099-NEC data for every 1099 vendor paid nonemployee
    compensation (see is_1099_vendor and _files_nec).

    One dict per eligible vendor with vendor id, name, address, tax_id, the
    total amount paid in `year`, whether they cross that year's reporting
    threshold (`reportable`, see nec_threshold), and whether a W-9 is on
    file.
    """
    totals = _vendor_payment_totals(db, year)
    threshold = nec_threshold(year)
    vendors = db.query(Vendor).filter(is_1099_vendor()).order_by(Vendor.name).all()

    results: list[dict] = []
    for vendor in filter(_files_nec, vendors):
        total = totals.get(vendor.id, Decimal("0.00"))
        results.append(
            {
                "vendor_id": vendor.id,
                "name": vendor.name,
                "company": vendor.company,
                "address": _vendor_address(vendor),
                "tax_id": vendor.tax_id,
                "total_paid": total,
                "reportable": total >= threshold,
                "w9_on_file": bool(vendor.w9_on_file),
                "year": year,
            }
        )
    return results


def compute_1096(db: Session, year: int) -> dict:
    """1096 transmittal summary: count and total of reportable 1099-NEC forms."""
    data = compute_1099_data(db, year)
    reportable = [d for d in data if d["reportable"]]
    total = sum((d["total_paid"] for d in reportable), Decimal("0.00"))
    return {
        "year": year,
        "form_count": len(reportable),
        "total_amount": _q(total),
    }


def generate_1099_nec_pdf(db: Session, year: int, vendor_id: int, payer: dict) -> bytes:
    """Render a 1099-NEC PDF for a single vendor.

    `payer` is a company-info dict (name / address / ein), read defensively.
    """
    record = next(
        (d for d in compute_1099_data(db, year) if d["vendor_id"] == vendor_id),
        None,
    )
    if record is None:
        raise ValueError(f"Vendor {vendor_id} is not 1099-eligible or does not exist")

    template = _jinja_env.get_template("form_1099nec.html")
    html_str = template.render(rec=record, payer=payer, year=year)
    return render_pdf(html_str)


def generate_1096_pdf(db: Session, year: int, payer: dict) -> bytes:
    """Render a 1096 transmittal-summary PDF for the given year."""
    summary = compute_1096(db, year)
    reportable = [d for d in compute_1099_data(db, year) if d["reportable"]]

    template = _jinja_env.get_template("form_1096.html")
    html_str = template.render(
        summary=summary,
        vendors=reportable,
        payer=payer,
        year=year,
    )
    return render_pdf(html_str)
