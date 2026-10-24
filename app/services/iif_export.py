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
from app.services.addresses import city_line
from app.services.csv_export import _Identifier, _csv_safe
from app.services.currency import convert_lines, to_home
from app.services.iif_common import account_to_iif_type, item_to_iif_type
from app.services.entry_text import is_bank_entry_placeholder


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
    text; the importer takes it off again. Only names, memos and the like go
    through here — never an amount (those are written as numbers directly) —
    so a memo that reads "-0001" keeps its apostrophe rather than opening as
    -1 if the file is edited in Excel (reviewer, #410)."""
    return _csv_safe(_Identifier(_iif_clean(value)))


def _iif_verbatim(value) -> str:
    """A phone, fax, email, terms or tax id: a value QuickBooks Desktop
    stores as typed, or matches against its Terms list, and it does not take
    Excel's apostrophe off. So only a leading "=" or "@" is guarded, which
    no real phone, email or terms starts with; "+15551234567" and "-0001"
    go out as typed and reach QuickBooks clean (owner decision on #449).
    Names keep _iif_text's full guard (#410). The importer takes the
    apostrophe off again, as it does for a name."""
    s = _iif_clean(value)
    if s[:1] in ("=", "@"):
        return "'" + s
    return s


# Letters with no Windows-1252 form and no accent to drop (NFKD leaves
# them whole), spelled the way a person would type them without the key.
_ANSI_PLAIN = {
    "Ł": "L",
    "ł": "l",
    "Đ": "D",
    "đ": "d",
    "ı": "i",
    "Ħ": "H",
    "ħ": "h",
    # the app's own transfer memo ("Savings → Checking") and the like
    "→": "->",
    "←": "<-",
    "↔": "<->",
    "⇒": "=>",
}


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


_IMPORT_PREFIXES = ("IIF Import — ", "QB report import — ")


def _memo_text(description) -> str:
    """A transaction's description as the file's MEMO, without the prefix
    an import wrote in front of it ("IIF Import — Two entities"): a round
    trip added another prefix each time (2.23.1 gate, NEW-100)."""
    text = (description or "").strip()
    changed = True
    while changed:
        changed = False
        for prefix in _IMPORT_PREFIXES:
            if text.startswith(prefix):
                text = text[len(prefix) :].strip()
                changed = True
    return text


# A split row carries its line's own class when the line has one, else the
# document's (2.23.1, #308: every split row wrote the header's class, so a
# document whose lines belonged to two entities left with all of them under
# one; line-level classes exist since 2.22.0).
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
# The split-row columns of a sale (invoice, sales receipt): the item, the
# quantity, the unit price and whether the line is taxable, after the
# shared columns — on !SPL rows only, which is where QuickBooks takes them
# (INVITEM on a !TRNS header is error 15101 in QuickBooks 2019+). Per
# Intuit's own sample import files: QNTY negative and PRICE positive on a
# sale, AMOUNT = QNTY × PRICE; the TRNS row's NAME is the full
# Customer:Job (#199: the export wrote the customer alone and no item,
# quantity or price, so a round trip lost the job and every quantity).
_SPL_ITEM_COLUMNS = _TXN_COLUMNS + ["INVITEM", "QNTY", "PRICE", "TAXABLE"]
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


def _txn_header(
    columns: list[str] = _TXN_COLUMNS, spl_columns: list[str] | None = None
) -> str:
    """!TRNS / !SPL / !ENDTRNS header trio; the !SPL row may declare more
    columns than the !TRNS row (a sale's item columns)."""
    return (
        _iif_line(["!TRNS"] + columns)
        + _iif_line(["!SPL"] + (spl_columns or columns))
        + _iif_line(["!ENDTRNS"])
    )


def _plain(value) -> str:
    """A quantity or a unit price as QuickBooks writes it: no exponent, no
    trailing zeros (25.0000 → 25, 1.50 → 1.5)."""
    text = format(Decimal(str(value or 0)), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _item_fields(il, rate: Decimal = Decimal("1"), home_amount=None) -> list[str]:
    """INVITEM, QNTY, PRICE, TAXABLE for one sale line, the way Intuit's
    sample files carry them: the item's name, the quantity negative, the
    unit price positive, Y/N. Every amount in the file is in the home
    currency, so on a document in another currency PRICE is the line's
    home-currency amount — the one its journal was booked at — divided by
    the quantity, to the places the quantity needs (four at least, two
    more than the quantity has digits), so QNTY × PRICE gives AMOUNT to
    the cent at any quantity (2.23.0 gate, W-4; then W-6 / NEW-93: a
    rate converted and rounded to four places missed by 4 cents at a
    quantity of 1,000)."""
    qty = Decimal(str(il.quantity if il.quantity is not None else 1))
    price = Decimal(str(il.rate or 0))
    if rate != 1 and home_amount is not None and qty:
        places = max(4, len(str(int(abs(qty)))) + 2)
        price = (Decimal(str(home_amount)) / qty).quantize(Decimal(1).scaleb(-places))
    elif rate != 1:
        price = (price * rate).quantize(Decimal("0.0001"))
    return [
        _iif_text(il.item.name) if il.item else "",
        _plain(-qty),
        _plain(price),
        "N" if getattr(il, "is_taxable", True) is False else "Y",
    ]


def _customer_job(db: Session, customer, job_id) -> str:
    """QuickBooks' NAME for a sale, raw: "Customer" or "Customer:Job" (the
    caller cleans it as it does any name)."""
    name = customer.name if customer else ""
    if job_id:
        from app.models.jobs import Job

        job = db.get(Job, job_id)
        if job and job.name:
            name = f"{name}:{job.name}"
    return name


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
    that names the account go through here, so they still match. Stops at
    a repeat, so a parent loop saved before Testing #101 can't recurse."""
    names, seen, cur = [], set(), acct
    while cur is not None and cur.id not in seen:
        seen.add(cur.id)
        names.append(cur.name)
        cur = db.get(Account, cur.parent_id) if cur.parent_id else None
    return _iif_text(":".join(reversed(names)))


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


def _address_lines(street1, street2, city, state, zip_code) -> tuple:
    """ADDR2, ADDR3 and ADDR4 of a customer or vendor: the two street lines
    and "City, ST ZIP". A blank part is left out rather than written: a
    customer with no ZIP went out as "Astoria, OR None" (the Python None
    formatted into the line) and one with no state as "None 97103", which
    QuickBooks and our own importer then read as the ZIP code or state
    (2.18.0 gate, NEW-7)."""
    return (
        (street1 or "").strip(),
        (street2 or "").strip(),
        city_line(city, state, zip_code),
    )


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
        addr2, addr3, city_st_zip = _address_lines(
            c.bill_address1, c.bill_address2, c.bill_city, c.bill_state, c.bill_zip
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
                # Contact fields: a phone or email a file carries as
                # `=HYPERLINK(...)` would run in Excel the same as a name
                # would, so a leading = or @ is guarded; "+1 555..." and
                # "-0001" reach QuickBooks as typed (see _iif_verbatim).
                _iif_verbatim(c.phone),
                _iif_verbatim(c.mobile),
                _iif_verbatim(c.email),
                _iif_verbatim(c.terms),
                _iif_verbatim(c.tax_id),
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
        addr2, addr3, city_st_zip = _address_lines(
            v.address1, v.address2, v.city, v.state, v.zip
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
                _iif_verbatim(v.phone),
                _iif_verbatim(v.fax),
                _iif_verbatim(v.email),
                _iif_verbatim(v.terms),
                _iif_verbatim(v.tax_id),
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
    # Transaction header — the SPL rows carry the item columns too (#199)
    header = _txn_header(_TXN_COLUMNS, _SPL_ITEM_COLUMNS)

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
        # the TRNS row names the Customer:Job, so the job survives the trip
        header_name = _iif_text(
            _customer_job(db, inv.customer, getattr(inv, "job_id", None))
        )
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
                header_name,
                str(total_home),
                inv.invoice_number or "",
                due_date,
                _iif_verbatim(inv.terms),
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
                    _class_name(db, getattr(il, "class_id", None)) or cls,
                ]
                + _item_fields(il, _rate(inv), credit)
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
                    "",
                    "",
                    "",
                    "",
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
        # A sales receipt is an invoice plus its own payment here, but one
        # CASH SALE in QuickBooks, and export_sales_receipts writes that
        # block. The payment went out as well — applied to the receipt's
        # number — so QuickBooks got the money twice and the customer a
        # phantom credit. What the receipt covers stays out of this block.
        allocations = [
            a
            for a in (pmt.allocations or [])
            if not (a.invoice and a.invoice.is_sales_receipt)
        ]
        receipt_part = sum(
            (
                Decimal(str(a.amount or 0))
                for a in (pmt.allocations or [])
                if a not in allocations
            ),
            Decimal("0"),
        )
        amount = Decimal(str(pmt.amount or 0)) - receipt_part
        if receipt_part and amount <= 0:
            continue
        cls = _class_name(db, getattr(pmt, "class_id", None))
        cust_name = _iif_text(pmt.customer.name) if pmt.customer else ""
        pmt_date = _iif_date(pmt.date)
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
        for alloc in allocations:
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
                    _class_name(db, getattr(el, "class_id", None)) or cls,
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
    kind,
    trnstype,
    d,
    acct,
    name,
    amount,
    docnum="",
    due="",
    terms="",
    memo="",
    cls="",
    extra=None,
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
            _iif_verbatim(terms),
            _iif_text(memo),
            cls,
        ]
        + list(extra or [])
    )


def _tax_posted_to_sales_tax_payable(db: Session, bill, tax_account_id) -> bool:
    """Whether this bill's journal debited Sales Tax Payable with its tax —
    how bills were posted before purchase tax became part of line cost."""
    if not tax_account_id or not bill.transaction_id:
        return False
    from app.models.transactions import TransactionLine

    return (
        db.query(TransactionLine.id)
        .filter(
            TransactionLine.transaction_id == bill.transaction_id,
            TransactionLine.account_id == tax_account_id,
            TransactionLine.debit > 0,
        )
        .first()
        is not None
    )


def export_bills(db: Session, date_from: date = None, date_to: date = None) -> str:
    """Bills as BILL blocks, at the amounts the ledger booked. QB
    convention: TRNS is the A/P credit (negative), each SPL the expense
    debit (positive); the importer reads abs() so either sign re-imports.

    Sales tax a supplier charges is part of what the purchase cost: the
    posting spreads it over the lines in proportion to their amounts and
    debits each line's account with its amount plus its share
    (services/purchase_posting.py), and nothing goes to Sales Tax Payable.
    This export still wrote the tax as its own split to Sales Tax Payable,
    so QuickBooks took the tax paid to a supplier off the sales tax owed —
    the posting the ledger stopped making for explore 2.17.3 (macbase1 F9;
    the export was found still making it while integrating the fixes).
    Each split now carries its line's share. A bill posted before that
    change still has its Sales Tax Payable debit in the ledger, and goes
    out as it was booked."""
    from app.models.bills import Bill, BillStatus
    from app.services.control_accounts import find
    from app.services.purchase_posting import spread

    # Display names only: an export must not fail because a chart is odd.
    ap_name = _resolve_account_name(db, find(db, "2000")) or "Accounts Payable"
    tax_account_id = find(db, "2200")
    tax_name = _resolve_account_name(db, tax_account_id) or "Sales Tax Payable"
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
        # The order the posting code built its journal in: the lines as they
        # were entered (by id), each line's debit, [the tax,] the A/P credit.
        entered = sorted(bill.lines, key=lambda bl: bl.id)
        splits = [bl for bl in entered if Decimal(str(bl.amount or 0)) != 0]
        tax = Decimal(str(bill.tax_amount or 0))
        tax_on_its_own = tax > 0 and _tax_posted_to_sales_tax_payable(
            db, bill, tax_account_id
        )
        if tax_on_its_own:
            pairs = [(Decimal(str(bl.amount)), Decimal("0")) for bl in splits]
            pairs.append((tax, Decimal("0")))
        else:
            shares = dict(
                zip(
                    (bl.id for bl in entered),
                    spread(tax, [bl.amount for bl in entered]),
                )
            )
            pairs = [
                (Decimal(str(bl.amount)) + shares[bl.id], Decimal("0")) for bl in splits
            ]
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
                _class_name(db, getattr(bl, "class_id", None)) or cls,
            )
        if tax_on_its_own:
            lines += _row(
                "SPL",
                "BILL",
                _iif_date(bill.date),
                tax_name,
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
            _memo_text(txn.description),
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
                _class_name(db, getattr(ln, "class_id", None)) or cls,
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
    lines = _txn_header(_TXN_COLUMNS, _SPL_ITEM_COLUMNS)
    for inv in q.order_by(Invoice.date, Invoice.id).all():
        cls = _class_name(db, inv.class_id)
        cust = inv.customer.name if inv.customer else ""
        header_name = _customer_job(db, inv.customer, getattr(inv, "job_id", None))
        pay = (
            db.query(Payment)
            .join(PaymentAllocation, PaymentAllocation.payment_id == Payment.id)
            .filter(
                PaymentAllocation.invoice_id == inv.id,
                Payment.customer_id == inv.customer_id,
                Payment.is_voided.is_(False),
            )
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
            header_name,
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
                _class_name(db, getattr(il, "class_id", None)) or cls,
                extra=_item_fields(il, _rate(inv), credit),
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
                extra=["", "", "", ""],
            )
        lines += _iif_line(["ENDTRNS"])
    return lines


# ── The rest of the ledger (#344) ────────────────────────────────────────
# Until 2.24.0 the export stopped at deposits: checks, expenses, card
# charges, transfers, bill payments and journal entries stayed behind, so
# an accountant reviewing in QuickBooks Desktop saw part of the books. Each
# goes out now in the block QuickBooks keeps it in.


def _is_card(acct) -> bool:
    kind = str(getattr(acct, "bank_kind", "") or "").lower()
    return "card" in kind or kind in ("cc", "credit")


def _register_side(db: Session, txn):
    """The line on the bank or card account of a register entry, and the
    other lines; None when no line is on such an account."""
    from app.models.accounts import Account

    lines = sorted(txn.lines, key=lambda ln: ln.id)
    accounts = {
        a.id: a
        for a in db.query(Account)
        .filter(Account.id.in_({ln.account_id for ln in lines}))
        .all()
    }
    on_register = [
        ln for ln in lines if getattr(accounts.get(ln.account_id), "bank_kind", None)
    ]
    if not on_register:
        return None, lines, accounts
    side = on_register[0]
    return side, [ln for ln in lines if ln.id != side.id], accounts


def _dated(q, model, date_from, date_to):
    if date_from:
        q = q.filter(model.date >= date_from)
    if date_to:
        q = q.filter(model.date <= date_to)
    return q


def _signed(ln) -> Decimal:
    """A line's amount as QuickBooks writes it: a debit positive, a credit
    negative."""
    return Decimal(str(ln.debit or 0)) - Decimal(str(ln.credit or 0))


def export_register_entries(
    db: Session, date_from: date = None, date_to: date = None
) -> str:
    """Register entries (source_type bank_entry, check, cc_charge) as the
    block QuickBooks keeps each in: money out of a bank account is a CHECK,
    money into one a DEPOSIT, a charge on a card account a CREDIT CARD.
    TRNS = the register account's line, SPL = each category line, each
    with its sign (a debit positive, a credit negative)."""
    from app.models.transactions import Transaction

    q = _dated(
        db.query(Transaction).filter(
            Transaction.source_type.in_(("bank_entry", "check", "cc_charge"))
        ),
        Transaction,
        date_from,
        date_to,
    )
    lines = _txn_header(_TXN_COLUMNS_SHORT)
    for txn in q.order_by(Transaction.date, Transaction.id).all():
        side, others, accounts = _register_side(db, txn)
        if side is None or not others:
            continue
        acct = accounts.get(side.account_id)
        amount = _signed(side)
        if _is_card(acct):
            trnstype = "CREDIT CARD"
        elif amount < 0:
            trnstype = "CHECK"
        else:
            trnstype = "DEPOSIT"
        cls = _class_name(db, txn.class_id)
        # the register stores the payee as the entry's description; a card
        # charge's posting prefixes it
        # a register entry typed with no payee carries a placeholder there,
        # which is no one's name
        name = (
            ""
            if is_bank_entry_placeholder(txn)
            else (txn.description or "").removeprefix("CC Charge: ").strip()
        )
        memo = _memo_text(
            side.description
            or ("" if is_bank_entry_placeholder(txn) else txn.description)
        )
        lines += _iif_line(
            [
                "TRNS",
                trnstype,
                _iif_date(txn.date),
                _resolve_account_name(db, side.account_id),
                _iif_text(name),
                str(amount),
                _iif_clean(txn.reference or ""),
                _iif_text(memo),
                cls,
            ]
        )
        for ln in others:
            lines += _iif_line(
                [
                    "SPL",
                    trnstype,
                    _iif_date(txn.date),
                    _resolve_account_name(db, ln.account_id),
                    _iif_text(name),
                    str(_signed(ln)),
                    _iif_clean(txn.reference or ""),
                    _iif_text(ln.description or ""),
                    _class_name(db, getattr(ln, "class_id", None)) or cls,
                ]
            )
        lines += _iif_line(["ENDTRNS"])
    return lines


def export_transfers(db: Session, date_from: date = None, date_to: date = None) -> str:
    """Transfers between bank and card accounts as TRANSFER blocks: TRNS =
    the account the money left (negative), SPL = the one it reached."""
    from app.models.transactions import Transaction

    q = _dated(
        db.query(Transaction).filter(Transaction.source_type == "transfer"),
        Transaction,
        date_from,
        date_to,
    )
    lines = _txn_header(_TXN_COLUMNS_SHORT)
    for txn in q.order_by(Transaction.date, Transaction.id).all():
        credits = [ln for ln in txn.lines if ln.credit and ln.credit > 0]
        debits = [ln for ln in txn.lines if ln.debit and ln.debit > 0]
        if not credits or not debits:
            continue
        cls = _class_name(db, txn.class_id)
        memo = _memo_text(txn.description)
        ref = _iif_clean(txn.reference or "")
        src = credits[0]
        lines += _iif_line(
            [
                "TRNS",
                "TRANSFER",
                _iif_date(txn.date),
                _resolve_account_name(db, src.account_id),
                "",
                str(-Decimal(str(src.credit))),
                ref,
                _iif_text(memo),
                cls,
            ]
        )
        for ln in debits:
            lines += _iif_line(
                [
                    "SPL",
                    "TRANSFER",
                    _iif_date(txn.date),
                    _resolve_account_name(db, ln.account_id),
                    "",
                    str(Decimal(str(ln.debit))),
                    ref,
                    _iif_text(ln.description or memo),
                    cls,
                ]
            )
        lines += _iif_line(["ENDTRNS"])
    return lines


def export_bill_payments(
    db: Session, date_from: date = None, date_to: date = None
) -> str:
    """Bill payments as BILLPMT blocks: TRNS = the bank account paid from
    (negative), SPL = Accounts Payable with the vendor (positive), at the
    amount the ledger booked."""
    from app.models.bills import BillPayment
    from app.services.control_accounts import find

    ap_name = _resolve_account_name(db, find(db, "2000")) or "Accounts Payable"
    q = _dated(
        db.query(BillPayment)
        .options(joinedload(BillPayment.vendor))
        .filter(BillPayment.is_voided.isnot(True)),
        BillPayment,
        date_from,
        date_to,
    )
    lines = _txn_header(_TXN_COLUMNS_SHORT)
    for bp in q.order_by(BillPayment.date, BillPayment.id).all():
        vendor = bp.vendor.name if bp.vendor else ""
        txn = bp.transaction
        # what the ledger booked (home currency), else the payment's amount
        if txn is not None and txn.lines:
            paid = sum((Decimal(str(ln.credit or 0)) for ln in txn.lines), Decimal("0"))
        else:
            paid = Decimal(str(bp.amount or 0))
        if paid == 0:
            continue
        bank = (
            _resolve_account_name(db, bp.pay_from_account_id)
            if bp.pay_from_account_id
            else ""
        )
        ref = _iif_clean(bp.check_number or "")
        memo = _iif_text(bp.notes or "")
        lines += _iif_line(
            [
                "TRNS",
                "BILLPMT",
                _iif_date(bp.date),
                bank or "Checking",
                _iif_text(vendor),
                str(-paid),
                ref,
                memo,
                "",
            ]
        )
        lines += _iif_line(
            [
                "SPL",
                "BILLPMT",
                _iif_date(bp.date),
                ap_name,
                _iif_text(vendor),
                str(paid),
                ref,
                memo,
                "",
            ]
        )
        lines += _iif_line(["ENDTRNS"])
    return lines


def journal_source_types() -> tuple:
    """The source types the Journal Entries page lists: what goes out as
    GENERAL JOURNAL."""
    from app.routes.migration import SOURCES
    from app.services.qb_ledger_import import SOURCE_TYPE as QB_LEDGER

    return ("manual", "qbo_journal", "iif_import", QB_LEDGER) + tuple(
        source_type for _, _, source_type in SOURCES.values()
    )


def export_journal_entries(
    db: Session, date_from: date = None, date_to: date = None
) -> str:
    """Journal entries as GENERAL JOURNAL blocks: the first line is the
    TRNS row, the rest SPL rows, each with its sign (a debit positive, a
    credit negative), the entry's reference as DOCNUM, its description and
    each line's memo. A voided entry and its reversal both stay home."""
    from app.models.transactions import Transaction
    from app.services.qbo_documents import REVERSALS

    voided = {
        source_id
        for (source_id,) in db.query(Transaction.source_id).filter(
            Transaction.source_type.in_(REVERSALS), Transaction.source_id.isnot(None)
        )
    }
    q = _dated(
        db.query(Transaction).filter(
            Transaction.source_type.in_(journal_source_types())
        ),
        Transaction,
        date_from,
        date_to,
    )
    lines = _txn_header(_TXN_COLUMNS_SHORT)
    for txn in q.order_by(Transaction.date, Transaction.id).all():
        if txn.id in voided:
            continue
        posted = sorted(txn.lines, key=lambda ln: ln.id)
        if len(posted) < 2:
            continue
        cls = _class_name(db, txn.class_id)
        ref = _iif_clean(txn.reference or "")
        memo = _memo_text(txn.description)
        for i, ln in enumerate(posted):
            lines += _iif_line(
                [
                    "TRNS" if i == 0 else "SPL",
                    "GENERAL JOURNAL",
                    _iif_date(txn.date),
                    _resolve_account_name(db, ln.account_id),
                    "",
                    str(_signed(ln)),
                    ref,
                    _iif_text(ln.description or memo),
                    _class_name(db, getattr(ln, "class_id", None)) or cls,
                ]
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
        # the rest of the ledger (#344)
        export_register_entries(db),
        export_transfers(db),
        export_bill_payments(db),
        export_journal_entries(db),
    ]
    return "\r\n".join(s for s in sections if s.strip())
