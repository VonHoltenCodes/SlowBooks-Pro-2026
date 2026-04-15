# ============================================================================
# IIF Import Service — Parse Intuit Interchange Format files into Slowbooks
#
# Handles IIF files exported from QuickBooks 2003 Pro (and compatible versions).
# The parser is forgiving: it skips unknown sections, tolerates missing fields,
# and collects per-row errors instead of aborting the entire import.
#
# Import order mirrors dependency chain:
#   accounts -> customers -> vendors -> items -> transactions
#
# Duplicate detection: matches on name (accounts, customers, vendors, items)
# or document number (invoices, payments) to prevent re-import collisions.
# ============================================================================

from datetime import datetime, date
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from app.models.accounts import Account, AccountType
from app.models.contacts import Customer, Vendor
from app.models.items import Item, ItemType
from app.models.invoices import Invoice, InvoiceLine, InvoiceStatus
from app.models.payments import Payment, PaymentAllocation
from app.models.estimates import Estimate, EstimateLine, EstimateStatus
from app.services.accounting import create_journal_entry, get_ar_account_id, get_sales_tax_account_id


# ============================================================================
# IIF Parser
# ============================================================================

def parse_iif(content: str) -> dict:
    """Parse IIF file content into structured sections.

    Returns dict with keys like "ACCNT", "CUST", "VEND", "INVITEM",
    and "TRNS" (list of transaction blocks).

    Each list/row section contains dicts keyed by header field names.
    Transaction blocks group TRNS + SPL lines until ENDTRNS.
    """
    result = {
        "ACCNT": [],
        "CUST": [],
        "VEND": [],
        "INVITEM": [],
        "TRNS": [],  # list of transaction blocks: [{"trns": dict, "spl": [dict]}]
    }

    # Normalize line endings
    content = content.replace("\r\n", "\n").replace("\r", "\n")
    lines = content.split("\n")

    headers = {}  # section_type -> [field_names]
    current_txn = None  # accumulator for TRNS/SPL/ENDTRNS blocks

    for line in lines:
        line = line.rstrip()
        if not line:
            continue

        fields = line.split("\t")
        row_type = fields[0].strip()

        # Header row — defines columns for subsequent data rows
        if row_type.startswith("!"):
            section = row_type[1:]  # strip the !
            headers[section] = [f.strip() for f in fields]
            continue

        # ENDTRNS — close current transaction block
        if row_type == "ENDTRNS":
            if current_txn:
                result["TRNS"].append(current_txn)
                current_txn = None
            continue

        # TRNS line — start new transaction block
        if row_type == "TRNS":
            if current_txn:
                # Previous block wasn't closed — save it anyway
                result["TRNS"].append(current_txn)
            hdr = headers.get("TRNS", [])
            row_dict = _fields_to_dict(hdr, fields)
            current_txn = {"trns": row_dict, "spl": []}
            continue

        # SPL line — add split to current transaction
        if row_type == "SPL":
            hdr = headers.get("SPL", [])
            row_dict = _fields_to_dict(hdr, fields)
            if current_txn:
                current_txn["spl"].append(row_dict)
            continue

        # List rows: ACCNT, CUST, VEND, INVITEM
        if row_type in result and row_type != "TRNS":
            hdr = headers.get(row_type, [])
            row_dict = _fields_to_dict(hdr, fields)
            result[row_type].append(row_dict)

    # Handle unclosed transaction block
    if current_txn:
        result["TRNS"].append(current_txn)

    return result


def _fields_to_dict(header: list, fields: list) -> dict:
    """Map positional fields to named dict using header row."""
    d = {}
    for i, name in enumerate(header):
        if name.startswith("!"):
            name = name[1:]  # strip ! from first field if present
        if i < len(fields):
            d[name] = fields[i].strip()
        else:
            d[name] = ""
    return d


def _parse_iif_date(s: str) -> date:
    """Parse MM/DD/YYYY date string from IIF."""
    if not s:
        return None
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _parse_decimal(s: str) -> Decimal:
    """Parse a decimal string, returning 0 on failure."""
    if not s:
        return Decimal("0")
    try:
        return Decimal(s.strip().replace(",", ""))
    except InvalidOperation:
        return Decimal("0")


# Well-known IIF account names mapped to standard Slowbooks account numbers.
# Used as fallback when name-based lookup fails during import.
_WELL_KNOWN_ACCOUNTS = {
    "accounts receivable": "1100",
    "a/r": "1100",
    "trade receivables": "1100",
    "undeposited funds": "1200",
    "accounts payable": "2000",
    "a/p": "2000",
    "trade payables": "2000",
    "sales tax payable": "2200",
    "checking": "1000",
    "savings": "1010",
    "service income": "4000",
    "product sales": "4100",
    "material income": "4200",
    "labor income": "4300",
    "other income": "4900",
    "cost of goods sold": "5000",
    "cogs": "5000",
}


def _find_account(db: Session, name: str) -> Account:
    """Find an account by name with fallback strategies.

    1. Exact name match
    2. Case-insensitive name match
    3. Well-known account name → account number mapping
    """
    if not name:
        return None

    # 1. Exact match
    acct = db.query(Account).filter(Account.name == name).first()
    if acct:
        return acct

    # 2. Case-insensitive match
    acct = db.query(Account).filter(
        Account.name.ilike(name)
    ).first()
    if acct:
        return acct

    # 3. Well-known name → account number fallback
    acct_num = _WELL_KNOWN_ACCOUNTS.get(name.lower().strip())
    if acct_num:
        acct = db.query(Account).filter(Account.account_number == acct_num).first()
        if acct:
            return acct

    return None


# ============================================================================
# Reverse type mappings (IIF -> Slowbooks)
# ============================================================================

_IIF_TO_ACCOUNT_TYPE = {
    "BANK": AccountType.ASSET,
    "AR": AccountType.ASSET,
    "OCASSET": AccountType.ASSET,
    "OASSET": AccountType.ASSET,
    "FIXASSET": AccountType.ASSET,
    "AP": AccountType.LIABILITY,
    "OCLIAB": AccountType.LIABILITY,
    "LTLIAB": AccountType.LIABILITY,
    "EQUITY": AccountType.EQUITY,
    "INC": AccountType.INCOME,
    "EXP": AccountType.EXPENSE,
    "COGS": AccountType.COGS,
    # Additional QB types mapped to closest Slowbooks equivalent
    "EXINC": AccountType.INCOME,
    "EXEXP": AccountType.EXPENSE,
    "NONPOSTING": AccountType.ASSET,
}

_IIF_TO_ITEM_TYPE = {
    "SERV": ItemType.SERVICE,
    "PART": ItemType.PRODUCT,
    "OTHC": ItemType.LABOR,
    "INVENTORY": ItemType.PRODUCT,
    "NON-INVENTORY": ItemType.MATERIAL,
}


# ============================================================================
# Import Functions
# ============================================================================

def import_accounts(db: Session, rows: list) -> dict:
    """Import account rows from IIF into Slowbooks.

    Handles colon-separated parent:child names by creating parents first.
    Skips accounts that already exist (matched by name).
    """
    imported = 0
    errors = []

    # Sort so parents come before children (fewer colons first)
    rows.sort(key=lambda r: r.get("NAME", "").count(":"))

    for i, row in enumerate(rows):
        sp = db.begin_nested()  # savepoint — isolate per-row failures
        try:
            full_name = row.get("NAME", "").strip()
            if not full_name:
                errors.append({"row": i + 1, "message": "Missing account NAME"})
                sp.rollback()
                continue

            iif_type = row.get("ACCNTTYPE", "").strip().upper()
            acct_type = _IIF_TO_ACCOUNT_TYPE.get(iif_type, AccountType.EXPENSE)
            acct_num = row.get("ACCNUM", "").strip() or None

            # Handle parent:child names
            parent_id = None
            if ":" in full_name:
                parts = full_name.split(":")
                name = parts[-1].strip()
                parent_name = parts[-2].strip()
                parent = db.query(Account).filter(Account.name == parent_name).first()
                if parent:
                    parent_id = parent.id
            else:
                name = full_name

            # Check duplicates by name OR account_number (account_number has a
            # unique constraint, so a name-only check misses cases where the
            # seeded chart-of-accounts uses a slightly different label,
            # e.g. "Opening Balance Equity" vs IIF's "Opening Bal Equity").
            dup_q = db.query(Account).filter(Account.name == name)
            if acct_num:
                dup_q = db.query(Account).filter(
                    (Account.name == name) | (Account.account_number == acct_num)
                )
            if dup_q.first():
                sp.rollback()
                continue  # skip duplicate

            acct = Account(
                name=name,
                account_type=acct_type,
                account_number=acct_num,
                description=row.get("DESC", "").strip() or None,
                parent_id=parent_id,
                is_active=True,
            )
            db.add(acct)
            db.flush()
            sp.commit()
            imported += 1

        except Exception as e:
            sp.rollback()
            errors.append({"row": i + 1, "message": str(e)})

    return {"imported": imported, "errors": errors}


def import_customers(db: Session, rows: list) -> dict:
    """Import customer rows from IIF."""
    imported = 0
    errors = []

    for i, row in enumerate(rows):
        sp = db.begin_nested()
        try:
            name = row.get("NAME", "").strip()
            if not name:
                errors.append({"row": i + 1, "message": "Missing customer NAME"})
                sp.rollback()
                continue

            existing = db.query(Customer).filter(Customer.name == name).first()
            if existing:
                sp.rollback()
                continue

            # Parse ADDR4 "City, State ZIP" pattern
            addr4 = row.get("ADDR4", "").strip()
            city, state, zipcode = "", "", ""
            if addr4:
                city, state, zipcode = _parse_city_state_zip(addr4)

            cust = Customer(
                name=name,
                company=row.get("COMPANYNAME", "").strip() or None,
                email=row.get("EMAIL", "").strip() or None,
                phone=row.get("PHONE1", "").strip() or None,
                mobile=row.get("PHONE2", "").strip() or None,
                bill_address1=row.get("ADDR2", "").strip() or None,
                bill_address2=row.get("ADDR3", "").strip() or None,
                bill_city=city or None,
                bill_state=state or None,
                bill_zip=zipcode or None,
                terms=row.get("TERMS", "").strip() or "Net 30",
                tax_id=row.get("TAXID", "").strip() or None,
                credit_limit=_parse_decimal(row.get("LIMIT", "")) or None,
                is_active=True,
            )
            db.add(cust)
            db.flush()
            sp.commit()
            imported += 1

        except Exception as e:
            sp.rollback()
            errors.append({"row": i + 1, "message": str(e)})

    return {"imported": imported, "errors": errors}


def import_vendors(db: Session, rows: list) -> dict:
    """Import vendor rows from IIF."""
    imported = 0
    errors = []

    for i, row in enumerate(rows):
        sp = db.begin_nested()
        try:
            name = row.get("NAME", "").strip()
            if not name:
                errors.append({"row": i + 1, "message": "Missing vendor NAME"})
                sp.rollback()
                continue

            existing = db.query(Vendor).filter(Vendor.name == name).first()
            if existing:
                sp.rollback()
                continue

            addr4 = row.get("ADDR4", "").strip()
            city, state, zipcode = "", "", ""
            if addr4:
                city, state, zipcode = _parse_city_state_zip(addr4)

            vend = Vendor(
                name=name,
                company=row.get("ADDR1", "").strip() or None,
                email=row.get("EMAIL", "").strip() or None,
                phone=row.get("PHONE1", "").strip() or None,
                fax=row.get("PHONE2", "").strip() or None,
                address1=row.get("ADDR2", "").strip() or None,
                address2=row.get("ADDR3", "").strip() or None,
                city=city or None,
                state=state or None,
                zip=zipcode or None,
                terms=row.get("TERMS", "").strip() or "Net 30",
                tax_id=row.get("TAXID", "").strip() or None,
                is_active=True,
            )
            db.add(vend)
            db.flush()
            sp.commit()
            imported += 1

        except Exception as e:
            sp.rollback()
            errors.append({"row": i + 1, "message": str(e)})

    return {"imported": imported, "errors": errors}


def import_items(db: Session, rows: list) -> dict:
    """Import item rows from IIF."""
    imported = 0
    errors = []

    for i, row in enumerate(rows):
        sp = db.begin_nested()
        try:
            name = row.get("NAME", "").strip()
            if not name:
                errors.append({"row": i + 1, "message": "Missing item NAME"})
                sp.rollback()
                continue

            existing = db.query(Item).filter(Item.name == name).first()
            if existing:
                sp.rollback()
                continue

            iif_type = row.get("INVITEMTYPE", "").strip().upper()
            item_type = _IIF_TO_ITEM_TYPE.get(iif_type, ItemType.SERVICE)

            # Resolve income account by name (with fallback)
            income_account_id = None
            acct_name = row.get("ACCNT", "").strip()
            if acct_name:
                acct = _find_account(db, acct_name)
                if acct:
                    income_account_id = acct.id

            taxable = row.get("TAXABLE", "").strip().upper()

            item = Item(
                name=name,
                item_type=item_type,
                description=row.get("DESC", "").strip() or None,
                rate=_parse_decimal(row.get("PRICE", "")),
                income_account_id=income_account_id,
                is_taxable=(taxable == "Y"),
                is_active=True,
            )
            db.add(item)
            db.flush()
            sp.commit()
            imported += 1

        except Exception as e:
            sp.rollback()
            errors.append({"row": i + 1, "message": str(e)})

    return {"imported": imported, "errors": errors}


def import_transactions(db: Session, blocks: list) -> dict:
    """Import transaction blocks (TRNS/SPL/ENDTRNS) from IIF.

    Routes by TRNSTYPE: INVOICE, PAYMENT, ESTIMATE, GENERAL JOURNAL.
    """
    counts = {"invoices": 0, "payments": 0, "estimates": 0}
    errors = []
    warnings = []

    for i, block in enumerate(blocks):
        sp = db.begin_nested()
        try:
            trns = block.get("trns", {})
            spls = block.get("spl", [])
            trns_type = trns.get("TRNSTYPE", "").strip().upper()

            if trns_type == "INVOICE":
                result = _import_invoice(db, trns, spls)
                if result:
                    counts["invoices"] += 1
                    if not result.transaction_id:
                        doc = result.invoice_number or f"block {i+1}"
                        warnings.append(f"Invoice {doc}: imported but journal entry could not be created (account mismatch)")
            elif trns_type == "PAYMENT":
                result = _import_payment(db, trns, spls)
                if result:
                    counts["payments"] += 1
                    if not result.transaction_id:
                        warnings.append(f"Payment block {i+1}: imported but journal entry could not be created (account mismatch)")
            elif trns_type == "ESTIMATE":
                result = _import_estimate(db, trns, spls)
                if result:
                    counts["estimates"] += 1
            # Skip other transaction types (GENERAL JOURNAL, etc.) for now
            sp.commit()

        except Exception as e:
            sp.rollback()
            errors.append({"row": i + 1, "message": f"Transaction block {i + 1}: {str(e)}"})

    return {"imported": counts, "errors": errors, "warnings": warnings}


def _import_invoice(db: Session, trns: dict, spls: list) -> Invoice:
    """Create an Invoice from IIF TRNS/SPL data."""
    doc_num = trns.get("DOCNUM", "").strip()

    # Skip if invoice number already exists
    if doc_num:
        existing = db.query(Invoice).filter(Invoice.invoice_number == doc_num).first()
        if existing:
            return None

    # Resolve customer
    cust_name = trns.get("NAME", "").strip()
    customer = db.query(Customer).filter(Customer.name == cust_name).first()
    if not customer:
        # Auto-create customer
        customer = Customer(name=cust_name, is_active=True)
        db.add(customer)
        db.flush()

    inv_date = _parse_iif_date(trns.get("DATE", ""))
    due_date = _parse_iif_date(trns.get("DUEDATE", ""))
    total = abs(_parse_decimal(trns.get("AMOUNT", "")))

    invoice = Invoice(
        invoice_number=doc_num or None,
        customer_id=customer.id,
        date=inv_date or date.today(),
        due_date=due_date or inv_date or date.today(),
        terms=trns.get("TERMS", "").strip() or "Net 30",
        status=InvoiceStatus.SENT,
        subtotal=Decimal("0"),
        tax_rate=Decimal("0"),
        tax_amount=Decimal("0"),
        total=total,
        balance_due=total,
    )
    db.add(invoice)
    db.flush()

    # Process SPL lines as invoice line items
    subtotal = Decimal("0")
    tax_total = Decimal("0")
    line_order = 0

    for spl in spls:
        acct_name = spl.get("ACCNT", "").strip()
        spl_amount = abs(_parse_decimal(spl.get("AMOUNT", "")))
        memo = spl.get("MEMO", "").strip()

        # Tax split
        if "tax" in acct_name.lower():
            tax_total += spl_amount
            continue

        # Resolve item by name if available
        item_id = None
        item_name = spl.get("INVITEM", "").strip()
        if item_name:
            item = db.query(Item).filter(Item.name == item_name).first()
            if item:
                item_id = item.id

        qty = abs(_parse_decimal(spl.get("QNTY", ""))) or Decimal("1")
        rate = abs(_parse_decimal(spl.get("PRICE", "")))
        if rate == 0 and qty > 0:
            rate = spl_amount / qty

        line = InvoiceLine(
            invoice_id=invoice.id,
            item_id=item_id,
            description=memo or None,
            quantity=qty,
            rate=rate,
            amount=spl_amount,
            line_order=line_order,
        )
        db.add(line)
        subtotal += spl_amount
        line_order += 1

    invoice.subtotal = subtotal
    invoice.tax_amount = tax_total
    invoice.total = subtotal + tax_total
    invoice.balance_due = invoice.total

    # Create journal entry
    ar_id = get_ar_account_id(db)
    unmatched_accounts = []
    if ar_id and subtotal > 0:
        journal_lines = [
            {"account_id": ar_id, "debit": invoice.total, "credit": Decimal("0"),
             "description": f"Invoice {doc_num}"},
        ]

        # Find income account for credit side
        for spl in spls:
            acct_name = spl.get("ACCNT", "").strip()
            spl_amount = abs(_parse_decimal(spl.get("AMOUNT", "")))
            if spl_amount == 0:
                continue
            acct = _find_account(db, acct_name)
            if acct:
                journal_lines.append({
                    "account_id": acct.id,
                    "debit": Decimal("0"),
                    "credit": spl_amount,
                    "description": f"Invoice {doc_num}",
                })
            else:
                unmatched_accounts.append(acct_name)

        # Only post if balanced
        total_dr = sum(Decimal(str(l["debit"])) for l in journal_lines)
        total_cr = sum(Decimal(str(l["credit"])) for l in journal_lines)
        if total_dr == total_cr and total_dr > 0:
            txn = create_journal_entry(
                db, invoice.date,
                f"IIF Import — Invoice {doc_num}",
                journal_lines,
                source_type="invoice",
                source_id=invoice.id,
            )
            invoice.transaction_id = txn.id
        elif total_dr != total_cr:
            # Journal unbalanced because account(s) not found — use default income
            default_income_id = get_default_income_account_id(db)
            if default_income_id:
                shortfall = total_dr - total_cr
                journal_lines.append({
                    "account_id": default_income_id,
                    "debit": Decimal("0"),
                    "credit": shortfall,
                    "description": f"Invoice {doc_num} (unmatched: {', '.join(unmatched_accounts)})",
                })
                txn = create_journal_entry(
                    db, invoice.date,
                    f"IIF Import — Invoice {doc_num}",
                    journal_lines,
                    source_type="invoice",
                    source_id=invoice.id,
                )
                invoice.transaction_id = txn.id

    db.flush()
    return invoice


def _import_payment(db: Session, trns: dict, spls: list) -> Payment:
    """Create a Payment from IIF TRNS/SPL data."""
    ref = trns.get("DOCNUM", "").strip()

    # Duplicate detection: match on customer + date + amount + reference
    cust_name = trns.get("NAME", "").strip()
    pmt_date = _parse_iif_date(trns.get("DATE", ""))
    pmt_amount = abs(_parse_decimal(trns.get("AMOUNT", "")))
    existing_q = db.query(Payment).join(Customer).filter(
        Customer.name == cust_name,
        Payment.date == (pmt_date or date.today()),
        Payment.amount == pmt_amount,
    )
    if ref:
        existing_q = existing_q.filter(Payment.reference == ref)
    if existing_q.first():
        return None

    # Resolve customer
    customer = db.query(Customer).filter(Customer.name == cust_name).first()
    if not customer:
        return None  # Can't create payment without customer

    pmt_date = _parse_iif_date(trns.get("DATE", ""))
    amount = abs(_parse_decimal(trns.get("AMOUNT", "")))

    # Resolve deposit account (with fallback to well-known names and Undeposited Funds)
    deposit_acct_name = trns.get("ACCNT", "").strip()
    deposit_acct = _find_account(db, deposit_acct_name)
    if not deposit_acct:
        # Last resort: use Undeposited Funds
        from app.services.accounting import get_undeposited_funds_id
        uf_id = get_undeposited_funds_id(db)
        if uf_id:
            deposit_acct = db.query(Account).filter(Account.id == uf_id).first()

    payment = Payment(
        customer_id=customer.id,
        date=pmt_date or date.today(),
        amount=amount,
        reference=ref or None,
        deposit_to_account_id=deposit_acct.id if deposit_acct else None,
    )
    db.add(payment)
    db.flush()

    # Create allocations from SPL lines
    for spl in spls:
        doc_num = spl.get("DOCNUM", "").strip()
        if doc_num:
            invoice = db.query(Invoice).filter(Invoice.invoice_number == doc_num).first()
            if invoice:
                alloc_amount = abs(_parse_decimal(spl.get("AMOUNT", "")))
                alloc = PaymentAllocation(
                    payment_id=payment.id,
                    invoice_id=invoice.id,
                    amount=alloc_amount or amount,
                )
                db.add(alloc)

                # Update invoice status
                invoice.amount_paid = (invoice.amount_paid or Decimal("0")) + (alloc_amount or amount)
                invoice.balance_due = invoice.total - invoice.amount_paid
                if invoice.balance_due <= 0:
                    invoice.status = InvoiceStatus.PAID
                elif invoice.amount_paid > 0:
                    invoice.status = InvoiceStatus.PARTIAL

    # Create journal entry
    ar_id = get_ar_account_id(db)
    if ar_id and deposit_acct and amount > 0:
        journal_lines = [
            {"account_id": deposit_acct.id, "debit": amount, "credit": Decimal("0"),
             "description": f"Payment from {cust_name}"},
            {"account_id": ar_id, "debit": Decimal("0"), "credit": amount,
             "description": f"Payment from {cust_name}"},
        ]
        txn = create_journal_entry(
            db, payment.date,
            f"IIF Import — Payment from {cust_name}",
            journal_lines,
            source_type="payment",
            source_id=payment.id,
        )
        payment.transaction_id = txn.id

    db.flush()
    return payment


def _import_estimate(db: Session, trns: dict, spls: list) -> Estimate:
    """Create an Estimate from IIF TRNS/SPL data."""
    doc_num = trns.get("DOCNUM", "").strip()

    if doc_num:
        existing = db.query(Estimate).filter(Estimate.estimate_number == doc_num).first()
        if existing:
            return None

    cust_name = trns.get("NAME", "").strip()
    customer = db.query(Customer).filter(Customer.name == cust_name).first()
    if not customer:
        customer = Customer(name=cust_name, is_active=True)
        db.add(customer)
        db.flush()

    est_date = _parse_iif_date(trns.get("DATE", ""))
    total = abs(_parse_decimal(trns.get("AMOUNT", "")))

    estimate = Estimate(
        estimate_number=doc_num or None,
        customer_id=customer.id,
        date=est_date or date.today(),
        status=EstimateStatus.PENDING,
        subtotal=Decimal("0"),
        tax_rate=Decimal("0"),
        tax_amount=Decimal("0"),
        total=total,
    )
    db.add(estimate)
    db.flush()

    subtotal = Decimal("0")
    tax_total = Decimal("0")
    line_order = 0

    for spl in spls:
        acct_name = spl.get("ACCNT", "").strip()
        spl_amount = abs(_parse_decimal(spl.get("AMOUNT", "")))
        memo = spl.get("MEMO", "").strip()

        if "tax" in acct_name.lower():
            tax_total += spl_amount
            continue

        item_id = None
        item_name = spl.get("INVITEM", "").strip()
        if item_name:
            item = db.query(Item).filter(Item.name == item_name).first()
            if item:
                item_id = item.id

        qty = abs(_parse_decimal(spl.get("QNTY", ""))) or Decimal("1")
        rate = abs(_parse_decimal(spl.get("PRICE", "")))
        if rate == 0 and qty > 0:
            rate = spl_amount / qty

        line = EstimateLine(
            estimate_id=estimate.id,
            item_id=item_id,
            description=memo or None,
            quantity=qty,
            rate=rate,
            amount=spl_amount,
            line_order=line_order,
        )
        db.add(line)
        subtotal += spl_amount
        line_order += 1

    estimate.subtotal = subtotal
    estimate.tax_amount = tax_total
    estimate.total = subtotal + tax_total
    db.flush()
    return estimate


def _parse_city_state_zip(s: str) -> tuple:
    """Parse 'City, State ZIP' string into components."""
    city, state, zipcode = "", "", ""
    if not s:
        return city, state, zipcode

    # Try "City, State ZIP" pattern
    if "," in s:
        parts = s.split(",", 1)
        city = parts[0].strip()
        remainder = parts[1].strip()
        # State ZIP
        rparts = remainder.rsplit(" ", 1)
        if len(rparts) == 2:
            state = rparts[0].strip()
            zipcode = rparts[1].strip()
        else:
            state = remainder
    else:
        # No comma — try "State ZIP" or just city
        city = s.strip()

    return city, state, zipcode


# ============================================================================
# Validation (pre-flight check without importing)
# ============================================================================

def validate_iif(content: str) -> dict:
    """Validate an IIF file and return a report without modifying the database.

    Checks:
    - File structure (has valid headers)
    - Account types are recognized
    - Transaction blocks are balanced (TRNS amount = -sum(SPL amounts))
    - Required fields present (NAME for lists, TRNSTYPE/DATE for transactions)
    """
    report = {
        "valid": True,
        "sections_found": [],
        "record_counts": {},
        "warnings": [],
        "errors": [],
    }

    try:
        parsed = parse_iif(content)
    except Exception as e:
        report["valid"] = False
        report["errors"].append(f"Failed to parse IIF file: {str(e)}")
        return report

    # Check what sections exist
    for section in ["ACCNT", "CUST", "VEND", "INVITEM"]:
        count = len(parsed.get(section, []))
        if count > 0:
            report["sections_found"].append(section)
            report["record_counts"][section] = count

    txn_count = len(parsed.get("TRNS", []))
    if txn_count > 0:
        report["sections_found"].append("TRNS")
        report["record_counts"]["TRNS"] = txn_count

    if not report["sections_found"]:
        report["valid"] = False
        report["errors"].append("No valid IIF sections found in file")
        return report

    # Validate accounts
    for i, row in enumerate(parsed.get("ACCNT", [])):
        name = row.get("NAME", "").strip()
        if not name:
            report["errors"].append(f"Account row {i + 1}: missing NAME")
            report["valid"] = False
        atype = row.get("ACCNTTYPE", "").strip().upper()
        if atype and atype not in _IIF_TO_ACCOUNT_TYPE:
            report["warnings"].append(f"Account '{name}': unrecognized type '{atype}' (will default to Expense)")

    # Validate customers
    for i, row in enumerate(parsed.get("CUST", [])):
        if not row.get("NAME", "").strip():
            report["errors"].append(f"Customer row {i + 1}: missing NAME")
            report["valid"] = False

    # Validate vendors
    for i, row in enumerate(parsed.get("VEND", [])):
        if not row.get("NAME", "").strip():
            report["errors"].append(f"Vendor row {i + 1}: missing NAME")
            report["valid"] = False

    # Validate items
    for i, row in enumerate(parsed.get("INVITEM", [])):
        name = row.get("NAME", "").strip()
        if not name:
            report["errors"].append(f"Item row {i + 1}: missing NAME")
            report["valid"] = False
        itype = row.get("INVITEMTYPE", "").strip().upper()
        if itype and itype not in _IIF_TO_ITEM_TYPE:
            report["warnings"].append(f"Item '{name}': unrecognized type '{itype}' (will default to Service)")

    # Validate transaction blocks
    for i, block in enumerate(parsed.get("TRNS", [])):
        trns = block.get("trns", {})
        trns_type = trns.get("TRNSTYPE", "").strip()
        if not trns_type:
            report["errors"].append(f"Transaction block {i + 1}: missing TRNSTYPE")
            report["valid"] = False
            continue

        trns_date = trns.get("DATE", "").strip()
        if not trns_date:
            report["warnings"].append(f"Transaction block {i + 1} ({trns_type}): missing DATE")

        # Check balance: TRNS amount should equal -sum(SPL amounts)
        trns_amt = _parse_decimal(trns.get("AMOUNT", ""))
        spl_total = sum(_parse_decimal(s.get("AMOUNT", "")) for s in block.get("spl", []))
        balance = trns_amt + spl_total
        if abs(balance) > Decimal("0.01"):
            report["warnings"].append(
                f"Transaction block {i + 1} ({trns_type}): unbalanced by {balance} "
                f"(TRNS={trns_amt}, SPL total={spl_total})"
            )

    return report


# ============================================================================
# Master import orchestrator
# ============================================================================

def import_all(db: Session, content: str) -> dict:
    """Import an entire IIF file into Slowbooks.

    Processes in dependency order: accounts -> customers -> vendors -> items -> transactions.
    Returns counts of imported records and any errors.
    """
    result = {
        "accounts": 0,
        "customers": 0,
        "vendors": 0,
        "items": 0,
        "invoices": 0,
        "payments": 0,
        "estimates": 0,
        "errors": [],
        "warnings": [],
    }

    parsed = parse_iif(content)

    # Import lists first (order matters for FK resolution)
    if parsed["ACCNT"]:
        r = import_accounts(db, parsed["ACCNT"])
        result["accounts"] = r["imported"]
        result["errors"].extend(r["errors"])

    if parsed["CUST"]:
        r = import_customers(db, parsed["CUST"])
        result["customers"] = r["imported"]
        result["errors"].extend(r["errors"])

    if parsed["VEND"]:
        r = import_vendors(db, parsed["VEND"])
        result["vendors"] = r["imported"]
        result["errors"].extend(r["errors"])

    if parsed["INVITEM"]:
        r = import_items(db, parsed["INVITEM"])
        result["items"] = r["imported"]
        result["errors"].extend(r["errors"])

    # Import transactions
    if parsed["TRNS"]:
        r = import_transactions(db, parsed["TRNS"])
        counts = r["imported"]
        result["invoices"] = counts.get("invoices", 0)
        result["payments"] = counts.get("payments", 0)
        result["estimates"] = counts.get("estimates", 0)
        result["errors"].extend(r["errors"])
        result["warnings"].extend(r.get("warnings", []))

    db.commit()
    return result
