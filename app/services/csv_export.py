# ============================================================================
# CSV Export Service — export entities to CSV format
# Feature 14: Uses Python stdlib csv module
# ============================================================================

import csv
from decimal import Decimal
import io

from sqlalchemy.orm import Session

from app.models.contacts import Customer, Vendor
from app.models.items import Item
from app.models.invoices import Invoice
from app.models.accounts import Account

_FORMULA_LEADS = ("=", "+", "-", "@", "\t", "\r")


def _rate_text(value) -> str:
    """A unit price as a person writes it: at least two places, and the
    third and fourth only when they carry a digit — 12.50, 0.045 — now that
    line rates are stored to four places (a $12.50 rate read "12.5000")."""
    if value is None:
        return ""
    d = Decimal(str(value))
    text = format(d.quantize(Decimal("0.0001")), "f")
    whole, _, frac = text.partition(".")
    frac = frac.rstrip("0")
    return f"{whole}.{frac.ljust(2, '0')}"


def _csv_safe(value: str) -> str:
    """Neutralize spreadsheet formula injection. A cell beginning with
    =, +, -, @, TAB, or CR is treated as a formula by Excel/Sheets; prefixing
    with an apostrophe forces plain text without changing the displayed value.
    A customer named `=HYPERLINK(...)` otherwise executes on open."""
    if value and value[0] in _FORMULA_LEADS:
        return "'" + value
    return value


def strip_formula_guard(value):
    """The importers' half of _csv_safe: take off the apostrophe the export
    put in front of a formula-shaped value, so re-importing our own file
    gives back `=HYPERLINK(...)` rather than a second customer named
    `'=HYPERLINK(...)` (2.17.3 exploratory test, W-M15). Only an apostrophe
    followed by one of the guarded characters is removed; any other value,
    and anything that is not text, passes through untouched."""
    if (
        isinstance(value, str)
        and len(value) > 1
        and value[0] == "'"
        and value[1] in _FORMULA_LEADS
    ):
        return value[1:]
    return value


class _SafeWriter:
    """csv.writer wrapper that runs every STRING cell through _csv_safe.
    Numbers / None / dates pass through untouched so output formatting and
    types are preserved (only text cells can carry an injection payload)."""

    def __init__(self, fileobj):
        self._w = csv.writer(fileobj)

    def writerow(self, row):
        self._w.writerow([_csv_safe(c) if isinstance(c, str) else c for c in row])


def export_customers(db: Session) -> str:
    from app.services.contact_balances import customer_balances

    customers = db.query(Customer).filter(Customer.is_active).all()
    # Customer.balance is never written; the balance is summed from the
    # open documents (services/contact_balances).
    balances = customer_balances(db, [c.id for c in customers])
    output = io.StringIO()
    writer = _SafeWriter(output)
    writer.writerow(
        [
            "ID",
            "Name",
            "Company",
            "Email",
            "Phone",
            "Address",
            "City",
            "State",
            "ZIP",
            "Terms",
            "Balance",
        ]
    )
    for c in customers:
        writer.writerow(
            [
                c.id,
                c.name,
                c.company or "",
                c.email or "",
                c.phone or "",
                c.bill_address1 or "",
                c.bill_city or "",
                c.bill_state or "",
                c.bill_zip or "",
                c.terms or "",
                float(balances.get(c.id, 0)),
            ]
        )
    return output.getvalue()


def export_vendors(db: Session) -> str:
    from app.services.contact_balances import vendor_balances

    vendors = db.query(Vendor).filter(Vendor.is_active).all()
    balances = vendor_balances(db, [v.id for v in vendors])
    output = io.StringIO()
    writer = _SafeWriter(output)
    writer.writerow(
        [
            "ID",
            "Name",
            "Company",
            "Email",
            "Phone",
            "Address",
            "City",
            "State",
            "ZIP",
            "Terms",
            "Balance",
        ]
    )
    for v in vendors:
        writer.writerow(
            [
                v.id,
                v.name,
                v.company or "",
                v.email or "",
                v.phone or "",
                v.address1 or "",
                v.city or "",
                v.state or "",
                v.zip or "",
                v.terms or "",
                float(balances.get(v.id, 0)),
            ]
        )
    return output.getvalue()


def export_items(db: Session) -> str:
    items = db.query(Item).filter(Item.is_active).all()
    output = io.StringIO()
    writer = _SafeWriter(output)
    writer.writerow(["ID", "Name", "Type", "Description", "Rate", "Cost", "Taxable"])
    for i in items:
        writer.writerow(
            [
                i.id,
                i.name,
                i.item_type.value,
                i.description or "",
                float(i.rate or 0),
                float(i.cost or 0),
                i.is_taxable,
            ]
        )
    return output.getvalue()


def export_invoices(db: Session, date_from=None, date_to=None) -> str:
    q = db.query(Invoice)
    if date_from:
        q = q.filter(Invoice.date >= date_from)
    if date_to:
        q = q.filter(Invoice.date <= date_to)
    invoices = q.order_by(Invoice.date).all()

    output = io.StringIO()
    writer = _SafeWriter(output)
    writer.writerow(
        [
            "Invoice #",
            "Customer",
            "Date",
            "Due Date",
            "Status",
            "Subtotal",
            "Tax",
            "Total",
            "Paid",
            "Balance",
        ]
    )
    for inv in invoices:
        writer.writerow(
            [
                inv.invoice_number,
                inv.customer.name if inv.customer else "",
                inv.date.isoformat(),
                inv.due_date.isoformat() if inv.due_date else "",
                inv.status.value,
                float(inv.subtotal),
                float(inv.tax_amount),
                float(inv.total),
                float(inv.amount_paid),
                float(inv.balance_due),
            ]
        )
    return output.getvalue()


def export_accounts(db: Session) -> str:
    accounts = db.query(Account).order_by(Account.account_number).all()
    output = io.StringIO()
    writer = _SafeWriter(output)
    writer.writerow(["Number", "Name", "Type", "Balance", "Active", "System"])
    for a in accounts:
        writer.writerow(
            [
                a.account_number or "",
                a.name,
                a.account_type.value,
                float(a.balance or 0),
                a.is_active,
                a.is_system,
            ]
        )
    return output.getvalue()


def _cls_name(db, class_id) -> str:
    if not class_id:
        return ""
    from app.models.classes import TxnClass

    row = db.get(TxnClass, class_id)
    return row.name if row else ""


def _job_name(db, job_id) -> str:
    if not job_id:
        return ""
    from app.models.jobs import Job

    row = db.get(Job, job_id)
    return row.full_name if row else ""


def export_classes(db: Session) -> str:
    from app.models.classes import TxnClass

    output = io.StringIO()
    writer = _SafeWriter(output)
    writer.writerow(["ID", "Name", "Archived"])
    for c in (
        db.query(TxnClass)
        .filter(TxnClass.is_system_default.is_(False))
        .order_by(TxnClass.name)
        .all()
    ):
        writer.writerow([c.id, c.name, "Y" if c.is_archived else "N"])
    return output.getvalue()


def export_jobs(db: Session) -> str:
    from app.models.jobs import Job

    output = io.StringIO()
    writer = _SafeWriter(output)
    writer.writerow(
        [
            "ID",
            "Customer",
            "Job",
            "Customer:Job",
            "Job #",
            "Status",
            "Type",
            "Start",
            "Projected End",
            "Contract Amount",
            "Active",
        ]
    )
    for j in db.query(Job).order_by(Job.customer_id, Job.name).all():
        cust = j.customer.name if j.customer else ""
        writer.writerow(
            [
                j.id,
                cust,
                j.name,
                f"{cust}:{j.name}",
                j.job_number or "",
                j.status,
                j.job_type or "",
                j.start_date or "",
                j.projected_end_date or "",
                j.contract_amount or "",
                "Y" if j.is_active else "N",
            ]
        )
    return output.getvalue()


def export_bills(db: Session, date_from=None, date_to=None) -> str:
    from app.models.bills import Bill, BillStatus

    q = db.query(Bill).filter(Bill.status != BillStatus.VOID)
    if date_from:
        q = q.filter(Bill.date >= date_from)
    if date_to:
        q = q.filter(Bill.date <= date_to)
    output = io.StringIO()
    writer = _SafeWriter(output)
    writer.writerow(
        [
            "Bill #",
            "Vendor",
            "Date",
            "Due",
            "Terms",
            "Status",
            "Class",
            "Job",
            "Line",
            "Account",
            "Cost Code",
            "Description",
            "Qty",
            "Rate",
            "Amount",
            "Subtotal",
            "Tax",
            "Total",
            "Balance Due",
        ]
    )
    for b in q.order_by(Bill.date, Bill.id).all():
        vendor = b.vendor.name if b.vendor else ""
        cls = _cls_name(db, b.class_id)
        job = _job_name(db, b.job_id)
        for i, ln in enumerate(b.lines, 1):
            writer.writerow(
                [
                    b.bill_number,
                    vendor,
                    b.date,
                    b.due_date or "",
                    b.terms or "",
                    b.status.value,
                    cls,
                    (_job_name(db, ln.job_id) or job),
                    i,
                    ln.account.name if ln.account else "",
                    ln.cost_code.label if getattr(ln, "cost_code", None) else "",
                    ln.description or "",
                    ln.quantity,
                    _rate_text(ln.rate),
                    ln.amount,
                    b.subtotal,
                    b.tax_amount,
                    b.total,
                    b.balance_due,
                ]
            )
    return output.getvalue()


def export_deposits(db: Session, date_from=None, date_to=None) -> str:
    from app.models.transactions import Transaction

    q = db.query(Transaction).filter(Transaction.source_type == "deposit")
    if date_from:
        q = q.filter(Transaction.date >= date_from)
    if date_to:
        q = q.filter(Transaction.date <= date_to)
    output = io.StringIO()
    writer = _SafeWriter(output)
    writer.writerow(
        [
            "Deposit ID",
            "Date",
            "Reference",
            "Memo",
            "Bank Account",
            "Line",
            "From Account",
            "Description",
            "Amount",
        ]
    )
    for t in q.order_by(Transaction.date, Transaction.id).all():
        bank = next((ln for ln in t.lines if ln.debit and ln.debit > 0), None)
        bank_name = bank.account.name if bank and bank.account else ""
        for i, ln in enumerate(
            [ln for ln in t.lines if ln.credit and ln.credit > 0], 1
        ):
            writer.writerow(
                [
                    t.id,
                    t.date,
                    t.reference or "",
                    t.description or "",
                    bank_name,
                    i,
                    ln.account.name if ln.account else "",
                    ln.description or "",
                    ln.credit,
                ]
            )
    return output.getvalue()


def export_sales_receipts(db: Session, date_from=None, date_to=None) -> str:
    from app.models.invoices import Invoice, InvoiceStatus

    q = db.query(Invoice).filter(
        Invoice.is_sales_receipt.is_(True), Invoice.status != InvoiceStatus.VOID
    )
    if date_from:
        q = q.filter(Invoice.date >= date_from)
    if date_to:
        q = q.filter(Invoice.date <= date_to)
    output = io.StringIO()
    writer = _SafeWriter(output)
    writer.writerow(
        [
            "Receipt #",
            "Customer",
            "Date",
            "Class",
            "Job",
            "Line",
            "Item",
            "Description",
            "Qty",
            "Rate",
            "Taxable",
            "Amount",
            "Subtotal",
            "Tax",
            "Total",
        ]
    )
    for inv in q.order_by(Invoice.date, Invoice.id).all():
        cust = inv.customer.name if inv.customer else ""
        cls = _cls_name(db, inv.class_id)
        job = _job_name(db, inv.job_id)
        for i, ln in enumerate(inv.lines, 1):
            writer.writerow(
                [
                    inv.invoice_number,
                    cust,
                    inv.date,
                    cls,
                    job,
                    i,
                    ln.item.name if ln.item else "",
                    ln.description or "",
                    ln.quantity,
                    _rate_text(ln.rate),
                    "Y" if ln.is_taxable else "N",
                    ln.amount,
                    inv.subtotal,
                    inv.tax_amount,
                    inv.total,
                ]
            )
    return output.getvalue()
