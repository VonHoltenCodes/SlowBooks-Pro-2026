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
    """'6.4%' -> Decimal('0.064'); anything else -> 0."""
    price = (price or "").strip()
    if not price.endswith("%"):
        return Decimal("0")
    try:
        return Decimal(price[:-1]) / Decimal("100")
    except InvalidOperation:
        return Decimal("0")


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


def _group_blocks(rows, cols, start, header_type):
    """Group report rows into blocks: a Type==header_type row starts a
    block, a TOTAL rail ends it, everything between with an Account is a
    detail line."""
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
        if current is not None and _cell(row, cols, "Account"):
            current["details"].append(row)
    return blocks


def import_deposit_report(db: Session, csv_text: str) -> dict:
    """Import a QB Deposit Detail report CSV as deposit journal entries."""
    result = {"deposits": 0, "duplicates_skipped": 0, "errors": [], "warnings": []}
    rows = list(csv.reader(io.StringIO(csv_text)))
    header_idx, cols = _find_header_for(rows, DEPOSIT_COLUMNS)
    if header_idx is None:
        result["errors"].append("Could not find the Deposit Detail header row.")
        return result

    for block in _group_blocks(rows, cols, header_idx, "Deposit"):
        sp = db.begin_nested()
        try:
            hdr = block["header"]
            dep_date = _parse_date(_cell(hdr, cols, "Date"))
            if dep_date is None:
                raise ValueError("deposit row has no parseable date")
            bank_name = _cell(hdr, cols, "Account")
            bank = _find_account(db, bank_name)
            if not bank:
                raise ValueError(f"bank account '{bank_name}' not found")
            total = _q(_amount(_cell(hdr, cols, "Amount")))
            if total <= 0:
                raise ValueError(f"deposit total {total} is not positive")

            # Sum-to-zero: header total + detail amounts must cancel.
            detail = []
            for drow in block["details"]:
                amt = _q(_amount(_cell(drow, cols, "Amount")))
                acct_name = _cell(drow, cols, "Account")
                acct = _find_account(db, acct_name)
                if not acct:
                    raise ValueError(
                        f"source account '{acct_name}' not found - import "
                        "your chart of accounts (IIF lists) first"
                    )
                detail.append((acct, amt, _cell(drow, cols, "Name")))
            residual = total + sum(a for _, a, _ in detail)
            if abs(residual) > Decimal("0.01"):
                raise ValueError(
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

            journal_lines = [
                {
                    "account_id": bank.id,
                    "debit": total,
                    "credit": Decimal("0"),
                    "description": f"Deposit to {bank.name}",
                }
            ]
            for acct, amt, name in detail:
                journal_lines.append(
                    {
                        "account_id": acct.id,
                        "debit": amt if amt > 0 else Decimal("0"),
                        "credit": -amt if amt < 0 else Decimal("0"),
                        "description": name or f"Deposit to {bank.name}",
                    }
                )
            create_journal_entry(
                db,
                dep_date,
                f"Deposit to {bank.name} ({total})",
                journal_lines,
                source_type="deposit",
            )
            sp.commit()
            result["deposits"] += 1
        except Exception as e:
            sp.rollback()
            result["errors"].append(f"Deposit block at row {block['row']}: {e}")

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
    rows = list(csv.reader(io.StringIO(csv_text)))
    header_idx, cols = _find_header_for(rows, CHECK_COLUMNS)
    if header_idx is None:
        result["errors"].append("Could not find the Check Detail header row.")
        return result

    for block in _group_blocks(rows, cols, header_idx, "Check"):
        sp = db.begin_nested()
        try:
            hdr = block["header"]
            chk_date = _parse_date(_cell(hdr, cols, "Date"))
            if chk_date is None:
                raise ValueError("check row has no parseable date")
            bank_name = _cell(hdr, cols, "Account")
            bank = _find_account(db, bank_name)
            if not bank:
                raise ValueError(f"bank account '{bank_name}' not found")
            payee = _cell(hdr, cols, "Name")
            num = _cell(hdr, cols, "Num")
            memo = _cell(hdr, cols, "Memo")
            total = _q(abs(_amount(_cell(hdr, cols, "Original Amount"))))
            if total <= 0:
                raise ValueError("check total is zero")

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
                    raise ValueError(
                        f"account '{acct_name}' not found - import your "
                        "chart of accounts (IIF lists) first"
                    )
                splits.append((acct, signed, _cell(drow, cols, "Memo")))
            split_sum = sum(a for _, a, _ in splits)
            if abs(split_sum - total) > Decimal("0.01"):
                raise ValueError(
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

            journal_lines = [
                {
                    "account_id": bank.id,
                    "debit": Decimal("0"),
                    "credit": total,
                    "description": desc,
                }
            ]
            for acct, signed, line_memo in splits:
                journal_lines.append(
                    {
                        "account_id": acct.id,
                        "debit": signed if signed > 0 else Decimal("0"),
                        "credit": -signed if signed < 0 else Decimal("0"),
                        "description": line_memo or desc,
                    }
                )
            create_journal_entry(
                db,
                chk_date,
                desc,
                journal_lines,
                source_type="check",
                reference=num or "",
            )
            sp.commit()
            result["checks"] += 1
        except Exception as e:
            sp.rollback()
            result["errors"].append(f"Check block at row {block['row']}: {e}")

    db.commit()
    return result


# ============================================================================
# Dispatcher — one upload, header-signature detection
# ============================================================================


def detect_report_type(csv_text: str) -> str | None:
    """Identify which QB report a CSV is, by column signature."""
    rows = list(csv.reader(io.StringIO(csv_text)))
    if _find_header_for(rows, REQUIRED_COLUMNS)[0] is not None:
        return "sales_receipts"
    if _find_header_for(rows, CHECK_COLUMNS)[0] is not None:
        return "checks"
    if _find_header_for(rows, DEPOSIT_COLUMNS)[0] is not None:
        return "deposits"
    return None


def import_qb_report(db: Session, csv_text: str) -> dict:
    """Import any supported QB report CSV, auto-detected by its columns.

    Check detection runs before deposit: the check signature is a strict
    superset of the deposit signature minus Amount, so ordering matters.
    """
    kind = detect_report_type(csv_text)
    base = {
        "detected": kind,
        "sales_receipts": 0,
        "deposits": 0,
        "checks": 0,
        "duplicates_skipped": 0,
        "errors": [],
        "warnings": [],
    }
    if kind is None:
        base["errors"].append(
            "Unrecognized report layout. Supported exports: Transaction "
            "Detail (filtered to Sales Receipt), Deposit Detail, and "
            "Check Detail — keep the report's default columns."
        )
        return base
    if kind == "sales_receipts":
        sub = import_sales_receipt_report(db, csv_text)
        base["sales_receipts"] = sub.pop("imported", 0)
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
