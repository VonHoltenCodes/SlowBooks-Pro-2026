# ============================================================================
# QuickBooks report-CSV import — sales receipts from a "Transaction Detail
# by Date" export.
#
# QuickBooks Desktop can't export transactions to IIF natively, so the
# documented fallback (docs/migrate-from-quickbooks.md) is a Transaction
# Detail report filtered to Sales Receipt, exported to CSV/Excel. This
# module is the landing zone for that file: each receipt becomes an
# Invoice flagged is_sales_receipt plus a Payment for the full total —
# the same document pair the IIF CASH SALE importer produces.
#
# Shaped by a real customer export (an RV dealer's receipts), which is
# why the parser is sign-aware: applied-deposit contra lines ("less
# deposit", negative Sales Price, Debit side) legitimately reduce a
# receipt's total, and the IIF importer's abs()-based parse would
# inflate them. Tax rows carry a percentage in Sales Price ("6.4%") and
# the agency's name in Name — they belong to the receipt, not to a
# customer.
# ============================================================================

import csv
import io
from datetime import datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from app.models.accounts import Account
from app.models.contacts import Customer
from app.models.invoices import Invoice, InvoiceLine, InvoiceStatus
from app.models.items import Item
from app.models.payments import Payment, PaymentAllocation
from app.services.accounting import (
    _q,
    create_journal_entry,
    get_ar_account_id,
    get_default_income_account_id,
    get_undeposited_funds_id,
)
from app.services.iif_import import _find_account

# Columns the parser needs to see in the report's header row. Memo, Item,
# Item Description, and Qty are used when present but not required.
REQUIRED_COLUMNS = ("Date", "Name", "Account", "Split", "Debit", "Credit")


def _amount(s) -> Decimal:
    """Parse a QB report amount: thousands separators, blanks, negatives."""
    s = (s or "").strip().replace(",", "").replace("$", "")
    if not s:
        return Decimal("0")
    try:
        return Decimal(s)
    except InvalidOperation:
        return Decimal("0")


def _parse_date(s):
    s = (s or "").strip()
    for fmt in ("%m/%d/%y", "%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _parse_tax_rate(price: str) -> Decimal:
    """'6.4%' -> Decimal('0.064'); anything else -> 0."""
    price = (price or "").strip()
    if not price.endswith("%"):
        return Decimal("0")
    try:
        return Decimal(price[:-1]) / Decimal("100")
    except InvalidOperation:
        return Decimal("0")


def _find_header(rows: list[list[str]]):
    """Locate the report's column-header row and map names to indexes."""
    for i, row in enumerate(rows):
        cells = [c.strip() for c in row]
        if all(col in cells for col in REQUIRED_COLUMNS):
            return i, {name: cells.index(name) for name in cells if name}
    return None, None


def _cell(row: list[str], cols: dict, name: str) -> str:
    idx = cols.get(name)
    if idx is None or idx >= len(row):
        return ""
    return (row[idx] or "").strip()


def _group_receipts(rows, cols, start, errors):
    """Group data rows into receipts.

    A row whose Split column is "-SPLIT-" is a receipt header: it carries
    the customer, date, document number, deposit account, and the total
    (Debit side). Rows that follow belong to that receipt: a percentage
    in Sales Price marks a tax row; everything else is a line item with
    a signed amount of Credit − Debit.
    """
    receipts = []
    current = None
    for n, row in enumerate(rows[start + 1 :], start=start + 2):
        if not _parse_date(_cell(row, cols, "Date")):
            continue  # report group headers/footers/blank rails
        if _cell(row, cols, "Split") == "-SPLIT-":
            current = {
                "row": n,
                "num": _cell(row, cols, "Num"),
                "date": _parse_date(_cell(row, cols, "Date")),
                "customer": _cell(row, cols, "Name"),
                "deposit_account": _cell(row, cols, "Account"),
                "total": _q(_amount(_cell(row, cols, "Debit"))),
                "lines": [],
                "tax_amount": Decimal("0"),
                "tax_rate": Decimal("0"),
            }
            receipts.append(current)
            continue
        if current is None:
            errors.append(f"Row {n}: detail line before any receipt header; skipped")
            continue
        price = _cell(row, cols, "Sales Price")
        signed = _q(
            _amount(_cell(row, cols, "Credit")) - _amount(_cell(row, cols, "Debit"))
        )
        if price.endswith("%"):
            current["tax_amount"] += signed
            rate = _parse_tax_rate(price)
            if rate and not current["tax_rate"]:
                current["tax_rate"] = rate
            continue
        qty = abs(_amount(_cell(row, cols, "Qty"))) or Decimal("1")
        current["lines"].append(
            {
                "description": _cell(row, cols, "Memo")
                or _cell(row, cols, "Item Description")
                or _cell(row, cols, "Item"),
                "item_name": _cell(row, cols, "Item"),
                "account": _cell(row, cols, "Account"),
                "quantity": qty,
                "amount": signed,
            }
        )
    return receipts


def _assign_number(db: Session, num: str) -> str:
    """Use the report's Num when it's free; otherwise SR-prefix and bump.

    QB sales receipts run their own sequence (often 1, 2, 3 …), which can
    collide with existing invoice numbers here.
    """
    from app.services.numbering import next_invoice_number

    if not num:
        return next_invoice_number(db)
    candidate = num
    if (
        db.query(Invoice.id).filter(Invoice.invoice_number == candidate).first()
        is not None
    ):
        n = 0
        candidate = f"SR-{num}"
        while (
            db.query(Invoice.id).filter(Invoice.invoice_number == candidate).first()
            is not None
        ):
            n += 1
            candidate = f"SR-{num}-{n}"
    return candidate


def import_sales_receipt_report(db: Session, csv_text: str) -> dict:
    """Import a Transaction Detail by Date CSV (filtered to Sales Receipt)."""
    result = {
        "imported": 0,
        "duplicates_skipped": 0,
        "errors": [],
        "warnings": [],
    }
    rows = list(csv.reader(io.StringIO(csv_text)))
    header_idx, cols = _find_header(rows)
    if header_idx is None:
        result["errors"].append(
            "Could not find the report's column header row. Export the "
            "Transaction Detail report with at least these columns: "
            + ", ".join(REQUIRED_COLUMNS)
        )
        return result

    receipts = _group_receipts(rows, cols, header_idx, result["errors"])
    ar_id = get_ar_account_id(db)
    default_income_id = get_default_income_account_id(db)

    for rec in receipts:
        sp = db.begin_nested()
        try:
            ref = rec["num"] or f"row {rec['row']}"
            line_sum = sum((ln["amount"] for ln in rec["lines"]), Decimal("0"))
            expected = _q(line_sum + rec["tax_amount"])
            if expected != rec["total"]:
                sp.rollback()
                result["errors"].append(
                    f"Receipt {ref}: lines + tax ({expected}) do not equal the "
                    f"deposit total ({rec['total']}); skipped"
                )
                continue

            cust_name = (rec["customer"] or "Walk-In Customer")[:200]
            customer = db.query(Customer).filter(Customer.name == cust_name).first()
            if not customer:
                customer = Customer(name=cust_name, is_active=True)
                db.add(customer)
                db.flush()

            # Dedup: same customer, date, and total among sales receipts —
            # the report can be re-exported and re-uploaded freely.
            existing = (
                db.query(Invoice)
                .filter(
                    Invoice.customer_id == customer.id,
                    Invoice.date == rec["date"],
                    Invoice.total == rec["total"],
                    Invoice.is_sales_receipt.is_(True),
                )
                .first()
            )
            if existing:
                sp.rollback()
                result["duplicates_skipped"] += 1
                continue

            subtotal = _q(line_sum)
            invoice = Invoice(
                invoice_number=_assign_number(db, rec["num"]),
                customer_id=customer.id,
                date=rec["date"],
                due_date=rec["date"],
                terms="Due on Receipt",
                status=InvoiceStatus.PAID,
                is_sales_receipt=True,
                subtotal=subtotal,
                tax_rate=rec["tax_rate"] if rec["tax_amount"] else Decimal("0"),
                tax_amount=_q(rec["tax_amount"]),
                total=rec["total"],
                amount_paid=rec["total"],
                balance_due=Decimal("0"),
            )
            db.add(invoice)
            db.flush()

            for order, ln in enumerate(rec["lines"]):
                item_id = None
                if ln["item_name"]:
                    item = db.query(Item).filter(Item.name == ln["item_name"]).first()
                    if item:
                        item_id = item.id
                qty = ln["quantity"]
                db.add(
                    InvoiceLine(
                        invoice_id=invoice.id,
                        item_id=item_id,
                        description=ln["description"] or None,
                        quantity=qty,
                        rate=_q(ln["amount"] / qty) if qty else ln["amount"],
                        amount=ln["amount"],
                        line_order=order,
                    )
                )

            # Invoice journal — sign-aware: DR A/R for the total; each
            # positive line credits its account, a contra line (applied
            # deposit, discount) debits it; tax credits its account.
            if ar_id and rec["total"] != 0:
                journal_lines = [
                    {
                        "account_id": ar_id,
                        "debit": rec["total"],
                        "credit": Decimal("0"),
                        "description": f"Sales receipt {invoice.invoice_number}",
                    }
                ]
                unmatched = []
                for ln in rec["lines"]:
                    if ln["amount"] == 0:
                        continue
                    acct = _find_account(db, ln["account"])
                    acct_id = acct.id if acct else default_income_id
                    if not acct:
                        unmatched.append(ln["account"] or "(blank)")
                    if acct_id is None:
                        continue
                    journal_lines.append(
                        {
                            "account_id": acct_id,
                            "debit": (
                                -ln["amount"] if ln["amount"] < 0 else Decimal("0")
                            ),
                            "credit": (
                                ln["amount"] if ln["amount"] > 0 else Decimal("0")
                            ),
                            "description": ln["description"] or "",
                        }
                    )
                if rec["tax_amount"]:
                    from app.services.accounting import get_sales_tax_account_id

                    tax_id = get_sales_tax_account_id(db)
                    if tax_id:
                        journal_lines.append(
                            {
                                "account_id": tax_id,
                                "debit": Decimal("0"),
                                "credit": rec["tax_amount"],
                                "description": "Sales tax",
                            }
                        )
                total_dr = sum(jl["debit"] for jl in journal_lines)
                total_cr = sum(jl["credit"] for jl in journal_lines)
                if total_dr == total_cr and total_dr > 0:
                    txn = create_journal_entry(
                        db,
                        invoice.date,
                        f"QB report import — Sales receipt {invoice.invoice_number}",
                        journal_lines,
                        source_type="invoice",
                        source_id=invoice.id,
                    )
                    invoice.transaction_id = txn.id
                    if unmatched:
                        result["warnings"].append(
                            f"Receipt {ref}: account(s) not found, posted to "
                            f"default income: {', '.join(sorted(set(unmatched)))}"
                        )
                else:
                    result["warnings"].append(
                        f"Receipt {ref}: imported but journal entry could not "
                        "be created (account mismatch)"
                    )

            # Payment for the full total, deposited per the header row.
            deposit_acct = _find_account(db, rec["deposit_account"])
            if not deposit_acct:
                uf_id = get_undeposited_funds_id(db)
                if uf_id:
                    deposit_acct = db.query(Account).filter(Account.id == uf_id).first()
            payment = Payment(
                customer_id=customer.id,
                date=rec["date"],
                amount=rec["total"],
                reference=invoice.invoice_number,
                deposit_to_account_id=deposit_acct.id if deposit_acct else None,
            )
            db.add(payment)
            db.flush()
            db.add(
                PaymentAllocation(
                    payment_id=payment.id,
                    invoice_id=invoice.id,
                    amount=rec["total"],
                )
            )
            if ar_id and deposit_acct and rec["total"] > 0:
                txn = create_journal_entry(
                    db,
                    invoice.date,
                    f"QB report import — Payment for receipt {invoice.invoice_number}",
                    [
                        {
                            "account_id": deposit_acct.id,
                            "debit": rec["total"],
                            "credit": Decimal("0"),
                            "description": f"Sales receipt {invoice.invoice_number}",
                        },
                        {
                            "account_id": ar_id,
                            "debit": Decimal("0"),
                            "credit": rec["total"],
                            "description": f"Sales receipt {invoice.invoice_number}",
                        },
                    ],
                    source_type="payment",
                    source_id=payment.id,
                )
                payment.transaction_id = txn.id

            db.flush()
            db.refresh(invoice)
            from app.services.inventory_hooks import post_sale_for_invoice

            post_sale_for_invoice(db, invoice, txn_date=invoice.date)

            sp.commit()
            result["imported"] += 1
        except Exception as e:
            sp.rollback()
            result["errors"].append(f"Receipt {rec.get('num') or rec['row']}: {e}")

    db.commit()
    return result
