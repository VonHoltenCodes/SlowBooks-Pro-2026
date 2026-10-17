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
import logging
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.accounts import Account
from app.models.contacts import Customer
from app.models.invoices import Invoice, InvoiceLine, InvoiceStatus
from app.models.items import Item
from app.models.payments import Payment, PaymentAllocation
from app.schemas.common import TAX_RATE_PLACES
from app.services.accounting import (
    _q,
    create_journal_entry,
    get_ar_account_id,
    get_default_income_account_id,
    get_undeposited_funds_id,
)
from app.services.donor_documents import document_label
from app.services.terminology import document_reference, terms_from_db
from app.services.csv_export import strip_formula_guard
from app.services.iif_import import _find_account
from app.services.jobs_service import resolve_customer_and_job
from app.services.safe_errors import DataProblem, safe_message


def _csv_rows(csv_text: str) -> list[list[str]]:
    """The report's rows, with a spreadsheet's formula guard ("'=Name")
    taken off each cell (csv_export.strip_formula_guard)."""
    return [
        [strip_formula_guard(c) for c in row]
        for row in csv.reader(io.StringIO(csv_text))
    ]


logger = logging.getLogger(__name__)

# Columns the parser needs to see in the report's header row. Memo, Item,
# Item Description, and Qty are used when present but not required.
REQUIRED_COLUMNS = ("Date", "Name", "Account", "Split", "Debit", "Credit")

# Header signatures for the three supported report shapes. Detection is by
# column set, not report title — titles vary by QB version and locale.
DEPOSIT_COLUMNS = ("Type", "Date", "Name", "Account", "Amount")
CHECK_COLUMNS = ("Type", "Date", "Name", "Account", "Original Amount", "Paid Amount")


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
    """'6.4%' -> 0.064, '8.875%' -> 0.08875; anything else -> 0. A document
    keeps its rate to six places (a percent to four), so a finer one is
    rounded half up here, not left to the database: PostgreSQL rounds it as
    it stores it, SQLite stores it as given and rounds it as it reads it."""
    price = (price or "").strip()
    if not price.endswith("%"):
        return Decimal("0")
    try:
        rate = Decimal(price[:-1]) / Decimal("100")
    except InvalidOperation:
        return Decimal("0")
    if not rate.is_finite():
        return Decimal("0")
    return rate.quantize(TAX_RATE_PLACES, rounding=ROUND_HALF_UP)


def _find_header_for(rows: list[list[str]], required: tuple):
    """Locate a column-header row containing `required`; map names to indexes."""
    for i, row in enumerate(rows):
        cells = [c.strip() for c in row]
        if all(col in cells for col in required):
            return i, {name: cells.index(name) for name in cells if name}
    return None, None


def _find_header(rows: list[list[str]]):
    return _find_header_for(rows, REQUIRED_COLUMNS)


def _cell(row: list[str], cols: dict, name: str) -> str:
    idx = cols.get(name)
    if idx is None or idx >= len(row):
        return ""
    return (row[idx] or "").strip()


_AR_NAMES = frozenset({"accounts receivable", "a/r", "accounts receivable (a/r)"})


def _ar_names(db: Session | None) -> frozenset:
    """How the report may name the receivable account: the usual names and
    this chart's own 1100 (renamed control accounts keep their number)."""
    names = set(_AR_NAMES)
    if db is not None:
        try:
            from app.services.control_accounts import find

            ar_id = find(db, "1100")
            acct = db.get(Account, ar_id) if ar_id else None
            if acct and acct.name:
                names.add(acct.name.strip().lower())
        except Exception:  # a chart with no receivable: the usual names stand
            pass
    return frozenset(names)


def _header_kind(row, cols, ar_names) -> str:
    """An invoice's header row posts to Accounts Receivable; a sales
    receipt's to the deposit account. A Type column, when the report has
    one, says so outright (#291)."""
    if "Type" in cols:
        rtype = _cell(row, cols, "Type").strip().lower()
        if rtype:
            return "invoice" if rtype == "invoice" else "receipt"
    return (
        "invoice"
        if _cell(row, cols, "Account").strip().lower() in ar_names
        else "receipt"
    )


def _group_sales(rows, cols, start, errors, ar_names=_AR_NAMES, warnings=None):
    """Group data rows into sales — receipts and invoices alike.

    A row whose Split column is "-SPLIT-" is a sale's header: it carries
    the customer (Customer:Job), date, document number, the posting
    account (the deposit account for a receipt, Accounts Receivable for an
    invoice), and the total (Debit side); an Open Balance, Paid or Due
    Date column, when the report has one, is read from it too. Rows that
    follow belong to that sale: a percentage in Sales Price marks a tax
    row; everything else is a line item with a signed amount of
    Credit − Debit.
    """
    sales = []
    current = None
    for n, row in enumerate(rows[start + 1 :], start=start + 2):
        if not _parse_date(_cell(row, cols, "Date")):
            continue  # report group headers/footers/blank rails
        if _cell(row, cols, "Split") == "-SPLIT-":
            open_cell = (
                _cell(row, cols, "Open Balance") if "Open Balance" in cols else ""
            )
            paid_cell = (
                _cell(row, cols, "Paid").strip().lower() if "Paid" in cols else ""
            )
            current = {
                "row": n,
                "kind": _header_kind(row, cols, ar_names),
                "num": _cell(row, cols, "Num"),
                "date": _parse_date(_cell(row, cols, "Date")),
                "customer": _cell(row, cols, "Name"),
                "deposit_account": _cell(row, cols, "Account"),
                "total": _q(_amount(_cell(row, cols, "Debit"))),
                "open_balance": _q(_amount(open_cell)) if open_cell != "" else None,
                "paid_flag": (
                    True
                    if paid_cell in ("paid", "yes", "y")
                    else False if paid_cell in ("unpaid", "no", "n") else None
                ),
                "due_date": (
                    _parse_date(_cell(row, cols, "Due Date"))
                    if "Due Date" in cols
                    else None
                ),
                "lines": [],
                "tax_amount": Decimal("0"),
                "tax_rate": Decimal("0"),
                "class": _cell(row, cols, "Class"),
            }
            sales.append(current)
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
        kept = qty.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        if kept != qty and warnings is not None:
            # quantities are kept to four places (2.23.0, NEW-94)
            warnings.append(
                f"{current['num'] or f'row {current['row']}'}: quantity {qty} kept as "
                f"{kept} (quantities are kept to four decimal places; the line "
                "amount is the report's, and its unit price is set so that "
                "quantity × price gives it)"
            )
        qty = kept
        # The unit price: the report's Sales Price when it reproduces the
        # amount at this quantity, else the amount ÷ the quantity to four
        # places, so quantity × price is the amount an edit works out
        # (2.23.0 gate, NEW-95: it was amount ÷ quantity to the cent).
        listed = _amount(price) if price and not price.endswith("%") else Decimal("0")
        if listed and _q(qty * abs(listed)) == abs(signed):
            rate = abs(listed)
        else:
            rate = (abs(signed) / qty).quantize(
                Decimal("0.0001"), rounding=ROUND_HALF_UP
            )
        current["lines"].append(
            {
                "description": _cell(row, cols, "Memo")
                or _cell(row, cols, "Item Description")
                or _cell(row, cols, "Item"),
                "item_name": _cell(row, cols, "Item"),
                "account": _cell(row, cols, "Account"),
                "quantity": qty,
                "rate": rate,
                "amount": signed,
                "class": _cell(row, cols, "Class"),
            }
        )
    return sales


def _group_receipts(rows, cols, start, errors, ar_names=_AR_NAMES, warnings=None):
    """The sales receipts of a report (an invoice's block, which posts to
    Accounts Receivable, is the invoice importer's)."""
    return [
        b
        for b in _group_sales(rows, cols, start, errors, ar_names, warnings=warnings)
        if b["kind"] == "receipt"
    ]


def _assign_number(db: Session, num: str, prefix: str = "SR") -> str:
    """Use the report's Num when it's free; otherwise prefix and bump.

    QB sales receipts run their own sequence (often 1, 2, 3 …), which can
    collide with existing invoice numbers here; an invoice whose number is
    taken gets a QB- prefix the same way.
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
        candidate = f"{prefix}-{num}"
        while (
            db.query(Invoice.id).filter(Invoice.invoice_number == candidate).first()
            is not None
        ):
            n += 1
            candidate = f"{prefix}-{num}-{n}"
    return candidate


def import_sales_receipt_report(db: Session, csv_text: str) -> dict:
    """Import a Transaction Detail by Date CSV (filtered to Sales Receipt)."""
    result = {
        "imported": 0,
        "duplicates_skipped": 0,
        "errors": [],
        "warnings": [],
    }
    rows = _csv_rows(csv_text)
    header_idx, cols = _find_header(rows)
    if header_idx is None:
        result["errors"].append(
            "Could not find the report's column header row. Export the "
            "Transaction Detail report with at least these columns: "
            + ", ".join(REQUIRED_COLUMNS)
        )
        return result

    receipts = _group_receipts(
        rows, cols, header_idx, result["errors"], _ar_names(db), result["warnings"]
    )
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
            header_class_id = _line_classes(db, rec, result["warnings"])  # #308
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
                class_id=header_class_id,
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
                        rate=ln["rate"],
                        amount=ln["amount"],
                        line_order=order,
                        class_id=ln["class_id"],
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
                        "class_id": header_class_id,
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
                            "class_id": ln["class_id"],
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
                        class_id=header_class_id,
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
            # A DataProblem carries its own sentence; Python's own errors
            # are bugs — logged with the traceback, answered generically.
            sp.rollback()
            result["errors"].append(
                f"Receipt {rec.get('num') or rec['row']}: "
                + safe_message(e, "QB report import")
            )

    db.commit()
    return result


def _status_for(total: Decimal, block: dict):
    """Open balance, paid amount and status from the report's Open Balance
    or Paid column; with neither, the invoice is open (#291)."""
    if block.get("open_balance") is not None:
        open_bal = _q(min(max(block["open_balance"], Decimal("0")), total))
    elif block.get("paid_flag") is True:
        open_bal = Decimal("0")
    else:
        open_bal = total
    paid = _q(total - open_bal)
    if open_bal == 0 and total > 0:
        status = InvoiceStatus.PAID
    elif paid > 0:
        status = InvoiceStatus.PARTIAL
    else:
        status = InvoiceStatus.SENT
    return open_bal, paid, status


# Statuses an invoice cannot take a report's payment in: something is paid
# on it already, or it is void.
_SETTLED = {InvoiceStatus.PAID, InvoiceStatus.PARTIAL} | {
    st for st in InvoiceStatus if "VOID" in st.name
}


def _report_class(db: Session, name: str, warnings=None):
    """The class a report row names (its Class column): exact, then
    case-insensitive; a class the books do not have is created, as a
    customer or a job named in a report is, and the result says so once
    (2.23.1, #308: the column was read into the index and never used, so
    a file whose entities are its classes arrived with every posting
    under Uncategorized)."""
    from app.models.classes import TxnClass
    from app.services.classes_service import resolve_class_id

    name = (name or "").strip()
    if not name:
        return None
    class_id = resolve_class_id(db, name)
    if class_id is not None:
        return class_id
    limit = TxnClass.__table__.c.name.type.length
    if len(name) > limit:
        raise DataProblem(
            f"class '{name}' is over the {limit}-character limit; shorten it in "
            "the report or create it under Settings → Classes first"
        )
    row = TxnClass(name=name)
    db.add(row)
    db.flush()
    if warnings is not None:
        note = (
            f"Class '{name}' is named in the report and was not in your books: created"
        )
        if note not in warnings:
            warnings.append(note)
    return row.id


def _line_classes(db: Session, block: dict, warnings) -> int | None:
    """Resolve a sale's header class and each line's (its own, else the
    header's) onto the block's dicts; returns the header's id."""
    header_class_id = _report_class(db, block.get("class"), warnings)
    for ln in block["lines"]:
        ln["class_id"] = (
            _report_class(db, ln.get("class"), warnings)
            if ln.get("class")
            else header_class_id
        )
    return header_class_id


def _a(word: str) -> str:
    """The word with its article: "an invoice", "a pledge" (2.23.0 gate,
    W-7: the notice switched vocabulary halfway for a nonprofit)."""
    return ("an " if word[:1].lower() in "aeiou" else "a ") + word


def _same_invoice(db: Session, customer_id: int, blk: dict):
    """The query for the invoice a report row already is here: this
    customer's, under the report's number as it was kept or renumbered
    (QB-<num>, QB-<num>-<n>), or — with no number — of the same date and
    total."""
    q = db.query(Invoice).filter(
        Invoice.is_sales_receipt.is_(False), Invoice.customer_id == customer_id
    )
    num = blk.get("num")
    if num:
        return q.filter(
            or_(
                Invoice.invoice_number == num,
                Invoice.invoice_number == f"QB-{num}",
                Invoice.invoice_number.like(f"QB-{num}-%"),
            )
        )
    return q.filter(Invoice.date == blk["date"], Invoice.total == blk["total"])


def _nothing_paid(db: Session, invoice) -> bool:
    return (
        invoice.status not in _SETTLED
        and _q(Decimal(str(invoice.amount_paid or 0))) == 0
        and db.query(PaymentAllocation.id).filter_by(invoice_id=invoice.id).first()
        is None
    )


def _record_report_payment(db, invoice, customer_id, paid_amt, ar_id, ledger_ref):
    """What QuickBooks showed as paid: one payment, dated the invoice, to
    Undeposited Funds (the report has no payment dates), applied to the
    invoice and posted."""
    uf_id = get_undeposited_funds_id(db)
    payment = Payment(
        customer_id=customer_id,
        date=invoice.date,
        amount=paid_amt,
        reference=invoice.invoice_number,
        deposit_to_account_id=uf_id,
    )
    db.add(payment)
    db.flush()
    db.add(
        PaymentAllocation(payment_id=payment.id, invoice_id=invoice.id, amount=paid_amt)
    )
    if ar_id and uf_id:
        txn = create_journal_entry(
            db,
            invoice.date,
            f"QB report import — Payment for {ledger_ref}",
            [
                {
                    "account_id": uf_id,
                    "debit": paid_amt,
                    "credit": Decimal("0"),
                    "description": ledger_ref,
                },
                {
                    "account_id": ar_id,
                    "debit": Decimal("0"),
                    "credit": paid_amt,
                    "description": ledger_ref,
                },
            ],
            source_type="payment",
            source_id=payment.id,
        )
        payment.transaction_id = txn.id
    return payment


def import_invoice_report(db: Session, csv_text: str) -> dict:
    """Import a Transaction Detail by Date CSV filtered to Invoice (#291):
    each invoice with its lines (item, quantity, price, tax), its
    Customer:Job and its number; open, partly paid or paid by the
    report's Open Balance (or Paid) column, with a payment for what was
    paid, dated the invoice, deposited to Undeposited Funds. Without that
    column every invoice comes in open, and the import says so."""
    result = {
        "imported": 0,
        "open": 0,
        "partly_paid": 0,
        "paid": 0,
        "updated": 0,  # already here and open; marked paid from Open Balance
        "duplicates_skipped": 0,
        "errors": [],
        "warnings": [],
    }
    rows = _csv_rows(csv_text)
    header_idx, cols = _find_header(rows)
    if header_idx is None:
        result["errors"].append(
            "Could not find the report's column header row. Export the "
            "Transaction Detail report with at least these columns: "
            + ", ".join(REQUIRED_COLUMNS)
        )
        return result

    blocks = [
        b
        for b in _group_sales(
            rows, cols, header_idx, result["errors"], _ar_names(db), result["warnings"]
        )
        if b["kind"] == "invoice"
    ]
    has_paid_column = "Open Balance" in cols or "Paid" in cols
    ar_id = get_ar_account_id(db)
    default_income_id = get_default_income_account_id(db)
    # The import's own messages say the company's word for the report's
    # document (Invoice, or Pledge on a nonprofit); what the ledger writes
    # for a document it made says that document's face, as a posting does.
    words = terms_from_db(db)
    doc = words("Invoice")

    for blk in blocks:
        sp = db.begin_nested()
        try:
            ref = blk["num"] or f"row {blk['row']}"
            line_sum = sum((ln["amount"] for ln in blk["lines"]), Decimal("0"))
            expected = _q(line_sum + blk["tax_amount"])
            if expected != blk["total"]:
                sp.rollback()
                result["errors"].append(
                    f"{doc} {ref}: lines + tax ({expected}) do not equal the "
                    f"{doc.lower()} total ({blk['total']}); skipped"
                )
                continue

            # Customer:Job — the job is created under the customer on first sight
            customer, job = resolve_customer_and_job(
                db, (blk["customer"] or "Walk-In Customer")[:200]
            )
            if customer is None:
                customer = (
                    db.query(Customer)
                    .filter(Customer.name == "Walk-In Customer")
                    .first()
                )
                if not customer:
                    customer = Customer(name="Walk-In Customer", is_active=True)
                    db.add(customer)
                    db.flush()

            # Already here? The same source document: this customer's
            # invoice under the report's number — as it was numbered here,
            # or as the import renumbered it when the number was taken
            # (QB-1031, QB-1031-1 …) — or, with no number, the customer's
            # invoice of that date and total. The number alone matched
            # another customer's invoice (skipped as a duplicate) and missed
            # the import's own renumbered copies (imported again on every
            # upload) (2.23.0 gate, W-2 / NEW-87).
            dup = _same_invoice(db, customer.id, blk).first()
            open_bal, paid_amt, status = _status_for(blk["total"], blk)
            if dup:
                # An invoice that came in open before the report had its
                # Open Balance column takes its payment from this upload,
                # as the warning promised; one with anything paid on it
                # already, or a different total, is left alone (NEW-84).
                if (
                    has_paid_column
                    and paid_amt > 0
                    and _nothing_paid(db, dup)
                    and _q(Decimal(str(dup.total or 0))) == blk["total"]
                ):
                    ledger_ref = document_reference(
                        document_label(dup, words), dup.invoice_number
                    )
                    _record_report_payment(
                        db, dup, customer.id, paid_amt, ar_id, ledger_ref
                    )
                    dup.amount_paid = paid_amt
                    dup.balance_due = open_bal
                    dup.status = status
                    db.flush()
                    sp.commit()
                    result["updated"] += 1
                    continue
                sp.rollback()
                result["duplicates_skipped"] += 1
                continue

            subtotal = _q(line_sum)
            header_class_id = _line_classes(db, blk, result["warnings"])  # #308
            invoice = Invoice(
                invoice_number=_assign_number(db, blk["num"], prefix="QB"),
                customer_id=customer.id,
                job_id=job.id if job else None,
                date=blk["date"],
                due_date=blk["due_date"] or blk["date"],
                status=status,
                is_sales_receipt=False,
                subtotal=subtotal,
                tax_rate=blk["tax_rate"] if blk["tax_amount"] else Decimal("0"),
                tax_amount=_q(blk["tax_amount"]),
                total=blk["total"],
                amount_paid=paid_amt,
                balance_due=open_bal,
                class_id=header_class_id,
            )
            db.add(invoice)
            db.flush()
            ledger_ref = document_reference(
                document_label(invoice, words), invoice.invoice_number
            )

            for order, ln in enumerate(blk["lines"]):
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
                        rate=ln["rate"],
                        amount=ln["amount"],
                        line_order=order,
                        class_id=ln["class_id"],
                    )
                )

            # The invoice's journal: DR A/R for the total; each positive line
            # credits its account, a contra line debits it; tax credits its account
            if ar_id and blk["total"] != 0:
                journal_lines = [
                    {
                        "account_id": ar_id,
                        "debit": blk["total"],
                        "credit": Decimal("0"),
                        "description": ledger_ref,
                        "class_id": header_class_id,
                    }
                ]
                unmatched = []
                for ln in blk["lines"]:
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
                            "class_id": ln["class_id"],
                        }
                    )
                if blk["tax_amount"]:
                    from app.services.accounting import get_sales_tax_account_id

                    tax_id = get_sales_tax_account_id(db)
                    if tax_id:
                        journal_lines.append(
                            {
                                "account_id": tax_id,
                                "debit": Decimal("0"),
                                "credit": blk["tax_amount"],
                                "description": "Sales tax",
                            }
                        )
                total_dr = sum(jl["debit"] for jl in journal_lines)
                total_cr = sum(jl["credit"] for jl in journal_lines)
                if total_dr == total_cr and total_dr > 0:
                    txn = create_journal_entry(
                        db,
                        invoice.date,
                        f"QB report import — {ledger_ref}",
                        journal_lines,
                        source_type="invoice",
                        source_id=invoice.id,
                        class_id=header_class_id,
                    )
                    invoice.transaction_id = txn.id
                    if unmatched:
                        result["warnings"].append(
                            f"{doc} {ref}: account(s) not found, posted to "
                            f"default income: {', '.join(sorted(set(unmatched)))}"
                        )
                else:
                    result["warnings"].append(
                        f"{doc} {ref}: imported but journal entry could not "
                        "be created (account mismatch)"
                    )

            # What QuickBooks showed as paid: one payment, dated the invoice,
            # to Undeposited Funds (the report has no payment dates)
            if paid_amt > 0:
                _record_report_payment(
                    db, invoice, customer.id, paid_amt, ar_id, ledger_ref
                )

            db.flush()
            db.refresh(invoice)
            from app.services.inventory_hooks import post_sale_for_invoice

            post_sale_for_invoice(db, invoice, txn_date=invoice.date)

            sp.commit()
            result["imported"] += 1
            result[
                (
                    "paid"
                    if status == InvoiceStatus.PAID
                    else "partly_paid" if status == InvoiceStatus.PARTIAL else "open"
                )
            ] += 1
        except Exception as e:
            sp.rollback()
            result["errors"].append(
                f"{doc} {blk.get('num') or blk['row']}: "
                + safe_message(e, "QB report import")
            )

    # Said once, after the fact, and only about invoices this upload brought
    # in: a re-upload that skipped them all said "every invoice came in
    # open" (2.23.0 gate, NEW-92).
    if result["imported"] and not has_paid_column:
        n = result["imported"]
        result["warnings"].append(
            f"The report has no Open Balance column, so the {n} "
            f"{words('invoice' if n == 1 else 'invoices')} that came in "
            f"{'is' if n == 1 else 'are'} open. Add Open Balance (Customize "
            f"Report → Display → Columns) and re-upload it: {_a(words('invoice'))} "
            "here with nothing paid on it is marked paid or partly paid from that "
            "column — or record the payments here."
        )

    db.commit()
    return result


# ============================================================================
# Deposit Detail report — QB's "Make Deposits" history.
#
# Block shape: a Type="Deposit" header row (bank account, positive total)
# followed by the payment rows being deposited (negative amounts, usually
# from Undeposited Funds), terminated by a TOTAL rail. Imported as
# journal-only Transactions with source_type="deposit" — identical to
# what the manual Make Deposits route and the IIF DEPOSIT importer
# produce, so the check register and pending-deposits logic see them.
# ============================================================================


def _group_blocks(rows, cols, start, header_type, skipped=None):
    """Group report rows into blocks: a Type==header_type row starts a
    block, a TOTAL rail ends it, everything between with an Account is a
    detail line.

    A dated, typed row OUTSIDE any block is a block header of some other
    transaction type (Check Detail reports can carry "Bill Pmt -Check",
    "Paycheck", and "Transfer" blocks) — tallied into `skipped` so the
    importer can warn instead of silently dropping them (#62 review).
    """
    blocks = []
    current = None
    for n, row in enumerate(rows[start + 1 :], start=start + 2):
        first = (row[0] or "").strip() if row else ""
        if first.upper().startswith("TOTAL"):
            current = None
            continue
        rtype = _cell(row, cols, "Type")
        if rtype == header_type:
            current = {"row": n, "header": row, "details": []}
            blocks.append(current)
            continue
        if (
            skipped is not None
            and current is None
            and rtype
            and _parse_date(_cell(row, cols, "Date"))
        ):
            skipped[rtype] = skipped.get(rtype, 0) + 1
            continue
        if current is not None and _cell(row, cols, "Account"):
            current["details"].append(row)
    return blocks


def import_deposit_report(db: Session, csv_text: str) -> dict:
    """Import a QB Deposit Detail report CSV as deposit journal entries."""
    result = {"deposits": 0, "duplicates_skipped": 0, "errors": [], "warnings": []}
    rows = _csv_rows(csv_text)
    header_idx, cols = _find_header_for(rows, DEPOSIT_COLUMNS)
    if header_idx is None:
        result["errors"].append("Could not find the Deposit Detail header row.")
        return result

    skipped_types = {}
    for block in _group_blocks(rows, cols, header_idx, "Deposit", skipped_types):
        sp = db.begin_nested()
        try:
            hdr = block["header"]
            dep_date = _parse_date(_cell(hdr, cols, "Date"))
            if dep_date is None:
                raise DataProblem("deposit row has no parseable date")
            bank_name = _cell(hdr, cols, "Account")
            bank = _find_account(db, bank_name)
            if not bank:
                raise DataProblem(f"bank account '{bank_name}' not found")
            total = _q(_amount(_cell(hdr, cols, "Amount")))
            if total <= 0:
                raise DataProblem(f"deposit total {total} is not positive")

            # Sum-to-zero: header total + detail amounts must cancel.
            detail = []
            for drow in block["details"]:
                amt = _q(_amount(_cell(drow, cols, "Amount")))
                acct_name = _cell(drow, cols, "Account")
                acct = _find_account(db, acct_name)
                if not acct:
                    raise DataProblem(
                        f"source account '{acct_name}' not found - import "
                        "your chart of accounts (IIF lists) first"
                    )
                detail.append(
                    (acct, amt, _cell(drow, cols, "Name"), _cell(drow, cols, "Class"))
                )
            residual = total + sum(a for _, a, _, _ in detail)
            if abs(residual) > Decimal("0.01"):
                raise DataProblem(
                    f"block does not balance (residual {residual}); "
                    "export the report with all its rows"
                )

            # Dedup: no document number on a deposit — match on date +
            # bank + total among imported/manual deposits. Two identical
            # same-day deposits to one account would collide; rare, and
            # reported as a skip rather than silently doubled.
            from app.models.transactions import Transaction

            existing = (
                db.query(Transaction)
                .filter(
                    Transaction.source_type == "deposit",
                    Transaction.date == dep_date,
                    Transaction.description == f"Deposit to {bank.name} ({total})",
                )
                .first()
            )
            if existing:
                sp.rollback()
                result["duplicates_skipped"] += 1
                continue

            header_class_id = _report_class(  # the header row's Class (#308)
                db, _cell(hdr, cols, "Class"), result["warnings"]
            )
            journal_lines = [
                {
                    "account_id": bank.id,
                    "debit": total,
                    "credit": Decimal("0"),
                    "description": f"Deposit to {bank.name}",
                    "class_id": header_class_id,
                }
            ]
            for acct, amt, name, class_name in detail:
                journal_lines.append(
                    {
                        "account_id": acct.id,
                        "debit": amt if amt > 0 else Decimal("0"),
                        "credit": -amt if amt < 0 else Decimal("0"),
                        "description": name or f"Deposit to {bank.name}",
                        "class_id": (
                            _report_class(db, class_name, result["warnings"])
                            if class_name
                            else header_class_id
                        ),
                    }
                )
            create_journal_entry(
                db,
                dep_date,
                f"Deposit to {bank.name} ({total})",
                journal_lines,
                source_type="deposit",
                class_id=header_class_id,
            )
            sp.commit()
            result["deposits"] += 1
        except Exception as e:
            # A DataProblem carries its own sentence; Python's own errors
            # are bugs — logged with the traceback, answered generically.
            sp.rollback()
            result["errors"].append(
                f"Deposit block at row {block['row']}: "
                + safe_message(e, "QB report import")
            )

    for btype, count in sorted(skipped_types.items()):
        result["warnings"].append(
            f"{count} '{btype}' block(s) skipped — this report import "
            "handles Deposit blocks only"
        )
    db.commit()
    return result


# ============================================================================
# Check Detail report — QB's "Write Checks" history.
#
# Block shape: a Type="Check" header row (bank account, negative Original
# Amount = check total, payee in Name) followed by expense split rows
# (blank Type, expense Account, positive Original / negative Paid),
# terminated by a TOTAL rail. Imported as journal-only Transactions with
# source_type="check": CR bank, DR each split — which is what the check
# register reads.
# ============================================================================


def import_check_report(db: Session, csv_text: str) -> dict:
    """Import a QB Check Detail report CSV as check journal entries."""
    result = {"checks": 0, "duplicates_skipped": 0, "errors": [], "warnings": []}
    rows = _csv_rows(csv_text)
    header_idx, cols = _find_header_for(rows, CHECK_COLUMNS)
    if header_idx is None:
        result["errors"].append("Could not find the Check Detail header row.")
        return result

    skipped_types = {}
    for block in _group_blocks(rows, cols, header_idx, "Check", skipped_types):
        sp = db.begin_nested()
        try:
            hdr = block["header"]
            chk_date = _parse_date(_cell(hdr, cols, "Date"))
            if chk_date is None:
                raise DataProblem("check row has no parseable date")
            bank_name = _cell(hdr, cols, "Account")
            bank = _find_account(db, bank_name)
            if not bank:
                raise DataProblem(f"bank account '{bank_name}' not found")
            payee = _cell(hdr, cols, "Name")
            num = _cell(hdr, cols, "Num")
            memo = _cell(hdr, cols, "Memo")
            total = _q(abs(_amount(_cell(hdr, cols, "Original Amount"))))
            if total <= 0:
                raise DataProblem("check total is zero")

            # Sign-aware splits: Paid Amount is negative for a normal
            # expense line and POSITIVE for a contra line — e.g. a payroll
            # check's withholding rows credit their liability accounts and
            # net the gross salary down to the check amount. An abs()
            # parse would fail the balance gate on every payroll check.
            splits = []
            for drow in block["details"]:
                paid = _cell(drow, cols, "Paid Amount")
                if paid:
                    signed = _q(-_amount(paid))
                else:
                    signed = _q(_amount(_cell(drow, cols, "Original Amount")))
                if signed == 0:
                    continue
                acct_name = _cell(drow, cols, "Account")
                acct = _find_account(db, acct_name)
                if not acct:
                    raise DataProblem(
                        f"account '{acct_name}' not found - import your "
                        "chart of accounts (IIF lists) first"
                    )
                splits.append(
                    (
                        acct,
                        signed,
                        _cell(drow, cols, "Memo"),
                        _cell(drow, cols, "Class"),
                    )
                )
            split_sum = sum(a for _, a, _, _ in splits)
            if abs(split_sum - total) > Decimal("0.01"):
                raise DataProblem(
                    f"splits ({split_sum}) do not equal the check total ({total})"
                )

            desc = f"Check {num} - {payee}" if num else f"Check - {payee}"
            if memo:
                desc += f" ({memo})"
            # Dedup on (date, description, source_type). Two same-day
            # checks to one payee with identical memo and number would
            # collide — reported as a skip.
            from app.models.transactions import Transaction

            existing = (
                db.query(Transaction)
                .filter(
                    Transaction.source_type == "check",
                    Transaction.date == chk_date,
                    Transaction.description == desc,
                )
                .first()
            )
            if existing:
                sp.rollback()
                result["duplicates_skipped"] += 1
                continue

            header_class_id = _report_class(  # the header row's Class (#308)
                db, _cell(hdr, cols, "Class"), result["warnings"]
            )
            journal_lines = [
                {
                    "account_id": bank.id,
                    "debit": Decimal("0"),
                    "credit": total,
                    "description": desc,
                    "class_id": header_class_id,
                }
            ]
            for acct, signed, line_memo, class_name in splits:
                journal_lines.append(
                    {
                        "account_id": acct.id,
                        "debit": signed if signed > 0 else Decimal("0"),
                        "credit": -signed if signed < 0 else Decimal("0"),
                        "description": line_memo or desc,
                        "class_id": (
                            _report_class(db, class_name, result["warnings"])
                            if class_name
                            else header_class_id
                        ),
                    }
                )
            create_journal_entry(
                db,
                chk_date,
                desc,
                journal_lines,
                source_type="check",
                reference=num or "",
                class_id=header_class_id,
            )
            sp.commit()
            result["checks"] += 1
        except Exception as e:
            # A DataProblem carries its own sentence; Python's own errors
            # are bugs — logged with the traceback, answered generically.
            sp.rollback()
            result["errors"].append(
                f"Check block at row {block['row']}: "
                + safe_message(e, "QB report import")
            )

    for btype, count in sorted(skipped_types.items()):
        result["warnings"].append(
            f"{count} '{btype}' block(s) skipped — this report import "
            "handles Check blocks only (Bill Pmt -Check, Paycheck, and "
            "Transfer support is on the roadmap)"
        )
    db.commit()
    return result


# ============================================================================
# Dispatcher — one upload, header-signature detection
# ============================================================================


def _sale_kinds(rows, header_idx, cols, ar_names=_AR_NAMES) -> set:
    return {b["kind"] for b in _group_sales(rows, cols, header_idx, [], ar_names)}


def detect_report_type(csv_text: str, db: Session | None = None) -> str | None:
    """Identify which QB report a CSV is, by column signature; a Transaction
    Detail export is receipts, invoices, or both, by what its header rows
    post to (#291)."""
    rows = _csv_rows(csv_text)
    header_idx, cols = _find_header_for(rows, REQUIRED_COLUMNS)
    if header_idx is not None:
        kinds = _sale_kinds(rows, header_idx, cols, _ar_names(db))
        if kinds == {"invoice"}:
            return "invoices"
        if kinds == {"invoice", "receipt"}:
            return "sales_and_invoices"
        return "sales_receipts"
    if _find_header_for(rows, CHECK_COLUMNS)[0] is not None:
        return "checks"
    if _find_header_for(rows, DEPOSIT_COLUMNS)[0] is not None:
        return "deposits"
    from app.services.qb_ledger_import import LEDGER_COLUMNS, is_ledger_header

    if is_ledger_header(_find_header_for(rows, LEDGER_COLUMNS)[1]):
        return "ledger"
    from app.services.qb_time_import import TIME_COLUMNS, is_time_header

    if is_time_header(_find_header_for(rows, TIME_COLUMNS)[1]):
        return "time"
    return None


def import_qb_report(db: Session, csv_text: str) -> dict:
    """Import any supported QB report CSV, auto-detected by its columns.

    Detection order (receipts, checks, deposits) is cosmetic: the three
    column signatures are mutually exclusive on exact cell matches — the
    deposit signature's bare "Amount" never appears in a check header
    ("Original Amount"/"Paid Amount" are distinct cells). (#62 review)
    """
    kind = detect_report_type(csv_text, db)
    base = {
        "detected": kind,
        "sales_receipts": 0,
        "invoices": 0,
        "invoices_open": 0,
        "invoices_partly_paid": 0,
        "invoices_paid": 0,
        "invoices_updated": 0,
        "deposits": 0,
        "checks": 0,
        "journals": 0,
        "time_entries": 0,
        "report": None,
        "duplicates_skipped": 0,
        "errors": [],
        "warnings": [],
    }
    if kind is None:
        base["errors"].append(
            "Unrecognized report layout. Supported exports: Transaction "
            "Detail (filtered to Sales Receipt or Invoice), Deposit Detail, "
            "Check Detail, Transaction Detail by Account and Time by Name by "
            "Job detail — keep the report's default columns."
        )
        return base
    if kind in ("sales_receipts", "invoices", "sales_and_invoices"):
        sub = {"duplicates_skipped": 0, "errors": [], "warnings": []}
        if kind != "invoices":
            r = import_sales_receipt_report(db, csv_text)
            base["sales_receipts"] = r.pop("imported", 0)
            for key in ("duplicates_skipped",):
                sub[key] += r.get(key, 0)
            sub["errors"] += r.get("errors", [])
            sub["warnings"] += r.get("warnings", [])
        if kind != "sales_receipts":
            r = import_invoice_report(db, csv_text)
            base["invoices"] = r.pop("imported", 0)
            base["invoices_open"] = r.get("open", 0)
            base["invoices_partly_paid"] = r.get("partly_paid", 0)
            base["invoices_paid"] = r.get("paid", 0)
            base["invoices_updated"] = r.get("updated", 0)
            sub["duplicates_skipped"] += r.get("duplicates_skipped", 0)
            sub["errors"] += r.get("errors", [])
            sub["warnings"] += r.get("warnings", [])
    elif kind == "ledger":
        from app.services.qb_ledger_import import LEDGER_COLUMNS, import_ledger_report

        rows = _csv_rows(csv_text)
        header_at, cols = _find_header_for(rows, LEDGER_COLUMNS)
        sub = import_ledger_report(db, rows, header_at, cols)
        base["journals"] = sub.pop("journals", 0)
    elif kind == "time":
        from app.services.qb_time_import import TIME_COLUMNS, import_time_report

        rows = _csv_rows(csv_text)
        header_at, cols = _find_header_for(rows, TIME_COLUMNS)
        sub = import_time_report(db, rows, header_at, cols)
        base["time_entries"] = sub.pop("time_entries", 0)
        base["report"] = sub.pop("report", None)
    elif kind == "deposits":
        sub = import_deposit_report(db, csv_text)
        base["deposits"] = sub.pop("deposits", 0)
    else:
        sub = import_check_report(db, csv_text)
        base["checks"] = sub.pop("checks", 0)
    base["duplicates_skipped"] = sub.get("duplicates_skipped", 0)
    base["errors"] = sub.get("errors", [])
    base["warnings"] = sub.get("warnings", [])
    return base
