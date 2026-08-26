# ============================================================================
# IIF Import Service — Parse Intuit Interchange Format files into Slowbooks
#
# Handles IIF files exported from QuickBooks 2003 Pro (and compatible versions).
# The parser is forgiving: it skips unknown sections, tolerates missing fields,
# and collects per-row errors instead of aborting the entire import.
#
# Import order mirrors dependency chain:
#   classes -> accounts -> customers -> vendors -> items -> transactions
#
# Duplicate detection: matches on name (accounts, customers, vendors, items)
# or document number (invoices, payments) to prevent re-import collisions.
# ============================================================================

import logging
from datetime import datetime, date
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from app.models.accounts import Account, AccountType
from app.models.classes import TxnClass
from app.models.contacts import Customer, Vendor
from app.models.items import Item, ItemType
from app.models.invoices import Invoice, InvoiceLine, InvoiceStatus
from app.models.payments import Payment, PaymentAllocation
from app.models.estimates import Estimate, EstimateLine, EstimateStatus
from app.models.bills import Bill, BillLine, BillStatus
from app.models.transactions import Transaction
from app.services.iif_common import IIF_TO_ACCOUNT_TYPE, IIF_TO_ITEM_TYPE
from app.services.accounting import (
    _q,
    create_journal_entry,
    get_ar_account_id,
    get_default_income_account_id,
)

logger = logging.getLogger(__name__)

# ============================================================================
# IIF Parser
# ============================================================================


def parse_iif(content: str) -> dict:
    """Parse IIF file content into structured sections.

    Returns dict with keys like "ACCNT", "CLASS", "CUST", "VEND",
    "INVITEM", and "TRNS" (list of transaction blocks).

    Each list/row section contains dicts keyed by header field names.
    Transaction blocks group TRNS + SPL lines until ENDTRNS.
    """
    result = {
        "ACCNT": [],
        # QuickBooks exports the class list separately from transactions
        # (File > Utilities > Export > Lists > Class List). A transaction
        # IIF only ever carries CLASS as a column on SPL rows, never the
        # definitions — so without this section there is nothing for
        # _resolve_block_class to find.
        "CLASS": [],
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

        # List rows: ACCNT, CLASS, CUST, VEND, INVITEM
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
    """Parse a decimal string, returning 0 on failure.

    Handles QB Mac / international formats that wrap amounts in double quotes
    with thousands separators, e.g. `"99,250.02"` or `"-1,725.00"`.
    """
    if not s:
        return Decimal("0")
    try:
        cleaned = s.strip().strip('"').replace(",", "")
        if not cleaned:
            return Decimal("0")
        return Decimal(cleaned)
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
    acct = db.query(Account).filter(Account.name.ilike(name)).first()
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
# Import Functions
# ============================================================================


# Read off the column rather than hardcoding 100, so the two can't drift.
_CLASS_NAME_MAX = TxnClass.__table__.c.name.type.length


def import_classes(db: Session, rows: list) -> dict:
    """Import class-list rows (!CLASS) from IIF.

    Names are stored verbatim, including QuickBooks' "Parent:Child"
    subclass path. Classes are flat here, but the SPL CLASS column uses
    that same full path, so keeping it intact is exactly what makes the
    two match at transaction time.
    """
    imported = 0
    errors = []

    for i, row in enumerate(rows):
        sp = db.begin_nested()
        try:
            name = row.get("NAME", "").strip()
            if not name:
                errors.append({"row": i + 1, "message": "Missing class NAME"})
                sp.rollback()
                continue

            # Deliberately NOT truncated the way vendor/customer names are.
            # A shortened class still imports but then matches no SPL CLASS
            # value, so the problem resurfaces as a puzzling "class not
            # found" on every transaction that cites it. Refuse the one row
            # and name the limit instead.
            if len(name) > _CLASS_NAME_MAX:
                errors.append(
                    {
                        "row": i + 1,
                        "message": (
                            f"Class '{name[:40]}...' is {len(name)} characters, "
                            f"over the {_CLASS_NAME_MAX}-character limit — "
                            f"shorten it in QuickBooks and re-export"
                        ),
                    }
                )
                sp.rollback()
                continue

            # Case-insensitive dedup, matching both resolve_class_id's lookup
            # and POST /api/classes' conflict check, so re-importing the same
            # list is a no-op rather than a unique-constraint failure.
            existing = db.query(TxnClass).filter(TxnClass.name.ilike(name)).first()
            if existing:
                sp.rollback()
                continue

            # QuickBooks flags inactive list entries with HIDDEN=Y, which is
            # what archived means here: kept on historical rows, absent from
            # entry-form dropdowns. Missing column -> "" -> active.
            hidden = row.get("HIDDEN", "").strip().upper() in ("Y", "YES", "TRUE")

            db.add(TxnClass(name=name, is_archived=hidden))
            db.flush()
            sp.commit()
            imported += 1

        except Exception as e:
            sp.rollback()
            errors.append({"row": i + 1, "message": str(e)})

    return {"imported": imported, "errors": errors}


def import_accounts(db: Session, rows: list) -> dict:
    """Import account rows from IIF into Slowbooks.

    Handles colon-separated parent:child names by creating parents first.
    Skips accounts that already exist (matched by name or account_number).

    Collects per-account OBAMOUNT values so the caller can build a single
    opening-balance journal entry. Mac QuickBooks exports often carry all
    opening balances in the ACCNT rows themselves and omit the TRNS section
    entirely, so without this the imported books have zero balances.
    """
    imported = 0
    errors = []
    warnings = []
    # {account_id: Decimal} — summed per account, fed to _create_opening_balance_entry.
    # Multiple IIF rows can collapse onto the same DB account when dedup matches
    # by account_number (e.g. IIF "Opening Bal Equity" #3200 + our seeded
    # "Retained Earnings" #3200); we sum their OBAMOUNTs and warn.
    ob_map: dict = {}
    # {account_id: source_name} — track which IIF row first set an OBAMOUNT so
    # we can warn on collisions.
    ob_first_source: dict = {}

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
            acct_type = IIF_TO_ACCOUNT_TYPE.get(iif_type, AccountType.EXPENSE)
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
            existing = dup_q.first()

            # Parse OBAMOUNT (QB convention: positive = debit-side).
            # Applied to either the newly-created account or an existing
            # duplicate that still has a zero balance.
            obamount = _parse_decimal(row.get("OBAMOUNT", ""))

            if existing:
                sp.rollback()
                if obamount != 0:
                    if existing.id in ob_map:
                        warnings.append(
                            f"IIF account '{full_name}' deduplicated onto existing "
                            f"'{existing.name}' which already received an opening "
                            f"balance from '{ob_first_source[existing.id]}'. Summing "
                            f"both OBAMOUNTs into '{existing.name}'."
                        )
                        ob_map[existing.id] += obamount
                    else:
                        ob_map[existing.id] = obamount
                        ob_first_source[existing.id] = full_name
                continue

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
            if obamount != 0:
                ob_map[acct.id] = obamount
                ob_first_source[acct.id] = full_name
            sp.commit()
            imported += 1

        except Exception as e:
            sp.rollback()
            errors.append({"row": i + 1, "message": str(e)})

    # Build a single opening-balance journal entry from OBAMOUNT values
    # (dropping any that summed to exactly zero after merging).
    opening_balances = [(aid, amt) for aid, amt in ob_map.items() if amt != 0]
    ob_result = _create_opening_balance_entry(db, opening_balances)
    if ob_result.get("warning"):
        warnings.append(ob_result["warning"])

    return {
        "imported": imported,
        "errors": errors,
        "warnings": warnings,
        "opening_balance": ob_result,
    }


def _create_opening_balance_entry(db: Session, balances: list) -> dict:
    """Create a single balanced journal entry for account opening balances.

    QB IIF convention: OBAMOUNT is the debit-side value.
      positive  -> DR the account
      negative  -> CR the account (abs value)
    Any residual imbalance (rounding, or accounts we couldn't resolve) is
    plugged to Retained Earnings (account 3200) so the entry always balances.
    """
    from datetime import date as _date
    from app.services.accounting import create_journal_entry

    if not balances:
        return {"created": False}

    lines = []
    total = Decimal("0")
    for acct_id, amount in balances:
        if amount > 0:
            lines.append(
                {
                    "account_id": acct_id,
                    "debit": amount,
                    "credit": Decimal("0"),
                    "description": "Opening balance (IIF import)",
                }
            )
        else:
            lines.append(
                {
                    "account_id": acct_id,
                    "debit": Decimal("0"),
                    "credit": -amount,
                    "description": "Opening balance (IIF import)",
                }
            )
        total += amount

    # Plug any residual to Retained Earnings so the JE balances
    if total != 0:
        plug = (
            db.query(Account).filter(Account.account_number == "3200").first()
            or db.query(Account)
            .filter(Account.name.ilike("Opening%Balance%Equity%"))
            .first()
            or db.query(Account)
            .filter(Account.name.ilike("Retained%Earnings%"))
            .first()
        )
        if not plug:
            return {
                "created": False,
                "warning": "Could not find Retained Earnings / Opening Balance Equity "
                "account to balance opening entry; opening balances skipped.",
            }
        if total > 0:
            lines.append(
                {
                    "account_id": plug.id,
                    "debit": Decimal("0"),
                    "credit": total,
                    "description": "Opening balance plug",
                }
            )
        else:
            lines.append(
                {
                    "account_id": plug.id,
                    "debit": -total,
                    "credit": Decimal("0"),
                    "description": "Opening balance plug",
                }
            )

    try:
        txn = create_journal_entry(
            db,
            _date.today(),
            "Opening balances (IIF import)",
            lines,
            source_type="iif_import",
            reference="IIF-OPENING",
        )
        return {
            "created": True,
            "transaction_id": txn.id,
            "line_count": len(lines),
        }
    except Exception as e:
        logger.exception("Failed to create opening balance journal entry")
        return {
            "created": False,
            "warning": f"Opening balance journal entry failed: {e}",
        }


def import_customers(db: Session, rows: list) -> dict:
    """Import customer rows from IIF."""
    imported = 0
    errors = []

    for i, row in enumerate(rows):
        sp = db.begin_nested()
        try:
            name = row.get("NAME", "").strip()[:200]
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
            name = row.get("NAME", "").strip()[:200]
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
            item_type = IIF_TO_ITEM_TYPE.get(iif_type, ItemType.SERVICE)

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

    Routes by TRNSTYPE: INVOICE, PAYMENT, ESTIMATE, BILL, DEPOSIT,
    CASH SALE (QB's sales receipts). Other types (GENERAL JOURNAL, etc.)
    are silently skipped — extend the dispatch table here when adding
    support.
    """
    counts = {
        "invoices": 0,
        "payments": 0,
        "sales_receipts": 0,
        "estimates": 0,
        "bills": 0,
        "deposits": 0,
        "duplicates_skipped": 0,
    }
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
                        warnings.append(
                            f"Invoice {doc}: imported but journal entry could not be created (account mismatch)"
                        )
            elif trns_type == "PAYMENT":
                result = _import_payment(db, trns, spls)
                if result:
                    counts["payments"] += 1
                    if not result.transaction_id:
                        warnings.append(
                            f"Payment block {i+1}: imported but journal entry could not be created (account mismatch)"
                        )
            elif trns_type in ("CASH SALE", "CASHSALE", "SALES RECEIPT"):
                if not trns.get("NAME", "").strip():
                    warnings.append(
                        f"Sales receipt block {i + 1}: no customer name; "
                        f"assigned to '{WALK_IN_CUSTOMER_NAME}'"
                    )
                result = _import_cash_sale(db, trns, spls)
                if result:
                    counts["sales_receipts"] += 1
                    if not result.transaction_id:
                        doc = result.invoice_number or f"block {i+1}"
                        warnings.append(
                            f"Sales receipt {doc}: imported but journal entry could not be created (account mismatch)"
                        )
                else:
                    counts["duplicates_skipped"] += 1
            elif trns_type == "ESTIMATE":
                result = _import_estimate(db, trns, spls)
                if result:
                    counts["estimates"] += 1
            elif trns_type == "BILL":
                result = _import_bill(db, trns, spls)
                if result:
                    counts["bills"] += 1
                else:
                    # None = (vendor, bill_number) already imported —
                    # a deliberate dedup hit, not a silent drop.
                    counts["duplicates_skipped"] += 1
            elif trns_type == "DEPOSIT":
                result = _import_deposit(db, trns, spls)
                if result:
                    counts["deposits"] += 1
                else:
                    counts["duplicates_skipped"] += 1
            # Skip other transaction types (GENERAL JOURNAL, etc.) for now
            sp.commit()

        except Exception as e:
            sp.rollback()
            errors.append(
                {"row": i + 1, "message": f"Transaction block {i + 1}: {str(e)}"}
            )

    return {"imported": counts, "errors": errors, "warnings": warnings}


def _resolve_block_class(db: Session, trns_type: str, doc_ref: str, spls: list):
    """Collapse SPL.CLASS values to a single class_id at the document level.

    Classes here are header-level (Bill.class_id / Transaction.class_id),
    so when the IIF supplies a CLASS column on SPL rows:
      - All CLASS values absent/empty → None (reports group with
        Uncategorized).
      - One distinct CLASS across the block → strict lookup; a missing
        class raises the same way a missing vendor or account does.
      - Multiple distinct CLASS values → ValueError. Refusing beats
        silently filing the whole document under the first class.
    """
    from app.services.classes_service import resolve_class_id

    names = {(s.get("CLASS") or "").strip() for s in spls}
    names.discard("")
    if not names:
        return None
    if len(names) > 1:
        raise ValueError(
            f"{trns_type} {doc_ref}: multiple CLASS values {sorted(names)} in one "
            f"block — classes apply per document; split the block or unify the CLASS"
        )
    name = names.pop()
    class_id = resolve_class_id(db, name)
    if class_id is None:
        raise ValueError(
            f"{trns_type} {doc_ref}: class '{name}' not found. Import your "
            f"QuickBooks class list (File > Utilities > Export > Lists > Class "
            f"List) or create it under Settings → Classes first, or correct "
            f"the CLASS in the IIF file."
        )
    return class_id


def _validate_block_balance(trns_type: str, trns: dict, spls: list) -> Decimal:
    """Sum-to-zero check shared by BILL and DEPOSIT.

    Returns the parsed TRNS amount (raw, sign-preserving) so callers
    don't have to parse it twice. Raises ValueError if the block doesn't
    balance to within 1 cent — an unbalanced block usually means a
    hand-edited IIF where the user dropped a line.
    """
    trns_amt = _parse_decimal(trns.get("AMOUNT", ""))
    spl_total = sum((_parse_decimal(s.get("AMOUNT", "")) for s in spls), Decimal("0"))
    residual = trns_amt + spl_total
    if abs(residual) > Decimal("0.01"):
        raise ValueError(
            f"{trns_type} block does not sum to zero "
            f"(TRNS={trns_amt}, SPL total={spl_total}, residual={residual}). "
            f"Standard QB convention: TRNS and SPL amounts carry opposite signs."
        )
    return trns_amt


def _import_bill(db: Session, trns: dict, spls: list) -> Bill:
    """Create a Bill from IIF TRNS/SPL data.

    QB IIF BILL convention: TRNS.AMOUNT is negative on the AP account,
    each SPL.AMOUNT is positive on the expense account; together they
    sum to zero.

    Missing vendors and accounts SURFACE as errors rather than
    auto-create — re-running the import after fixing the config is
    preferred over silently creating unmatched-vendor bills the user
    then has to clean up. (Deliberately stricter than _import_invoice's
    auto-create-customer behavior: bills post expenses.)

    Returns the created Bill, or None when the (vendor, bill_number)
    pair was already imported (idempotent re-runs).
    """
    trns_amt = _validate_block_balance("BILL", trns, spls)

    vendor_name = trns.get("NAME", "").strip()
    if not vendor_name:
        raise ValueError("BILL: missing vendor NAME on TRNS line")
    vendor = db.query(Vendor).filter(Vendor.name == vendor_name).first()
    if not vendor:
        raise ValueError(
            f"BILL: vendor '{vendor_name}' not found. Add the vendor in "
            f"Vendors first, or correct the NAME in the IIF file."
        )

    ap_acct_name = trns.get("ACCNT", "").strip()
    if not ap_acct_name:
        raise ValueError(f"BILL ({vendor_name}): missing AP account ACCNT on TRNS line")
    ap_account = _find_account(db, ap_acct_name)
    if not ap_account:
        raise ValueError(
            f"BILL ({vendor_name}): AP account '{ap_acct_name}' not found. "
            f"Add the account in the chart of accounts first."
        )

    doc_num = trns.get("DOCNUM", "").strip()
    if not doc_num:
        raise ValueError(
            f"BILL ({vendor_name}): missing DOCNUM (bill_number is required)"
        )

    # Idempotent dedup: same (vendor, bill_number) pair.
    existing = (
        db.query(Bill)
        .filter(Bill.vendor_id == vendor.id, Bill.bill_number == doc_num)
        .first()
    )
    if existing:
        return None

    # Resolve all SPL expense accounts up front. Doing this BEFORE any
    # db.add() means a partial parse (one of three SPLs has a missing
    # account) raises cleanly without a half-created Bill row.
    block_class_id = _resolve_block_class(db, "BILL", doc_num, spls)
    spl_resolved = []
    for spl in spls:
        spl_acct_name = spl.get("ACCNT", "").strip()
        if not spl_acct_name:
            raise ValueError(f"BILL {doc_num}: SPL line missing ACCNT")
        spl_acct = _find_account(db, spl_acct_name)
        if not spl_acct:
            raise ValueError(
                f"BILL {doc_num}: expense account '{spl_acct_name}' not found"
            )
        spl_amount = _parse_decimal(spl.get("AMOUNT", ""))
        spl_resolved.append((spl, spl_acct, spl_amount))

    bill_date = _parse_iif_date(trns.get("DATE", "")) or date.today()
    due_date = _parse_iif_date(trns.get("DUEDATE", ""))
    total = _q(abs(trns_amt))

    bill = Bill(
        bill_number=doc_num,
        vendor_id=vendor.id,
        date=bill_date,
        due_date=due_date,
        terms=trns.get("TERMS", "").strip() or "Net 30",
        status=BillStatus.UNPAID,
        subtotal=total,
        tax_rate=Decimal("0"),
        tax_amount=Decimal("0"),
        total=total,
        balance_due=total,
        notes=trns.get("MEMO", "").strip() or None,
        class_id=block_class_id,
    )
    db.add(bill)
    db.flush()

    journal_lines = []
    for spl, spl_acct, spl_amount in spl_resolved:
        amt = _q(abs(spl_amount))
        memo = spl.get("MEMO", "").strip() or None
        db.add(
            BillLine(
                bill_id=bill.id,
                account_id=spl_acct.id,
                description=memo,
                quantity=Decimal("1"),
                rate=amt,
                amount=amt,
            )
        )
        if amt > 0:
            journal_lines.append(
                {
                    "account_id": spl_acct.id,
                    "debit": amt,
                    "credit": Decimal("0"),
                    "description": memo or f"Bill {doc_num}",
                }
            )

    if journal_lines and total > 0:
        journal_lines.append(
            {
                "account_id": ap_account.id,
                "debit": Decimal("0"),
                "credit": total,
                "description": f"Bill {doc_num} - {vendor_name}",
            }
        )
        txn = create_journal_entry(
            db,
            bill_date,
            f"IIF Import — Bill {doc_num} - {vendor_name}",
            journal_lines,
            source_type="bill",
            source_id=bill.id,
            class_id=block_class_id,
        )
        bill.transaction_id = txn.id

    db.flush()
    return bill


def _import_deposit(db: Session, trns: dict, spls: list) -> Transaction:
    """Create a deposit (journal-only Transaction) from IIF TRNS/SPL.

    QB IIF DEPOSIT convention: TRNS.AMOUNT positive on the bank account,
    each SPL.AMOUNT negative on the income/source account; together they
    sum to zero. Sign convention is the inverse of BILL.

    There is no Deposit model — deposits are journal-only Transactions
    with source_type='deposit', matching what the manual Make Deposits
    route produces. The IIF DOCNUM lands in Transaction.reference so
    re-imports dedupe correctly.

    Returns the created Transaction, or None when an existing deposit
    with matching (date, reference) already exists.
    """
    trns_amt = _validate_block_balance("DEPOSIT", trns, spls)

    bank_acct_name = trns.get("ACCNT", "").strip()
    if not bank_acct_name:
        raise ValueError("DEPOSIT: missing bank account ACCNT on TRNS line")
    bank_acct = _find_account(db, bank_acct_name)
    if not bank_acct:
        raise ValueError(
            f"DEPOSIT: bank account '{bank_acct_name}' not found. "
            f"Add the account in the chart of accounts first."
        )

    doc_num = trns.get("DOCNUM", "").strip()
    deposit_date = _parse_iif_date(trns.get("DATE", "")) or date.today()
    total = _q(abs(trns_amt))

    # Idempotent dedup keyed on (date, reference) when DOCNUM is set.
    # Without a DOCNUM, deposits aren't dedupable (you can legitimately
    # have two deposits of the same amount on the same day from
    # different sources), so re-runs without DOCNUM will double-import.
    if doc_num:
        existing = (
            db.query(Transaction)
            .filter(
                Transaction.source_type == "deposit",
                Transaction.reference == doc_num,
                Transaction.date == deposit_date,
            )
            .first()
        )
        if existing:
            return None

    spl_resolved = []
    for spl in spls:
        spl_acct_name = spl.get("ACCNT", "").strip()
        if not spl_acct_name:
            raise ValueError(
                f"DEPOSIT {doc_num or bank_acct_name}: SPL line missing ACCNT"
            )
        spl_acct = _find_account(db, spl_acct_name)
        if not spl_acct:
            raise ValueError(
                f"DEPOSIT {doc_num or bank_acct_name}: source account "
                f"'{spl_acct_name}' not found"
            )
        spl_amount = _parse_decimal(spl.get("AMOUNT", ""))
        spl_resolved.append((spl, spl_acct, spl_amount))

    block_class_id = _resolve_block_class(
        db, "DEPOSIT", doc_num or bank_acct_name, spls
    )

    memo = trns.get("MEMO", "").strip()
    description = memo or f"Deposit to {bank_acct.name}"

    journal_lines = [
        {
            "account_id": bank_acct.id,
            "debit": total,
            "credit": Decimal("0"),
            "description": description,
        }
    ]
    for spl, spl_acct, spl_amount in spl_resolved:
        spl_memo = spl.get("MEMO", "").strip() or None
        journal_lines.append(
            {
                "account_id": spl_acct.id,
                "debit": Decimal("0"),
                "credit": _q(abs(spl_amount)),
                "description": spl_memo or description,
            }
        )

    txn = create_journal_entry(
        db,
        deposit_date,
        f"IIF Import — {description}",
        journal_lines,
        source_type="deposit",
        reference=doc_num or None,
        class_id=block_class_id,
    )
    db.flush()
    return txn


def _import_invoice(db: Session, trns: dict, spls: list) -> Invoice:
    """Create an Invoice from IIF TRNS/SPL data.

    Convention assumed: TRNS.AMOUNT is positive (AR debit = invoice total), each
    SPL.AMOUNT is negative (the offsetting credit to income / sales-tax). This
    matches a standard QuickBooks invoice export. The abs() calls below are
    safe for that convention. Mixed-sign SPL rows (e.g. a positive "discount"
    line that reduces the invoice total) are NOT correctly handled: they would
    inflate the subtotal and trip the balance check, causing the journal to
    fall back to default_income_id. Handling discount lines requires sign-
    preserving parsing + test IIFs — separate work.
    """
    doc_num = trns.get("DOCNUM", "").strip()

    # Skip if invoice number already exists
    if doc_num:
        existing = db.query(Invoice).filter(Invoice.invoice_number == doc_num).first()
        if existing:
            return None

    # Resolve customer
    cust_name = trns.get("NAME", "").strip()[:200]
    if not cust_name:
        # A TRNS row with no NAME cannot anchor an AR document, and auto-
        # creating a Customer(name="") plants a record that is invisible in
        # list views and unsearchable — the API refuses blank names since
        # 6c82e9a, so the importer must not sneak them in the back door.
        return None
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
        # Quantize each SPL amount to 2dp before accumulating so the stored
        # subtotal matches sum(line.amount). QB Pro fuel-surcharge / per-unit
        # exports sometimes emit sub-cent AMOUNT values that drift otherwise.
        spl_amount = _q(abs(_parse_decimal(spl.get("AMOUNT", ""))))
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
            rate = _q(spl_amount / qty)

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
            {
                "account_id": ar_id,
                "debit": invoice.total,
                "credit": Decimal("0"),
                "description": f"Invoice {doc_num}",
            },
        ]

        # Find income account for credit side
        for spl in spls:
            acct_name = spl.get("ACCNT", "").strip()
            # Quantize to match the per-line amount written above; otherwise
            # the JE credits could land a cent off the AR debit when stored.
            spl_amount = _q(abs(_parse_decimal(spl.get("AMOUNT", ""))))
            if spl_amount == 0:
                continue
            acct = _find_account(db, acct_name)
            if acct:
                journal_lines.append(
                    {
                        "account_id": acct.id,
                        "debit": Decimal("0"),
                        "credit": spl_amount,
                        "description": f"Invoice {doc_num}",
                    }
                )
            else:
                unmatched_accounts.append(acct_name)

        # Only post if balanced
        total_dr = sum(Decimal(str(line["debit"])) for line in journal_lines)
        total_cr = sum(Decimal(str(line["credit"])) for line in journal_lines)
        if total_dr == total_cr and total_dr > 0:
            txn = create_journal_entry(
                db,
                invoice.date,
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
                journal_lines.append(
                    {
                        "account_id": default_income_id,
                        "debit": Decimal("0"),
                        "credit": shortfall,
                        "description": f"Invoice {doc_num} (unmatched: {', '.join(unmatched_accounts)})",
                    }
                )
                txn = create_journal_entry(
                    db,
                    invoice.date,
                    f"IIF Import — Invoice {doc_num}",
                    journal_lines,
                    source_type="invoice",
                    source_id=invoice.id,
                )
                invoice.transaction_id = txn.id

    db.flush()
    # Phase 11 (audit fix): IIF-imported invoices must also move inventory
    # for any inventory-tracked line item. Without this, bulk historical
    # import leaves a phantom COGS gap.
    db.refresh(invoice)
    from app.services.inventory_hooks import post_sale_for_invoice

    post_sale_for_invoice(db, invoice, txn_date=invoice.date)
    return invoice


def _import_payment(db: Session, trns: dict, spls: list) -> Payment:
    """Create a Payment from IIF TRNS/SPL data."""
    ref = trns.get("DOCNUM", "").strip()

    # Duplicate detection: match on customer + date + amount + reference
    cust_name = trns.get("NAME", "").strip()
    pmt_date = _parse_iif_date(trns.get("DATE", ""))
    pmt_amount = abs(_parse_decimal(trns.get("AMOUNT", "")))
    existing_q = (
        db.query(Payment)
        .join(Customer)
        .filter(
            Customer.name == cust_name,
            Payment.date == (pmt_date or date.today()),
            Payment.amount == pmt_amount,
        )
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
            invoice = (
                db.query(Invoice).filter(Invoice.invoice_number == doc_num).first()
            )
            if invoice:
                alloc_amount = abs(_parse_decimal(spl.get("AMOUNT", "")))
                alloc = PaymentAllocation(
                    payment_id=payment.id,
                    invoice_id=invoice.id,
                    amount=alloc_amount or amount,
                )
                db.add(alloc)

                # Update invoice status
                invoice.amount_paid = (invoice.amount_paid or Decimal("0")) + (
                    alloc_amount or amount
                )
                invoice.balance_due = invoice.total - invoice.amount_paid
                if invoice.balance_due <= 0:
                    invoice.status = InvoiceStatus.PAID
                elif invoice.amount_paid > 0:
                    invoice.status = InvoiceStatus.PARTIAL

    # Create journal entry
    ar_id = get_ar_account_id(db)
    if ar_id and deposit_acct and amount > 0:
        journal_lines = [
            {
                "account_id": deposit_acct.id,
                "debit": amount,
                "credit": Decimal("0"),
                "description": f"Payment from {cust_name}",
            },
            {
                "account_id": ar_id,
                "debit": Decimal("0"),
                "credit": amount,
                "description": f"Payment from {cust_name}",
            },
        ]
        txn = create_journal_entry(
            db,
            payment.date,
            f"IIF Import — Payment from {cust_name}",
            journal_lines,
            source_type="payment",
            source_id=payment.id,
        )
        payment.transaction_id = txn.id

    db.flush()
    return payment


# QB allows sales receipts with a blank Customer:Job (counter sales); an
# Invoice row can't. Blank-name CASH SALE blocks land on this named bucket
# customer — a real, visible record, unlike the blank-name auto-creates
# the importer refuses elsewhere.
WALK_IN_CUSTOMER_NAME = "Walk-In Customer"


def _import_cash_sale(db: Session, trns: dict, spls: list) -> Invoice:
    """Create a paid invoice + payment from an IIF CASH SALE block.

    QB writes sales receipts as TRNSTYPE "CASH SALE": the TRNS line debits
    the deposit account (bank / Undeposited Funds) where an INVOICE block
    would debit A/R, and SPL lines credit income/tax exactly like an
    invoice. Imported as an invoice flagged is_sales_receipt plus a
    same-day payment for the full total — the same pair of documents the
    Enter Sales Receipts screen produces, so void/report behavior matches.
    """
    trns = dict(trns)
    if not trns.get("NAME", "").strip():
        trns["NAME"] = WALK_IN_CUSTOMER_NAME
    if not trns.get("TERMS", "").strip():
        trns["TERMS"] = "Due on Receipt"

    if not trns.get("DOCNUM", "").strip():
        # POS receipts are often unnumbered. Dedup on customer + date +
        # amount (the DOCNUM-less analogue of _import_payment's check),
        # then let the numbering service assign the next number.
        cust_name = trns.get("NAME", "").strip()[:200]
        sale_date = _parse_iif_date(trns.get("DATE", "")) or date.today()
        total = abs(_parse_decimal(trns.get("AMOUNT", "")))
        existing = (
            db.query(Invoice)
            .join(Customer, Invoice.customer_id == Customer.id)
            .filter(
                Customer.name == cust_name,
                Invoice.date == sale_date,
                Invoice.total == total,
                Invoice.is_sales_receipt.is_(True),
            )
            .first()
        )
        if existing:
            return None
        from app.services.numbering import next_invoice_number

        trns["DOCNUM"] = next_invoice_number(db)

    invoice = _import_invoice(db, trns, spls)
    if invoice is None:
        return None
    invoice.is_sales_receipt = True

    # Deposit side: TRNS.ACCNT is the bank / Undeposited Funds account the
    # receipt deposited to. Same fallback chain as _import_payment.
    deposit_acct = _find_account(db, trns.get("ACCNT", "").strip())
    if not deposit_acct:
        from app.services.accounting import get_undeposited_funds_id

        uf_id = get_undeposited_funds_id(db)
        if uf_id:
            deposit_acct = db.query(Account).filter(Account.id == uf_id).first()

    total = Decimal(str(invoice.total))
    payment = Payment(
        customer_id=invoice.customer_id,
        date=invoice.date,
        amount=total,
        method=trns.get("PAYMETH", "").strip() or None,
        reference=invoice.invoice_number,
        deposit_to_account_id=deposit_acct.id if deposit_acct else None,
    )
    db.add(payment)
    db.flush()
    db.add(
        PaymentAllocation(payment_id=payment.id, invoice_id=invoice.id, amount=total)
    )
    invoice.amount_paid = total
    invoice.balance_due = Decimal("0")
    invoice.status = InvoiceStatus.PAID

    # Payment journal: DR deposit account / CR A/R, clearing the A/R debit
    # _import_invoice posted — the ledger nets to cash + income, matching
    # the app's own sales-receipt flow.
    ar_id = get_ar_account_id(db)
    if ar_id and deposit_acct and total > 0:
        journal_lines = [
            {
                "account_id": deposit_acct.id,
                "debit": total,
                "credit": Decimal("0"),
                "description": f"Sales receipt {invoice.invoice_number}",
            },
            {
                "account_id": ar_id,
                "debit": Decimal("0"),
                "credit": total,
                "description": f"Sales receipt {invoice.invoice_number}",
            },
        ]
        txn = create_journal_entry(
            db,
            invoice.date,
            f"IIF Import — Sales receipt {invoice.invoice_number}",
            journal_lines,
            source_type="payment",
            source_id=payment.id,
        )
        payment.transaction_id = txn.id

    db.flush()
    return invoice


def _import_estimate(db: Session, trns: dict, spls: list) -> Estimate:
    """Create an Estimate from IIF TRNS/SPL data."""
    doc_num = trns.get("DOCNUM", "").strip()

    if doc_num:
        existing = (
            db.query(Estimate).filter(Estimate.estimate_number == doc_num).first()
        )
        if existing:
            return None

    cust_name = trns.get("NAME", "").strip()[:200]
    if not cust_name:
        # A TRNS row with no NAME cannot anchor an AR document, and auto-
        # creating a Customer(name="") plants a record that is invisible in
        # list views and unsearchable — the API refuses blank names since
        # 6c82e9a, so the importer must not sneak them in the back door.
        return None
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
        # Same per-line quantization the invoice import uses; keeps stored
        # subtotal aligned with sum(line.amount) when the source IIF has
        # sub-cent AMOUNT values.
        spl_amount = _q(abs(_parse_decimal(spl.get("AMOUNT", ""))))
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
            rate = _q(spl_amount / qty)

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
    except Exception:
        # No exception text in the client-facing report (it can leak
        # internals — py/stack-trace-exposure); details go to the log.
        logger.exception("Failed to parse IIF file during validation")
        report["valid"] = False
        report["errors"].append(
            "Failed to parse IIF file — it does not look like valid IIF."
        )
        return report

    # Check what sections exist
    for section in ["ACCNT", "CLASS", "CUST", "VEND", "INVITEM"]:
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
        if atype and atype not in IIF_TO_ACCOUNT_TYPE:
            report["warnings"].append(
                f"Account '{name}': unrecognized type '{atype}' (will default to Expense)"
            )

    # Validate classes
    for i, row in enumerate(parsed.get("CLASS", [])):
        name = row.get("NAME", "").strip()
        if not name:
            report["errors"].append(f"Class row {i + 1}: missing NAME")
            report["valid"] = False
        elif len(name) > _CLASS_NAME_MAX:
            report["errors"].append(
                f"Class row {i + 1}: name is {len(name)} characters, over the "
                f"{_CLASS_NAME_MAX}-character limit"
            )
            report["valid"] = False

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
        if itype and itype not in IIF_TO_ITEM_TYPE:
            report["warnings"].append(
                f"Item '{name}': unrecognized type '{itype}' (will default to Service)"
            )

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
            report["warnings"].append(
                f"Transaction block {i + 1} ({trns_type}): missing DATE"
            )

        # Check balance: TRNS amount should equal -sum(SPL amounts)
        trns_amt = _parse_decimal(trns.get("AMOUNT", ""))
        spl_total = sum(
            _parse_decimal(s.get("AMOUNT", "")) for s in block.get("spl", [])
        )
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

    Processes in dependency order: classes -> accounts -> customers ->
    vendors -> items -> transactions.
    Returns counts of imported records and any errors.
    """
    result = {
        "classes": 0,
        "accounts": 0,
        "opening_balance_lines": 0,
        "customers": 0,
        "vendors": 0,
        "items": 0,
        "invoices": 0,
        "payments": 0,
        "sales_receipts": 0,
        "estimates": 0,
        "bills": 0,
        "deposits": 0,
        "errors": [],
        "warnings": [],
    }

    parsed = parse_iif(content)

    # Import lists first (order matters for FK resolution)

    # Classes have no dependencies of their own, but transactions cite them
    # by name and _resolve_block_class refuses unknown ones — so the list
    # lands before anything that can carry a CLASS.
    if parsed["CLASS"]:
        r = import_classes(db, parsed["CLASS"])
        result["classes"] = r["imported"]
        result["errors"].extend(r["errors"])

    if parsed["ACCNT"]:
        r = import_accounts(db, parsed["ACCNT"])
        result["accounts"] = r["imported"]
        result["errors"].extend(r["errors"])
        result["warnings"].extend(r.get("warnings", []))
        ob = r.get("opening_balance") or {}
        if ob.get("created"):
            result["opening_balance_lines"] = ob.get("line_count", 0)

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
        result["sales_receipts"] = counts.get("sales_receipts", 0)
        result["estimates"] = counts.get("estimates", 0)
        result["bills"] = counts.get("bills", 0)
        result["deposits"] = counts.get("deposits", 0)
        result["duplicates_skipped"] = counts.get("duplicates_skipped", 0)
        result["errors"].extend(r["errors"])
        result["warnings"].extend(r.get("warnings", []))

    db.commit()
    return result
