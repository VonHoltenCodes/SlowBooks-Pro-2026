# ============================================================================
# IIF Export Service — Intuit Interchange Format (Tab-Delimited)
# Generates .iif files compatible with QuickBooks Pro 2003 (Build 12.0.3190)
#
# IIF format from the published Intuit SDK documentation (QBFC 5.0, qbXML
# 4.0 IIF appendix) and the files QuickBooks Pro 2003 itself writes from
# File > Utilities > Export.
#
# Format rules:
#   - Tab-delimited fields, \r\n line endings (Windows)
#   - Header rows start with ! (define column order)
#   - Transaction blocks: TRNS line, one or more SPL lines, ENDTRNS
#   - Sign convention: TRNS amount = primary (debit), SPL = splits (credits)
#   - Dates: MM/DD/YYYY
#   - No CSV-style quoting — tabs in values would break the format
#   - Encoding: Windows-1252 ("ANSI"), which is what QuickBooks reads an
#     IIF file as — see to_ansi()
#   - Amounts are home currency: a foreign-currency document goes out at
#     the amounts its journal entry booked, as QuickBooks 2003 has one
#     currency
# ============================================================================

import unicodedata
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session, joinedload

from app.models.accounts import Account
from app.models.contacts import Customer, Vendor
from app.models.items import Item
from app.models.invoices import Invoice, InvoiceLine, InvoiceStatus
from app.models.payments import Payment, PaymentAllocation
from app.models.estimates import Estimate, EstimateLine
from app.services.csv_export import _csv_safe
from app.services.currency import convert_lines, to_home
from app.services.iif_common import account_to_iif_type, item_to_iif_type


def _iif_date(d: date) -> str:
    """Format date as MM/DD/YYYY for QB2003."""
    return d.strftime("%m/%d/%Y") if d else ""


def _iif_clean(value) -> str:
    """Strip tabs and CR/LF from a field so user-supplied text can't break
    the IIF format. A customer named with a literal tab (or a memo with an
    embedded newline) would otherwise shift every following column or split
    one logical row into two, leaving the importing QuickBooks instance
    parsing garbage. Replaces with a single space."""
    if value is None:
        return ""
    s = str(value)
    return s.replace("\t", " ").replace("\r", " ").replace("\n", " ")


def _tab_join(fields: list) -> str:
    """Join fields with tabs, converting None to empty string. Each field is
    sanitized so embedded tabs / newlines can't smuggle extra columns or
    rows into the output."""
    return "\t".join(_iif_clean(f) for f in fields)


def _iif_line(fields: list) -> str:
    """Build a single IIF line: tab-joined fields + \\r\\n."""
    return _tab_join(fields) + "\r\n"


def _iif_text(value) -> str:
    """A name or free-text field, cleaned and neutralised the way the CSV
    export neutralises a cell: people edit IIF files in Excel (it is the
    documented way to fix one before import), and a customer named
    `=HYPERLINK(...)` would run there. The leading apostrophe makes it
    text; the importer takes it off again. Only text goes through here —
    an amount like -850.00 must stay a number."""
    return _csv_safe(_iif_clean(value))


# Letters with no Windows-1252 form and no accent to drop (NFKD leaves
# them whole), spelled the way a person would type them without the key.
_ANSI_PLAIN = {"Ł": "L", "ł": "l", "Đ": "D", "đ": "d", "ı": "i", "Ħ": "H", "ħ": "h"}


def _ansi_char(ch: str) -> str:
    try:
        ch.encode("cp1252")
        return ch
    except UnicodeEncodeError:
        pass
    if ch in _ANSI_PLAIN:
        return _ANSI_PLAIN[ch]
    plain = ""
    for part in unicodedata.normalize("NFKD", ch):
        if unicodedata.combining(part):
            continue
        try:
            part.encode("cp1252")
        except UnicodeEncodeError:
            continue
        plain += part
    return plain or "?"


def to_ansi(text: str) -> bytes:
    """The file as QuickBooks reads it: Windows-1252. QuickBooks reads an
    IIF file in the ANSI code page, so a UTF-8 file turned "Bäckerei
    Müller" into "BÃ¤ckerei MÃ¼ller" on import (2.17.3 exploratory test,
    W-M14). Every character Windows-1252 has (accents, €, curly quotes,
    dashes) is written as itself; one it lacks becomes its plain letter
    ("ő" -> "o", "Ł" -> "L") or "?", never an error or a broken file."""
    try:
        return text.encode("cp1252")
    except UnicodeEncodeError:
        return "".join(_ansi_char(ch) for ch in text).encode("cp1252")


def _rate(doc) -> Decimal:
    return Decimal(str(getattr(doc, "exchange_rate", None) or 1))


def _home(lines: list[tuple], rate: Decimal) -> list[tuple]:
    """(debit, credit) pairs in the document's currency, converted to home
    currency exactly as the posting code converted its journal lines (the
    same rounding, the same cent of drift on the same line), so the file
    carries the amounts the ledger booked. Pass the pairs in the order the
    posting code built them."""
    converted = convert_lines(
        [{"debit": Decimal(str(d)), "credit": Decimal(str(c))} for d, c in lines],
        rate,
    )
    return [(ln["debit"], ln["credit"]) for ln in converted]


def _class_name(db: Session, class_id) -> str:
    """The class a document is tagged with, verbatim (Parent:Child paths
    round-trip). Empty when untagged — the CLASS column is still emitted so
    a re-import reads a consistent column set."""
    if not class_id:
        return ""
    from app.models.classes import TxnClass

    row = db.get(TxnClass, class_id)
    return _iif_text(row.name) if row else ""


# Column sets for transaction blocks. Every block ends with CLASS so a tag
# never falls off on the way out (#70); the importer reads it back.
_TXN_COLUMNS = [
    "TRNSTYPE",
    "DATE",
    "ACCNT",
    "NAME",
    "AMOUNT",
    "DOCNUM",
    "DUEDATE",
    "TERMS",
    "MEMO",
    "CLASS",
]
_TXN_COLUMNS_SHORT = [
    "TRNSTYPE",
    "DATE",
    "ACCNT",
    "NAME",
    "AMOUNT",
    "DOCNUM",
    "MEMO",
    "CLASS",
]


def _txn_header(columns: list[str] = _TXN_COLUMNS) -> str:
    """!TRNS / !SPL / !ENDTRNS header trio for one column set."""
    return (
        _iif_line(["!TRNS"] + columns)
        + _iif_line(["!SPL"] + columns)
        + _iif_line(["!ENDTRNS"])
    )


def _resolve_account_name(db: Session, account_id: int) -> str:
    """Get account name with parent:child notation for QB2003."""
    if not account_id:
        return ""
    acct = db.query(Account).filter(Account.id == account_id).first()
    if not acct:
        return ""
    return _full_account_name(db, acct)


def _full_account_name(db: Session, acct: Account) -> str:
    """Build colon-separated parent:child account name for QB convention.
    Neutralised as text (see _iif_text): the list row and every transaction
    that names the account go through here, so they still match."""
    if acct.parent_id:
        parent = db.query(Account).filter(Account.id == acct.parent_id).first()
        if parent:
            return _iif_text(f"{_full_account_name(db, parent)}:{acct.name}")
    return _iif_text(acct.name)


# ============================================================================
# Export Functions — each returns IIF-formatted string content
# ============================================================================


def export_accounts(db: Session) -> str:
    """Export Chart of Accounts as !ACCNT section."""
    header = _iif_line(["!ACCNT", "NAME", "ACCNTTYPE", "DESC", "ACCNUM", "EXTRA"])

    accounts = (
        db.query(Account)
        .filter(Account.is_active)
        .order_by(Account.account_number)
        .all()
    )

    lines = header
    for acct in accounts:
        name = _full_account_name(db, acct)
        lines += _iif_line(
            [
                "ACCNT",
                name,
                account_to_iif_type(acct),
                _iif_text(acct.description),
                acct.account_number or "",
                "",  # EXTRA field (unused, but QB expects the column)
            ]
        )

    return lines


def export_customers(db: Session) -> str:
    """Export customers as !CUST section."""
    header = _iif_line(
        [
            "!CUST",
            "NAME",
            "COMPANYNAME",
            "FIRSTNAME",
            "LASTNAME",
            "ADDR1",
            "ADDR2",
            "ADDR3",
            "ADDR4",
            "ADDR5",
            "PHONE1",
            "PHONE2",
            "EMAIL",
            "TERMS",
            "TAXID",
            "LIMIT",
        ]
    )

    customers = (
        db.query(Customer).filter(Customer.is_active).order_by(Customer.name).all()
    )

    lines = header
    for c in customers:
        # Split name into first/last (best effort)
        parts = (c.name or "").split(" ", 1)
        first = parts[0] if parts else ""
        last = parts[1] if len(parts) > 1 else ""

        # QB address convention: ADDR1=company/name, ADDR2-3=street, ADDR4=city/state/zip
        addr1 = c.company or c.name or ""
        addr2 = c.bill_address1 or ""
        addr3 = c.bill_address2 or ""
        city_st_zip = ", ".join(
            filter(
                None,
                [
                    c.bill_city,
                    (
                        f"{c.bill_state} {c.bill_zip}".strip()
                        if (c.bill_state or c.bill_zip)
                        else None
                    ),
                ],
            )
        )

        lines += _iif_line(
            [
                "CUST",
                _iif_text(c.name),
                _iif_text(c.company),
                _iif_text(first),
                _iif_text(last),
                _iif_text(addr1),
                _iif_text(addr2),
                _iif_text(addr3),
                _iif_text(city_st_zip),
                "",  # ADDR5
                c.phone or "",
                c.mobile or "",
                c.email or "",
                c.terms or "",
                c.tax_id or "",
                str(c.credit_limit) if c.credit_limit else "",
            ]
        )

        # QuickBooks "Customer:Job" — every job goes out as a CUST row under
        # its customer, which is exactly what the importer splits back.
        for job in sorted(getattr(c, "jobs", None) or [], key=lambda j: j.name):
            lines += _iif_line(["CUST", _iif_text(f"{c.name}:{job.name}")] + [""] * 14)
    return lines


def export_vendors(db: Session) -> str:
    """Export vendors as !VEND section."""
    header = _iif_line(
        [
            "!VEND",
            "NAME",
            "ADDR1",
            "ADDR2",
            "ADDR3",
            "ADDR4",
            "ADDR5",
            "PHONE1",
            "PHONE2",
            "EMAIL",
            "TERMS",
            "TAXID",
        ]
    )

    vendors = db.query(Vendor).filter(Vendor.is_active).order_by(Vendor.name).all()

    lines = header
    for v in vendors:
        addr1 = v.company or v.name or ""
        addr2 = v.address1 or ""
        addr3 = v.address2 or ""
        city_st_zip = ", ".join(
            filter(
                None,
                [
                    v.city,
                    f"{v.state} {v.zip}".strip() if (v.state or v.zip) else None,
                ],
            )
        )

        lines += _iif_line(
            [
                "VEND",
                _iif_text(v.name),
                _iif_text(addr1),
                _iif_text(addr2),
                _iif_text(addr3),
                _iif_text(city_st_zip),
                "",  # ADDR5
                v.phone or "",
                v.fax or "",
                v.email or "",
                v.terms or "",
                v.tax_id or "",
            ]
        )

    return lines


def export_items(db: Session) -> str:
    """Export items as !INVITEM section."""
    header = _iif_line(
        [
            "!INVITEM",
            "NAME",
            "INVITEMTYPE",
            "DESC",
            "ACCNT",
            "PRICE",
            "TAXABLE",
        ]
    )

    items = db.query(Item).filter(Item.is_active).order_by(Item.name).all()

    lines = header
    for item in items:
        acct_name = _resolve_account_name(db, item.income_account_id)
        lines += _iif_line(
            [
                "INVITEM",
                _iif_text(item.name),
                item_to_iif_type(item),
                _iif_text(item.description),
                acct_name,
                str(item.rate) if item.rate else "0",
                "Y" if item.is_taxable else "N",
            ]
        )

    return lines


def export_invoices(db: Session, date_from: date = None, date_to: date = None) -> str:
    """Export invoices as !TRNS/!SPL/ENDTRNS transaction blocks.

    Sign convention per QB IIF spec:
      TRNS line: positive amount (debit to Accounts Receivable)
      SPL lines: negative amount (credit to income accounts)
      Sum of TRNS + all SPL = 0 (balanced transaction)
    """
    # Transaction header — defines columns for both TRNS and SPL lines
    header = _txn_header(_TXN_COLUMNS)

    query = (
        db.query(Invoice)
        .options(
            joinedload(Invoice.customer),
            joinedload(Invoice.lines).joinedload(InvoiceLine.item),
        )
        .filter(Invoice.status != InvoiceStatus.VOID)
        .filter(Invoice.is_sales_receipt.is_(False))
    )

    if date_from:
        query = query.filter(Invoice.date >= date_from)
    if date_to:
        query = query.filter(Invoice.date <= date_to)

    invoices = query.order_by(Invoice.date, Invoice.id).all()

    lines = header
    for inv in invoices:
        cls = _class_name(db, getattr(inv, "class_id", None))
        cust_name = _iif_text(inv.customer.name) if inv.customer else ""
        inv_date = _iif_date(inv.date)
        due_date = _iif_date(inv.due_date)
        splits, tax_amt, home = _sale_amounts(inv)
        total_home = home[0][0]

        # TRNS line — debit A/R for full invoice amount
        lines += _iif_line(
            [
                "TRNS",
                "INVOICE",
                inv_date,
                "Accounts Receivable",
                cust_name,
                str(total_home),
                inv.invoice_number or "",
                due_date,
                inv.terms or "",
                "",
                cls,
            ]
        )

        # SPL lines — credit income accounts for each line item
        for il, (_dr, credit) in zip(splits, home[1:]):
            acct_name = ""
            if il.item and il.item.income_account_id:
                acct_name = _resolve_account_name(db, il.item.income_account_id)
            if not acct_name:
                acct_name = "Service Income"  # fallback

            lines += _iif_line(
                [
                    "SPL",
                    "INVOICE",
                    inv_date,
                    acct_name,
                    cust_name,
                    str(-credit),
                    inv.invoice_number or "",
                    "",
                    "",
                    _iif_text(il.description),
                    cls,
                ]
            )

        # SPL line for tax if applicable
        if tax_amt > 0:
            lines += _iif_line(
                [
                    "SPL",
                    "INVOICE",
                    inv_date,
                    "Sales Tax Payable",
                    cust_name,
                    str(-home[-1][1]),
                    inv.invoice_number or "",
                    "",
                    "",
                    "Sales Tax",
                    cls,
                ]
            )

        lines += _iif_line(["ENDTRNS"])

    return lines


def _sale_amounts(inv):
    """An invoice's (or sales receipt's) split lines, its tax, and every
    amount of the block in home currency: [total, one per split, tax if
    any] as (debit, credit), converted the way the invoice's journal entry
    was (routes/invoices/helpers.py: the A/R debit, one credit per non-zero
    line, then the tax)."""
    splits = [il for il in inv.lines if Decimal(str(il.amount or 0)) != 0]
    tax_amt = Decimal(str(inv.tax_amount or 0))
    pairs = [(Decimal(str(inv.total or 0)), Decimal("0"))]
    pairs += [(Decimal("0"), Decimal(str(il.amount))) for il in splits]
    if tax_amt > 0:
        pairs.append((Decimal("0"), tax_amt))
    return splits, tax_amt, _home(pairs, _rate(inv))


def export_payments(db: Session, date_from: date = None, date_to: date = None) -> str:
    """Export payments as !TRNS/!SPL/ENDTRNS transaction blocks.

    Sign convention:
      TRNS line: positive amount (debit to deposit account / bank)
      SPL line:  negative amount (credit to Accounts Receivable)
    """
    header = _txn_header(_TXN_COLUMNS_SHORT)

    query = (
        db.query(Payment)
        .options(
            joinedload(Payment.customer),
            joinedload(Payment.deposit_to_account),
            joinedload(Payment.allocations).joinedload(PaymentAllocation.invoice),
        )
        .filter(Payment.is_voided == False)  # noqa: E712
    )

    if date_from:
        query = query.filter(Payment.date >= date_from)
    if date_to:
        query = query.filter(Payment.date <= date_to)

    payments = query.order_by(Payment.date, Payment.id).all()

    lines = header
    fx_name = None
    for pmt in payments:
        cls = _class_name(db, getattr(pmt, "class_id", None))
        cust_name = _iif_text(pmt.customer.name) if pmt.customer else ""
        pmt_date = _iif_date(pmt.date)
        amount = Decimal(str(pmt.amount or 0))
        pay_rate = _rate(pmt)

        # Deposit account name
        deposit_acct = "Undeposited Funds"
        if pmt.deposit_to_account:
            deposit_acct = _full_account_name(db, pmt.deposit_to_account)

        ref = pmt.reference or pmt.check_number or ""

        # Home currency, as routes/payments.py posted it: the cash at the
        # payment's rate, the A/R each allocation relieved at its invoice's
        # booked rate, any unallocated remainder at the payment's rate, and
        # the difference as realized exchange gain or loss.
        cash_home = to_home(amount, pay_rate)
        splits = []
        allocated = Decimal("0")
        for alloc in pmt.allocations or []:
            alloc_amt = Decimal(str(alloc.amount or 0))
            allocated += alloc_amt
            inv = alloc.invoice
            splits.append(
                (
                    "Accounts Receivable",
                    to_home(alloc_amt, _rate(inv) if inv else pay_rate),
                    (inv.invoice_number or "") if inv else "",
                )
            )
        remainder = amount - allocated
        if remainder or not splits:
            # A payment's unapplied remainder is still credited to A/R (a
            # customer credit); leaving it out left the block unbalanced.
            splits.append(("Accounts Receivable", to_home(remainder, pay_rate), ""))
        residual = cash_home - sum((amt for _a, amt, _d in splits), Decimal("0"))
        if residual:
            if fx_name is None:
                fx_name = _fx_account_name(db)
            splits.append((fx_name, residual, ""))

        # TRNS line — debit bank/deposit account
        lines += _iif_line(
            [
                "TRNS",
                "PAYMENT",
                pmt_date,
                deposit_acct,
                cust_name,
                str(cash_home),
                ref,
                _iif_text(pmt.notes),
                cls,
            ]
        )

        # SPL lines — credit A/R once per allocation (DOCNUM = the invoice),
        # then any remainder and any exchange difference
        for acct_name, credit, doc_num in splits:
            lines += _iif_line(
                [
                    "SPL",
                    "PAYMENT",
                    pmt_date,
                    acct_name,
                    cust_name,
                    str(-credit),
                    doc_num,
                    "",
                    cls,
                ]
            )

        lines += _iif_line(["ENDTRNS"])

    return lines


def _fx_account_name(db: Session) -> str:
    """The realized exchange gain/loss account, looked up the way
    app.services.currency finds it — but never created: an export writes
    nothing."""
    from app.services.currency import FX_ACCOUNT_NAME, FX_ACCOUNT_NUMBER

    acct = (
        db.query(Account).filter(Account.account_number == FX_ACCOUNT_NUMBER).first()
        or db.query(Account).filter(Account.name == FX_ACCOUNT_NAME).first()
    )
    return _full_account_name(db, acct) if acct else FX_ACCOUNT_NAME


def export_estimates(db: Session) -> str:
    """Export estimates as !TRNS/!SPL/ENDTRNS with TRNSTYPE=ESTIMATE.

    Estimates don't post to A/R in QB2003 — they're non-posting transactions.
    But the IIF format is identical to invoices with ESTIMATE type.
    """
    header = _txn_header(_TXN_COLUMNS_SHORT)

    estimates = (
        db.query(Estimate)
        .options(
            joinedload(Estimate.customer),
            joinedload(Estimate.lines).joinedload(EstimateLine.item),
        )
        .order_by(Estimate.date, Estimate.id)
        .all()
    )

    lines = header
    for est in estimates:
        cls = _class_name(db, getattr(est, "class_id", None))
        cust_name = _iif_text(est.customer.name) if est.customer else ""
        est_date = _iif_date(est.date)
        total = Decimal(str(est.total or 0))

        lines += _iif_line(
            [
                "TRNS",
                "ESTIMATE",
                est_date,
                "Accounts Receivable",
                cust_name,
                str(total),
                est.estimate_number or "",
                _iif_text(est.notes),
                cls,
            ]
        )

        for el in est.lines:
            amt = Decimal(str(el.amount or 0))
            if amt == 0:
                continue
            acct_name = ""
            if el.item and el.item.income_account_id:
                acct_name = _resolve_account_name(db, el.item.income_account_id)
            if not acct_name:
                acct_name = "Service Income"

            lines += _iif_line(
                [
                    "SPL",
                    "ESTIMATE",
                    est_date,
                    acct_name,
                    cust_name,
                    str(-amt),
                    est.estimate_number or "",
                    _iif_text(el.description),
                    cls,
                ]
            )

        tax_amt = Decimal(str(est.tax_amount or 0))
        if tax_amt > 0:
            lines += _iif_line(
                [
                    "SPL",
                    "ESTIMATE",
                    est_date,
                    "Sales Tax Payable",
                    cust_name,
                    str(-tax_amt),
                    est.estimate_number or "",
                    "Sales Tax",
                    cls,
                ]
            )

        lines += _iif_line(["ENDTRNS"])

    return lines


def export_classes(db: Session) -> str:
    """!CLASS list — names verbatim (Parent:Child paths round-trip), archived
    classes as HIDDEN=Y. The importer's mirror image."""
    from app.models.classes import TxnClass

    lines = _iif_line(["!CLASS", "NAME", "HIDDEN"])
    rows = (
        db.query(TxnClass)
        .filter(TxnClass.is_system_default.is_(False))
        .order_by(TxnClass.name)
        .all()
    )
    for c in rows:
        lines += _iif_line(["CLASS", _iif_text(c.name), "Y" if c.is_archived else "N"])
    return lines


def _row(
    kind, trnstype, d, acct, name, amount, docnum="", due="", terms="", memo="", cls=""
):
    return _iif_line(
        [
            kind,
            trnstype,
            d,
            acct,
            _iif_text(name),
            str(amount),
            _iif_clean(docnum),
            due,
            terms,
            _iif_text(memo),
            cls,
        ]
    )


def export_bills(db: Session, date_from: date = None, date_to: date = None) -> str:
    """Bills as BILL blocks. QB convention: TRNS is the A/P credit (negative),
    each SPL the expense debit (positive); the importer reads abs() so
    either sign re-imports."""
    from app.models.bills import Bill, BillStatus
    from app.services.control_accounts import find

    # Display name only: an export must not fail because a chart is odd.
    ap_name = _resolve_account_name(db, find(db, "2000")) or "Accounts Payable"
    q = (
        db.query(Bill)
        .options(joinedload(Bill.vendor), joinedload(Bill.lines))
        .filter(Bill.status != BillStatus.VOID)
    )
    if date_from:
        q = q.filter(Bill.date >= date_from)
    if date_to:
        q = q.filter(Bill.date <= date_to)
    lines = _txn_header()
    for bill in q.order_by(Bill.date, Bill.id).all():
        cls = _class_name(db, bill.class_id)
        vendor = bill.vendor.name if bill.vendor else ""
        splits = [bl for bl in bill.lines if Decimal(str(bl.amount or 0)) != 0]
        tax = Decimal(str(bill.tax_amount or 0))
        # Home currency, in the order routes/bills.py built the journal:
        # each line's debit, the tax, then the A/P credit.
        pairs = [(Decimal(str(bl.amount)), Decimal("0")) for bl in splits]
        if tax > 0:
            pairs.append((tax, Decimal("0")))
        pairs.append((Decimal("0"), Decimal(str(bill.total or 0))))
        home = _home(pairs, _rate(bill))
        lines += _row(
            "TRNS",
            "BILL",
            _iif_date(bill.date),
            ap_name,
            vendor,
            -home[-1][1],
            bill.bill_number or "",
            _iif_date(bill.due_date),
            bill.terms or "",
            bill.notes or "",
            cls,
        )
        for bl, (debit, _cr) in zip(splits, home):
            acct = _resolve_account_name(db, bl.account_id) if bl.account_id else ""
            if not acct and bl.item and bl.item.expense_account_id:
                acct = _resolve_account_name(db, bl.item.expense_account_id)
            lines += _row(
                "SPL",
                "BILL",
                _iif_date(bill.date),
                acct or "Uncategorized Expenses",
                vendor,
                debit,
                bill.bill_number or "",
                "",
                "",
                bl.description or "",
                cls,
            )
        if tax > 0:
            lines += _row(
                "SPL",
                "BILL",
                _iif_date(bill.date),
                "Sales Tax Payable",
                vendor,
                home[len(splits)][0],
                bill.bill_number or "",
                "",
                "",
                "Sales tax",
                cls,
            )
        lines += _iif_line(["ENDTRNS"])
    return lines


def export_deposits(db: Session, date_from: date = None, date_to: date = None) -> str:
    """Make Deposits entries (journal-only transactions, source_type
    'deposit') as DEPOSIT blocks: TRNS = the bank debit (positive), SPL =
    each source credit (negative)."""
    from app.models.transactions import Transaction

    q = db.query(Transaction).filter(Transaction.source_type == "deposit")
    if date_from:
        q = q.filter(Transaction.date >= date_from)
    if date_to:
        q = q.filter(Transaction.date <= date_to)
    lines = _txn_header()
    for txn in q.order_by(Transaction.date, Transaction.id).all():
        debits = [ln for ln in txn.lines if ln.debit and ln.debit > 0]
        credits = [ln for ln in txn.lines if ln.credit and ln.credit > 0]
        if not debits:
            continue
        cls = _class_name(db, txn.class_id)
        bank = debits[0]
        lines += _row(
            "TRNS",
            "DEPOSIT",
            _iif_date(txn.date),
            _resolve_account_name(db, bank.account_id),
            "",
            Decimal(str(bank.debit)),
            txn.reference or "",
            "",
            "",
            txn.description or "",
            cls,
        )
        for ln in credits:
            lines += _row(
                "SPL",
                "DEPOSIT",
                _iif_date(txn.date),
                _resolve_account_name(db, ln.account_id),
                "",
                -Decimal(str(ln.credit)),
                txn.reference or "",
                "",
                "",
                ln.description or "",
                cls,
            )
        lines += _iif_line(["ENDTRNS"])
    return lines


def export_sales_receipts(
    db: Session, date_from: date = None, date_to: date = None
) -> str:
    """Sales receipts (invoices flagged is_sales_receipt) as CASH SALE
    blocks: TRNS = the deposit account (positive), SPL = income per line
    and the tax line (negative) — the shape the importer creates a paid
    invoice + same-day payment from."""
    from app.models.payments import Payment
    from app.services.control_accounts import find

    default_dep = _resolve_account_name(db, find(db, "1200")) or "Undeposited Funds"
    q = (
        db.query(Invoice)
        .options(
            joinedload(Invoice.customer),
            joinedload(Invoice.lines).joinedload(InvoiceLine.item),
        )
        .filter(
            Invoice.status != InvoiceStatus.VOID, Invoice.is_sales_receipt.is_(True)
        )
    )
    if date_from:
        q = q.filter(Invoice.date >= date_from)
    if date_to:
        q = q.filter(Invoice.date <= date_to)
    lines = _txn_header()
    for inv in q.order_by(Invoice.date, Invoice.id).all():
        cls = _class_name(db, inv.class_id)
        cust = inv.customer.name if inv.customer else ""
        pay = (
            db.query(Payment)
            .filter(Payment.customer_id == inv.customer_id, Payment.date == inv.date)
            .order_by(Payment.id.desc())
            .first()
        )
        dep_name = (
            _resolve_account_name(db, pay.deposit_to_account_id)
            if pay and pay.deposit_to_account_id
            else default_dep
        )
        d = _iif_date(inv.date)
        splits, tax, home = _sale_amounts(inv)
        lines += _row(
            "TRNS",
            "CASH SALE",
            d,
            dep_name,
            cust,
            home[0][0],
            inv.invoice_number or "",
            "",
            "",
            inv.notes or "",
            cls,
        )
        for il, (_dr, credit) in zip(splits, home[1:]):
            acct = ""
            if il.item and il.item.income_account_id:
                acct = _resolve_account_name(db, il.item.income_account_id)
            lines += _row(
                "SPL",
                "CASH SALE",
                d,
                acct or "Service Income",
                cust,
                -credit,
                inv.invoice_number or "",
                "",
                "",
                il.description or "",
                cls,
            )
        if tax > 0:
            lines += _row(
                "SPL",
                "CASH SALE",
                d,
                "Sales Tax Payable",
                cust,
                -home[-1][1],
                inv.invoice_number or "",
                "",
                "",
                "Sales Tax",
                cls,
            )
        lines += _iif_line(["ENDTRNS"])
    return lines


def export_all(db: Session) -> str:
    """Export all Slowbooks data as a single IIF file.

    Order matters — QB2003 imports lists before transactions,
    and accounts must exist before items can reference them.
    """
    sections = [
        export_accounts(db),
        export_classes(db),
        export_customers(db),
        export_vendors(db),
        export_items(db),
        export_estimates(db),
        export_invoices(db),
        export_sales_receipts(db),
        export_payments(db),
        export_bills(db),
        export_deposits(db),
    ]
    return "\r\n".join(s for s in sections if s.strip())
