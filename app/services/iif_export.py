# ============================================================================
# IIF Export Service — Intuit Interchange Format (Tab-Delimited)
# Generates .iif files compatible with QuickBooks Pro 2003 (Build 12.0.3190)
#
# IIF format spec reverse-engineered from:
#   1. Intuit SDK documentation (QBFC 5.0, qbXML 4.0 IIF appendix)
#   2. QB2003 Pro Revision R7 disc (mounted /mnt from sr1)
#   3. File > Utilities > Export menu in QB2003 (observed output format)
#
# Format rules:
#   - Tab-delimited fields, \r\n line endings (Windows)
#   - Header rows start with ! (define column order)
#   - Transaction blocks: TRNS line, one or more SPL lines, ENDTRNS
#   - Sign convention: TRNS amount = primary (debit), SPL = splits (credits)
#   - Dates: MM/DD/YYYY
#   - No CSV-style quoting — tabs in values would break the format
# ============================================================================

from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session, joinedload

from app.models.accounts import Account
from app.models.contacts import Customer, Vendor
from app.models.items import Item
from app.models.invoices import Invoice, InvoiceLine, InvoiceStatus
from app.models.payments import Payment, PaymentAllocation
from app.models.estimates import Estimate, EstimateLine, EstimateStatus


def _iif_date(d: date) -> str:
    """Format date as MM/DD/YYYY for QB2003."""
    return d.strftime("%m/%d/%Y") if d else ""


def _tab_join(fields: list) -> str:
    """Join fields with tabs, converting None to empty string."""
    return "\t".join(str(f) if f is not None else "" for f in fields)


def _iif_line(fields: list) -> str:
    """Build a single IIF line: tab-joined fields + \\r\\n."""
    return _tab_join(fields) + "\r\n"


def _map_account_type(acct: Account) -> str:
    """Map Slowbooks AccountType + account_number to IIF ACCNTTYPE.

    QB2003 has finer-grained account types than our 6 enums.
    We use account_number ranges to distinguish sub-types within
    each Slowbooks category.
    """
    num = int(acct.account_number or "0")
    atype = acct.account_type.value

    if atype == "asset":
        if 1000 <= num <= 1099:
            return "BANK"
        if num == 1100:
            return "AR"
        if num < 1500:
            return "OCASSET"
        if num < 2000:
            return "FIXASSET"
        return "OASSET"

    if atype == "liability":
        if num == 2000:
            return "AP"
        if num < 2500:
            return "OCLIAB"
        return "LTLIAB"

    return {
        "equity": "EQUITY",
        "income": "INC",
        "expense": "EXP",
        "cogs": "COGS",
    }[atype]


def _map_item_type(item: Item) -> str:
    """Map Slowbooks ItemType to IIF INVITEMTYPE."""
    return {
        "service": "SERV",
        "product": "PART",
        "material": "PART",
        "labor": "OTHC",
    }[item.item_type.value]


def _resolve_account_name(db: Session, account_id: int) -> str:
    """Get account name with parent:child notation for QB2003."""
    if not account_id:
        return ""
    acct = db.query(Account).filter(Account.id == account_id).first()
    if not acct:
        return ""
    return _full_account_name(db, acct)


def _full_account_name(db: Session, acct: Account) -> str:
    """Build colon-separated parent:child account name for QB convention."""
    if acct.parent_id:
        parent = db.query(Account).filter(Account.id == acct.parent_id).first()
        if parent:
            return f"{_full_account_name(db, parent)}:{acct.name}"
    return acct.name


# ============================================================================
# Export Functions — each returns IIF-formatted string content
# ============================================================================

def export_accounts(db: Session) -> str:
    """Export Chart of Accounts as !ACCNT section."""
    header = _iif_line(["!ACCNT", "NAME", "ACCNTTYPE", "DESC", "ACCNUM", "EXTRA"])

    accounts = db.query(Account).filter(Account.is_active == True).order_by(
        Account.account_number
    ).all()

    lines = header
    for acct in accounts:
        name = _full_account_name(db, acct)
        lines += _iif_line([
            "ACCNT",
            name,
            _map_account_type(acct),
            acct.description or "",
            acct.account_number or "",
            "",  # EXTRA field (unused, but QB expects the column)
        ])

    return lines


def export_customers(db: Session) -> str:
    """Export customers as !CUST section."""
    header = _iif_line([
        "!CUST", "NAME", "COMPANYNAME", "FIRSTNAME", "LASTNAME",
        "ADDR1", "ADDR2", "ADDR3", "ADDR4", "ADDR5",
        "PHONE1", "PHONE2", "EMAIL", "TERMS", "TAXID", "LIMIT",
    ])

    customers = db.query(Customer).filter(Customer.is_active == True).order_by(
        Customer.name
    ).all()

    lines = header
    for c in customers:
        # Split name into first/last (best effort)
        parts = (c.name or "").split(" ", 1)
        first = parts[0] if parts else ""
        last = parts[1] if len(parts) > 1 else ""

        # QB address convention: ADDR1=company/name, ADDR2-3=street, ADDR4=city/region/postcode
        addr1 = c.company or c.name or ""
        addr2 = c.bill_address1 or ""
        addr3 = c.bill_address2 or ""
        city_region_postcode = ", ".join(filter(None, [
            c.bill_city,
            f"{c.bill_state} {c.bill_zip}".strip() if (c.bill_state or c.bill_zip) else None,
        ]))

        lines += _iif_line([
            "CUST",
            c.name or "",
            c.company or "",
            first,
            last,
            addr1,
            addr2,
            addr3,
            city_region_postcode,
            "",  # ADDR5
            c.phone or "",
            c.mobile or "",
            c.email or "",
            c.terms or "",
            c.tax_id or "",
            str(c.credit_limit) if c.credit_limit else "",
        ])

    return lines


def export_vendors(db: Session) -> str:
    """Export vendors as !VEND section."""
    header = _iif_line([
        "!VEND", "NAME", "ADDR1", "ADDR2", "ADDR3", "ADDR4", "ADDR5",
        "PHONE1", "PHONE2", "EMAIL", "TERMS", "TAXID",
    ])

    vendors = db.query(Vendor).filter(Vendor.is_active == True).order_by(
        Vendor.name
    ).all()

    lines = header
    for v in vendors:
        addr1 = v.company or v.name or ""
        addr2 = v.address1 or ""
        addr3 = v.address2 or ""
        city_region_postcode = ", ".join(filter(None, [
            v.city,
            f"{v.state} {v.zip}".strip() if (v.state or v.zip) else None,
        ]))

        lines += _iif_line([
            "VEND",
            v.name or "",
            addr1,
            addr2,
            addr3,
            city_region_postcode,
            "",  # ADDR5
            v.phone or "",
            v.fax or "",
            v.email or "",
            v.terms or "",
            v.tax_id or "",
        ])

    return lines


def export_items(db: Session) -> str:
    """Export items as !INVITEM section."""
    header = _iif_line([
        "!INVITEM", "NAME", "INVITEMTYPE", "DESC", "ACCNT", "PRICE", "TAXABLE",
    ])

    items = db.query(Item).filter(Item.is_active == True).order_by(Item.name).all()

    lines = header
    for item in items:
        acct_name = _resolve_account_name(db, item.income_account_id)
        lines += _iif_line([
            "INVITEM",
            item.name or "",
            _map_item_type(item),
            item.description or "",
            acct_name,
            str(item.rate) if item.rate else "0",
            "Y" if item.is_taxable else "N",
        ])

    return lines


def export_invoices(db: Session, date_from: date = None, date_to: date = None) -> str:
    """Export invoices as !TRNS/!SPL/ENDTRNS transaction blocks.

    Sign convention per QB IIF spec:
      TRNS line: positive amount (debit to Accounts Receivable)
      SPL lines: negative amount (credit to income accounts)
      Sum of TRNS + all SPL = 0 (balanced transaction)
    """
    # Transaction header — defines columns for both TRNS and SPL lines
    header = (
        _iif_line(["!TRNS", "TRNSTYPE", "DATE", "ACCNT", "NAME", "AMOUNT",
                    "DOCNUM", "DUEDATE", "TERMS", "MEMO"]) +
        _iif_line(["!SPL", "TRNSTYPE", "DATE", "ACCNT", "NAME", "AMOUNT",
                    "DOCNUM", "DUEDATE", "TERMS", "MEMO"]) +
        _iif_line(["!ENDTRNS"])
    )

    query = db.query(Invoice).options(
        joinedload(Invoice.customer),
        joinedload(Invoice.lines).joinedload(InvoiceLine.item),
    ).filter(Invoice.status != InvoiceStatus.VOID)

    if date_from:
        query = query.filter(Invoice.date >= date_from)
    if date_to:
        query = query.filter(Invoice.date <= date_to)

    invoices = query.order_by(Invoice.date, Invoice.id).all()

    lines = header
    for inv in invoices:
        cust_name = inv.customer.name if inv.customer else ""
        inv_date = _iif_date(inv.date)
        due_date = _iif_date(inv.due_date)
        total = Decimal(str(inv.total or 0))

        # TRNS line — debit A/R for full invoice amount
        lines += _iif_line([
            "TRNS", "INVOICE", inv_date, "Accounts Receivable", cust_name,
            str(total), inv.invoice_number or "", due_date, inv.terms or "", "",
        ])

        # SPL lines — credit income accounts for each line item
        for il in inv.lines:
            amt = Decimal(str(il.amount or 0))
            if amt == 0:
                continue
            item_name = il.item.name if il.item else ""
            acct_name = ""
            if il.item and il.item.income_account_id:
                acct_name = _resolve_account_name(db, il.item.income_account_id)
            if not acct_name:
                acct_name = "Service Income"  # fallback

            lines += _iif_line([
                "SPL", "INVOICE", inv_date, acct_name, cust_name,
                str(-amt), inv.invoice_number or "", "", "",
                il.description or "",
            ])

        # SPL line for tax if applicable
        tax_amt = Decimal(str(inv.tax_amount or 0))
        if tax_amt > 0:
            lines += _iif_line([
                "SPL", "INVOICE", inv_date, "Sales Tax Payable", cust_name,
                str(-tax_amt), inv.invoice_number or "", "", "", "Sales Tax",
            ])

        lines += _iif_line(["ENDTRNS"])

    return lines


def export_payments(db: Session, date_from: date = None, date_to: date = None) -> str:
    """Export payments as !TRNS/!SPL/ENDTRNS transaction blocks.

    Sign convention:
      TRNS line: positive amount (debit to deposit account / bank)
      SPL line:  negative amount (credit to Accounts Receivable)
    """
    header = (
        _iif_line(["!TRNS", "TRNSTYPE", "DATE", "ACCNT", "NAME", "AMOUNT",
                    "DOCNUM", "MEMO"]) +
        _iif_line(["!SPL", "TRNSTYPE", "DATE", "ACCNT", "NAME", "AMOUNT",
                    "DOCNUM", "MEMO"]) +
        _iif_line(["!ENDTRNS"])
    )

    query = db.query(Payment).options(
        joinedload(Payment.customer),
        joinedload(Payment.deposit_to_account),
        joinedload(Payment.allocations).joinedload(PaymentAllocation.invoice),
    )

    if date_from:
        query = query.filter(Payment.date >= date_from)
    if date_to:
        query = query.filter(Payment.date <= date_to)

    payments = query.order_by(Payment.date, Payment.id).all()

    lines = header
    for pmt in payments:
        cust_name = pmt.customer.name if pmt.customer else ""
        pmt_date = _iif_date(pmt.date)
        amount = Decimal(str(pmt.amount or 0))

        # Deposit account name
        deposit_acct = "Undeposited Funds"
        if pmt.deposit_to_account:
            deposit_acct = _full_account_name(db, pmt.deposit_to_account)

        ref = pmt.reference or pmt.check_number or ""

        # TRNS line — debit bank/deposit account
        lines += _iif_line([
            "TRNS", "PAYMENT", pmt_date, deposit_acct, cust_name,
            str(amount), ref, pmt.notes or "",
        ])

        # SPL lines — credit A/R (one per allocation, or single if no allocations)
        if pmt.allocations:
            for alloc in pmt.allocations:
                alloc_amt = Decimal(str(alloc.amount or 0))
                doc_num = ""
                if alloc.invoice:
                    doc_num = alloc.invoice.invoice_number or ""
                lines += _iif_line([
                    "SPL", "PAYMENT", pmt_date, "Accounts Receivable", cust_name,
                    str(-alloc_amt), doc_num, "",
                ])
        else:
            lines += _iif_line([
                "SPL", "PAYMENT", pmt_date, "Accounts Receivable", cust_name,
                str(-amount), "", "",
            ])

        lines += _iif_line(["ENDTRNS"])

    return lines


def export_estimates(db: Session) -> str:
    """Export estimates as !TRNS/!SPL/ENDTRNS with TRNSTYPE=ESTIMATE.

    Estimates don't post to A/R in QB2003 — they're non-posting transactions.
    But the IIF format is identical to invoices with ESTIMATE type.
    """
    header = (
        _iif_line(["!TRNS", "TRNSTYPE", "DATE", "ACCNT", "NAME", "AMOUNT",
                    "DOCNUM", "MEMO"]) +
        _iif_line(["!SPL", "TRNSTYPE", "DATE", "ACCNT", "NAME", "AMOUNT",
                    "DOCNUM", "MEMO"]) +
        _iif_line(["!ENDTRNS"])
    )

    estimates = db.query(Estimate).options(
        joinedload(Estimate.customer),
        joinedload(Estimate.lines).joinedload(EstimateLine.item),
    ).order_by(Estimate.date, Estimate.id).all()

    lines = header
    for est in estimates:
        cust_name = est.customer.name if est.customer else ""
        est_date = _iif_date(est.date)
        total = Decimal(str(est.total or 0))

        lines += _iif_line([
            "TRNS", "ESTIMATE", est_date, "Accounts Receivable", cust_name,
            str(total), est.estimate_number or "", est.notes or "",
        ])

        for el in est.lines:
            amt = Decimal(str(el.amount or 0))
            if amt == 0:
                continue
            acct_name = ""
            if el.item and el.item.income_account_id:
                acct_name = _resolve_account_name(db, el.item.income_account_id)
            if not acct_name:
                acct_name = "Service Income"

            lines += _iif_line([
                "SPL", "ESTIMATE", est_date, acct_name, cust_name,
                str(-amt), est.estimate_number or "",
                el.description or "",
            ])

        tax_amt = Decimal(str(est.tax_amount or 0))
        if tax_amt > 0:
            lines += _iif_line([
                "SPL", "ESTIMATE", est_date, "Sales Tax Payable", cust_name,
                str(-tax_amt), est.estimate_number or "", "Sales Tax",
            ])

        lines += _iif_line(["ENDTRNS"])

    return lines


def export_all(db: Session) -> str:
    """Export all Slowbooks data as a single IIF file.

    Order matters — QB2003 imports lists before transactions,
    and accounts must exist before items can reference them.
    """
    sections = [
        export_accounts(db),
        export_customers(db),
        export_vendors(db),
        export_items(db),
        export_estimates(db),
        export_invoices(db),
        export_payments(db),
    ]
    return "\r\n".join(s for s in sections if s.strip())
