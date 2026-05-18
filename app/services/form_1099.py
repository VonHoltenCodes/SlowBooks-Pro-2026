# ============================================================================
# Form 1099-NEC / 1096 Service — contractor tax-form generation
# ----------------------------------------------------------------------------
# Sums vendor payments (bill_payments) per calendar year and produces:
#   - 1099-NEC data / PDF for each eligible vendor
#   - 1096 transmittal summary / PDF aggregating the NEC forms
#
# A vendor crosses the IRS 1099-NEC reporting threshold at $600 of payments
# in the calendar year. Vendor payments are recorded as BillPayment rows
# (vendor_id, date, amount).
#
# DISCLAIMER: IRS thresholds and form layouts change. Verify against the
# current-year IRS instructions before filing.
# ============================================================================

from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.contacts import Vendor
from app.models.bills import BillPayment
from app.services.pdf_service import _jinja_env, _safe_url_fetcher
from weasyprint import HTML

CENT = Decimal("0.01")

# IRS 1099-NEC reporting threshold for nonemployee compensation.
NEC_THRESHOLD = Decimal("600.00")


def _q(value) -> Decimal:
    """Quantize a money value to 2 decimal places."""
    if not isinstance(value, Decimal):
        value = Decimal(str(value or 0))
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


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


def _vendor_payment_totals(db: Session, year: int) -> dict[int, Decimal]:
    """Sum bill payments per vendor for the given calendar year."""
    start = date(year, 1, 1)
    end = date(year, 12, 31)
    rows = (
        db.query(BillPayment.vendor_id, func.sum(BillPayment.amount))
        .filter(BillPayment.date >= start, BillPayment.date <= end)
        .group_by(BillPayment.vendor_id)
        .all()
    )
    return {vid: _q(total) for vid, total in rows if vid is not None}


def compute_1099_data(db: Session, year: int) -> list[dict]:
    """Return 1099-NEC data for every 1099-eligible vendor.

    One dict per eligible vendor with vendor id, name, address, tax_id, the
    total amount paid in `year`, whether they cross the $600 reporting
    threshold (`reportable`), and whether a W-9 is on file.
    """
    totals = _vendor_payment_totals(db, year)
    vendors = (
        db.query(Vendor)
        .filter(Vendor.is_1099_eligible.is_(True))
        .order_by(Vendor.name)
        .all()
    )

    results: list[dict] = []
    for vendor in vendors:
        total = totals.get(vendor.id, Decimal("0.00"))
        results.append(
            {
                "vendor_id": vendor.id,
                "name": vendor.name,
                "company": vendor.company,
                "address": _vendor_address(vendor),
                "tax_id": vendor.tax_id,
                "total_paid": total,
                "reportable": total >= NEC_THRESHOLD,
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
    return HTML(string=html_str, url_fetcher=_safe_url_fetcher).write_pdf()


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
    return HTML(string=html_str, url_fetcher=_safe_url_fetcher).write_pdf()
