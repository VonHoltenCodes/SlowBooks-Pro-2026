# ============================================================================
# CSV Bank Transaction Import — Bank of America, Chase, PayPal, and any
# file with date / description / amount columns
# Extends Feature 18 (bank feed import) to support CSV bank statement exports.
#
# Column mapping & pitfalls documented in the skill. Key rules:
#   - Chase checking: Amount column already signed (neg=debit, pos=credit)
#   - Chase credit:   Amount column already signed (neg=charge, pos=payment)
#   - PayPal:         Gross (NOT Net) = transaction amount;
#                     Fee column goes to Merchant Fee expense (6120)
#   - Generic:        a header naming a date, a description and either one
#                     signed amount or money-out / money-in columns; when
#                     no header says so, the import dialog asks the user
#                     which column is which (a "mapping")
# ============================================================================

import csv
import hashlib
import io
import logging
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Optional

from sqlalchemy.orm import Session

from app.models.banking import BankTransaction
from app.services.accounting import _q
from app.services.bank_rules_engine import apply_bank_rules
from app.services.safe_errors import DataProblem

logger = logging.getLogger(__name__)

# ── Column signatures for format auto-detection ──────────────────────────

CHASE_CHECKING_SIG = {"Details", "Posting Date", "Description", "Amount", "Type"}
CHASE_CREDIT_SIG = {
    "Transaction Date",
    "Post Date",
    "Description",
    "Category",
    "Type",
    "Amount",
}
PAYPAL_SIG = {
    "Date",
    "Time",
    "Name",
    "Type",
    "Status",
}
PAYPAL_NEW_SIG = {
    "Date",
    "Time",
    "Description",
    "Gross",
    "Fee",
    "Net",
    "Transaction ID",
    "From Email Address",
    "Name",
}
BOFA_DETAIL_SIG = {"Date", "Description", "Amount", "Running Bal."}

# How far into a file parse_csv will look for the header row. Bank of
# America puts a statement summary of roughly eight lines above it;
# every other supported export puts the header on row 1.
PREAMBLE_SCAN_LINES = 25


def detect_format(headers: set[str]) -> str:
    """Detect CSV format by header column signature (not filename)."""
    if CHASE_CHECKING_SIG.issubset(headers):
        return "chase_checking"
    if CHASE_CREDIT_SIG.issubset(headers):
        return "chase_credit"
    if PAYPAL_SIG.issubset(headers):
        return "paypal"
    if PAYPAL_NEW_SIG.issubset(headers):
        return "paypal_new"
    if BOFA_DETAIL_SIG.issubset(headers):
        return "bofa_detail"
    return "unknown"


def parse_date(val: str) -> date:
    """Parse a date string with broad format tolerance."""
    val = val.strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(val, fmt).date()
        except ValueError:
            continue
    try:
        from dateutil import parser as dateparser

        return dateparser.parse(val).date()
    except ImportError:
        pass
    raise DataProblem(f"Cannot parse date: {val}")


# ── Format-specific parsers ──────────────────────────────────────────────


def parse_chase_checking(reader: csv.DictReader) -> list[dict]:
    """Parse Chase personal checking CSV.

    Columns: Details, Posting Date, Description, Amount, Type, Balance, Check or Slip #
    """
    transactions = []
    for row in reader:
        txn_type = (row.get("Details") or "").strip()
        date_str = (row.get("Posting Date") or "").strip()
        description = (row.get("Description") or "").strip()
        amount_str = (row.get("Amount") or "").strip()
        check_slip = (row.get("Check or Slip #") or "").strip()

        if not date_str or not amount_str:
            continue

        try:
            txn_date = parse_date(date_str)
            amount = Decimal(amount_str)
        except (ValueError, InvalidOperation) as e:
            logger.warning("Skipping chase checking row: %s", e)
            continue

        transactions.append(
            {
                "date": txn_date,
                "amount": amount,  # already signed: negative=debit, positive=credit
                "payee": description,
                "description": description,
                "check_number": check_slip if check_slip else None,
                "import_id": f"chk_{txn_date.isoformat()}_{amount}",
                "txn_type": txn_type,
            }
        )
    return transactions


def parse_chase_credit(reader: csv.DictReader) -> list[dict]:
    """Parse Chase credit card CSV.

    Columns: Transaction Date, Post Date, Description, Category, Type, Amount, Memo
    """
    transactions = []
    for row in reader:
        date_str = (row.get("Transaction Date") or "").strip()
        description = (row.get("Description") or "").strip()
        category = (row.get("Category") or "").strip()
        amount_str = (row.get("Amount") or "").strip()
        memo = (row.get("Memo") or "").strip()

        if not date_str or not amount_str:
            continue

        try:
            txn_date = parse_date(date_str)
            amount = Decimal(amount_str)
        except (ValueError, InvalidOperation) as e:
            logger.warning("Skipping chase credit row: %s", e)
            continue

        transactions.append(
            {
                "date": txn_date,
                "amount": amount,  # already signed: negative=charge, positive=payment/return
                "payee": description,
                "description": (
                    f"{category} - {description}" if category else description
                ),
                "check_number": None,
                "import_id": f"cc_{txn_date.isoformat()}_{amount}",
                "category": category,
                "memo": memo,
            }
        )
    return transactions


def parse_paypal(reader: csv.DictReader) -> list[dict]:
    """Parse PayPal CSV.

    CRITICAL DIFFERENCE from raw intuition: map *Gross* (not Net) as the
    transaction amount. Net understates revenue because PayPal already
    subtracted the fee. Route the Fee column to Merchant Fee expense (6120)
    in a separate split. See imports/csv-import.md for worked example.

    Additionally, skip paired "Bank Deposit to PP Account" rows — they are
    the mirror-image of Express Checkout payments and net to zero. The real
    cash movement shows up in the Chase checking CSV as an IAT (PAYPAL)
    transfer.
    """
    transactions = []
    for row in reader:
        date_str = (row.get("Date") or "").strip()
        name = (row.get("Name") or "").strip()
        txn_type = (row.get("Type") or "").strip()
        status = (row.get("Status") or "").strip()
        gross_str = (row.get("Gross") or "").strip()
        fee_str = (row.get("Fee") or "").strip()
        item_title = (row.get("Item Title") or "").strip()

        if not date_str or not gross_str:
            continue

        try:
            txn_date = parse_date(date_str)
            gross = Decimal(gross_str)
            fee = Decimal(fee_str) if fee_str else Decimal("0.00")
        except (ValueError, InvalidOperation) as e:
            logger.warning("Skipping PayPal row: %s", e)
            continue

        # Skip paired Bank Deposit rows — they're the mirror of payments
        if txn_type == "Bank Deposit to PP Account":
            continue

        # Build description from available fields
        desc_parts = [p for p in [name, item_title, txn_type] if p]
        description = " | ".join(desc_parts)

        transactions.append(
            {
                "date": txn_date,
                "amount": gross,  # Gross, NOT Net
                "payee": name or item_title or "PayPal Transfer",
                "description": description,
                "check_number": None,
                "import_id": f"pp_{txn_date.isoformat()}_{gross}",
                "fee": fee,
                "status": status,
            }
        )
    return transactions


def parse_paypal_new(reader: csv.DictReader) -> list[dict]:
    """Parse new-style PayPal CSV (2026 format — simpler columns).

    Columns: Date, Time, Time Zone, Description, Currency, Gross, Fee, Net,
             Balance, Transaction ID, From Email Address, Name, Bank Name,
             Bank Account, Shipping and Handling Amount, Sales Tax, Invoice ID,
             Reference Txn ID

    Key difference from old format: no 'Type' or 'Status' columns.
    Uses Description + Name as payee, Gross as amount.
    """
    transactions = []
    for row in reader:
        date_str = (row.get("Date") or "").strip()
        name = (row.get("Name") or "").strip()
        description = (row.get("Description") or "").strip()
        gross_str = (row.get("Gross") or "").strip()
        fee_str = (row.get("Fee") or "").strip()

        if not date_str or not gross_str:
            continue

        try:
            txn_date = parse_date(date_str)
            gross = Decimal(gross_str)
            fee = Decimal(fee_str) if fee_str else Decimal("0.00")
        except (ValueError, InvalidOperation) as e:
            logger.warning("Skipping PayPal row: %s", e)
            continue

        # Skip Bank Deposit to PP Account rows (mirror entries)
        if description.startswith("Bank Deposit to PP Account"):
            continue

        payee = name or description or "PayPal Transfer"
        desc = (
            f"{description} | {name}"
            if name and description
            else (description or name or "")
        )

        transactions.append(
            {
                "date": txn_date,
                "amount": gross,
                "payee": payee,
                "description": desc,
                "check_number": None,
                "import_id": f"pp_{txn_date.isoformat()}_{gross}",
                "fee": fee,
            }
        )
    return transactions


# Description prefixes Bank of America uses for statement metadata rows that
# sit inside the transaction table. These are balances, not transactions.
_BOFA_STATEMENT_METADATA = ("beginning balance", "ending balance")


def _parse_bofa_amount(val: str) -> Decimal:
    """Parse Bank of America amounts such as ``1,234.56`` or ``($25.00)``."""
    normalized = val.strip().replace(",", "").replace("$", "")
    if normalized.startswith("(") and normalized.endswith(")"):
        normalized = f"-{normalized[1:-1]}"
    return Decimal(normalized)


def parse_bofa_detail(reader: csv.DictReader) -> list[dict]:
    """Parse Bank of America's detailed checking/savings CSV export.

    The export starts with a statement-summary block before the real header.
    ``parse_csv`` positions the reader at that header.

    A beginning-balance row is statement metadata, not a transaction, so it
    stays out of the review queue; opening balances are posted separately
    through the linked ledger account (see ``bank_posting.post_opening_balance``).
    Bank of America normally emits that row with an empty Amount, which the
    blank-amount guard below already drops — but it is dropped **by
    description as well**, because relying on the blank was relying on a
    detail of one export layout to enforce a rule about what a transaction
    is. If the row ever carries an amount it would otherwise import as a
    deposit and overstate the account by the opening balance.
    """
    transactions = []
    for row in reader:
        date_str = (row.get("Date") or "").strip()
        description = (row.get("Description") or "").strip()
        amount_str = (row.get("Amount") or "").strip()

        if not date_str or not amount_str:
            continue
        if description.lower().startswith(_BOFA_STATEMENT_METADATA):
            continue

        try:
            txn_date = parse_date(date_str)
            amount = _parse_bofa_amount(amount_str)
        except (ValueError, InvalidOperation) as e:
            logger.warning("Skipping Bank of America row: %s", e)
            continue

        transactions.append(
            {
                "date": txn_date,
                "amount": amount,
                "payee": description,
                "description": description,
                "check_number": None,
            }
        )
    return transactions


# ── Generic layouts: date, description, amount (or money out / money in) ──
#
# A plain "Date,Description,Amount" export from a bank we have no named
# parser for was "Unknown CSV format", with no way forward (exploratory
# 2.17.3, W-L15). A header that names the three things is enough now; and
# when none does, the import dialog asks which column is which and sends
# that back as a mapping: {"date": i, "description": i, "payee": i|None,
# "amount": i|None, "debit": i|None, "credit": i|None, "check_number":
# i|None, "date_format": "auto"|..., "has_header": bool} — column indexes,
# so a file without a header row (or with two columns of the same name)
# maps just as well.


def _norm(header: str) -> str:
    return " ".join(header.strip().strip('"').strip("'").lower().split())


_ROLE_HEADERS = {
    "date": (
        "date",
        "transaction date",
        "trans date",
        "trans. date",
        "txn date",
        "posted date",
        "posting date",
        "post date",
        "value date",
        "booking date",
        "effective date",
    ),
    "description": (
        "description",
        "transaction description",
        "details",
        "transaction details",
        "narrative",
        "memo",
        "notes",
    ),
    "payee": ("payee", "name", "merchant", "vendor", "payee name", "merchant name"),
    "amount": (
        "amount",
        "transaction amount",
        "amount (usd)",
        "amount usd",
        "usd amount",
        "amt",
    ),
    "debit": (
        "debit",
        "debits",
        "debit amount",
        "withdrawal",
        "withdrawals",
        "withdrawal amount",
        "money out",
        "paid out",
    ),
    "credit": (
        "credit",
        "credits",
        "credit amount",
        "deposit",
        "deposits",
        "deposit amount",
        "money in",
        "paid in",
    ),
    "check_number": (
        "check",
        "check number",
        "check #",
        "check no",
        "check no.",
        "chk",
        "chk #",
        "cheque",
        "cheque number",
        "check or slip #",
    ),
}

DATE_FORMATS = ("auto", "MM/DD/YYYY", "DD/MM/YYYY", "YYYY-MM-DD")

UNKNOWN_LAYOUT_TEXT = (
    "SlowBooks doesn't recognise this file's columns. Choose which column holds "
    "the date, the description and the amount, then preview again."
)

# Rows shown under the column pickers so the user can see what each holds.
MAPPING_SAMPLE_ROWS = 5


def _generic_roles(cells: list[str]) -> dict:
    """Column index per role, from a header row's names (first match wins).

    With no description-like column, the payee column is the description."""
    roles: dict = {}
    for index, cell in enumerate(cells):
        name = _norm(cell)
        for role, names in _ROLE_HEADERS.items():
            if role not in roles and name in names:
                roles[role] = index
                break
    if "description" not in roles and "payee" in roles:
        roles["description"] = roles.pop("payee")
    return roles


def _is_generic_header(roles: dict) -> bool:
    has_amount = "amount" in roles or ("debit" in roles and "credit" in roles)
    return "date" in roles and "description" in roles and has_amount


_NUMERIC_DATE = re.compile(r"^\s*(\d{1,4})[/.\-](\d{1,2})[/.\-](\d{1,4})(?:\D|$)")


def _date_order(date_format: str, values: list[str]) -> str:
    """ "mdy" | "dmy" | "ymd" for the whole file. "auto" reads the values: a
    four-digit first part is year-first, a first part over 12 can only be a
    day; otherwise the US month-first order."""
    explicit = {"MM/DD/YYYY": "mdy", "DD/MM/YYYY": "dmy", "YYYY-MM-DD": "ymd"}
    if date_format in explicit:
        return explicit[date_format]
    for value in values:
        m = _NUMERIC_DATE.match(value)
        if not m:
            continue
        if len(m.group(1)) == 4:
            return "ymd"
        if int(m.group(1)) > 12:
            return "dmy"
    return "mdy"


def _mapped_date(value: str, order: str) -> date:
    m = _NUMERIC_DATE.match(value)
    if not m:
        return parse_date(value)  # "Sep 1, 2026" and friends
    a, b, c = (int(g) for g in m.groups())
    if order == "ymd":
        year, month, day = a, b, c
    elif order == "dmy":
        day, month, year = a, b, c
    else:
        month, day, year = a, b, c
    if year < 100:  # a two-digit year, as strptime's %y reads it
        year += 2000 if year < 69 else 1900
    return date(year, month, day)


def _parse_amount(value: str) -> Decimal:
    """1,234.56 · $1,234.56 · -25.00 · (25.00) · 25.00- → Decimal."""
    text = value.strip().replace(",", "").replace("$", "").replace(" ", "")
    negative = False
    if text.startswith("(") and text.endswith(")"):
        text, negative = text[1:-1], True
    if text.endswith("-"):
        text, negative = text[:-1], not negative
    amount = Decimal(text.lstrip("+"))
    return _q(-amount if negative else amount)


def _clean_mapping(mapping: dict) -> dict:
    """The user's column choices, checked; a DataProblem says what to fix."""
    if not isinstance(mapping, dict):
        raise DataProblem("Choose which column holds each part of a transaction.")

    def column(key, missing=None):
        raw = mapping.get(key)
        if raw is None or raw == "":
            if missing:
                raise DataProblem(missing)
            return None
        try:
            index = int(raw)
        except (TypeError, ValueError):
            raise DataProblem("Choose the columns from the lists.") from None
        if index < 0:
            raise DataProblem("Choose the columns from the lists.")
        return index

    out = {
        "date": column("date", "Choose the column that holds the date."),
        "description": column(
            "description", "Choose the column that holds the description."
        ),
        "payee": column("payee"),
        "amount": column("amount"),
        "debit": column("debit"),
        "credit": column("credit"),
        "check_number": column("check_number"),
    }
    sides = out["debit"] is not None or out["credit"] is not None
    if out["amount"] is None and not sides:
        raise DataProblem(
            "Choose the amount column, or the money-out and money-in columns."
        )
    if out["amount"] is not None and sides:
        raise DataProblem(
            "Choose either one amount column or the money-out and money-in "
            "columns, not both."
        )
    date_format = mapping.get("date_format") or "auto"
    if date_format not in DATE_FORMATS:
        raise DataProblem("Choose a date format from the list.")
    out["date_format"] = date_format
    out["has_header"] = bool(mapping.get("has_header", True))
    return out


def _cell(row: list[str], index) -> str:
    if index is None or index >= len(row):
        return ""
    return (row[index] or "").strip()


def _layout(rows: list[list[str]], has_header: bool | None = None) -> dict:
    """What the mapping step shows: the first row's cells (the header, if it
    is one), a few rows under it, and a first guess at the roles."""
    rows = [r for r in rows if any((c or "").strip() for c in r)]
    first = rows[0] if rows else []
    roles = _generic_roles(first)
    if has_header is None:
        # A first row that names a role is a header; one that is all data
        # (a date and numbers) is not.
        has_header = bool(roles) or not any(
            _NUMERIC_DATE.match((c or "").strip()) for c in first
        )
    return {
        "header_row": [(c or "").strip() for c in first],
        "sample": [
            [(c or "").strip() for c in r] for r in rows[1 : MAPPING_SAMPLE_ROWS + 1]
        ],
        "has_header": has_header,
        "suggested": roles,
    }


def _parse_mapped(lines: list[str], mapping: dict, fmt: str = "generic") -> dict:
    rows = list(csv.reader(lines))
    try:
        m = _clean_mapping(mapping)
    except DataProblem as problem:
        return {
            "format": "unknown",
            "transactions": [],
            "error": problem.user_text,
            **_layout(rows),
        }
    body = rows[1:] if m["has_header"] else rows
    body = [r for r in body if any((c or "").strip() for c in r)]
    order = _date_order(
        m["date_format"], [_cell(r, m["date"]) for r in body if _cell(r, m["date"])]
    )
    transactions, unread = [], 0
    for row in body:
        date_str = _cell(row, m["date"])
        if not date_str:
            continue  # a totals or note row
        try:
            txn_date = _mapped_date(date_str, order)
            if m["amount"] is not None:
                raw = _cell(row, m["amount"])
                if not raw:
                    continue
                amount = _parse_amount(raw)
            else:
                out_raw = _cell(row, m["debit"])
                in_raw = _cell(row, m["credit"])
                if not out_raw and not in_raw:
                    continue
                money_out = abs(_parse_amount(out_raw)) if out_raw else Decimal("0")
                money_in = abs(_parse_amount(in_raw)) if in_raw else Decimal("0")
                amount = money_in - money_out
        except (ValueError, InvalidOperation):
            unread += 1
            continue
        if amount == 0:
            continue
        description = _cell(row, m["description"])
        payee = _cell(row, m["payee"]) or description
        check = _cell(row, m["check_number"])
        transactions.append(
            {
                "date": txn_date,
                "amount": amount,
                "payee": payee,
                "description": description or payee,
                "check_number": check or None,
            }
        )
    result = {
        "format": fmt,
        "transactions": transactions,
        "error": None,
        "unread": unread,
        "mapping": m,
    }
    if not transactions:
        result.update(
            {
                "format": "unknown",
                "error": (
                    "No transactions could be read with these columns. Check the "
                    "date format and the amount columns, then preview again."
                ),
                **_layout(rows, m["has_header"]),
            }
        )
    return result


# ── Dispatch ─────────────────────────────────────────────────────────────


def parse_csv(csv_text: str, mapping: Optional[dict] = None) -> dict:
    """Parse CSV text, auto-detect format, return parsed transactions.

    Strips BOM and surrounding quotes from headers for reliable detection.
    Handles PayPal's '\ufeff"Date"' header format. A named layout wins;
    then a header naming a date, a description and an amount (or money
    out / money in); otherwise the result carries the file's columns and a
    few rows for the import dialog's mapping step. `mapping` is that
    step's answer, and skips detection.

    Returns:
        {"format": str, "transactions": list[dict], "error": str | None}
        plus, for an unknown layout, header_row / sample / has_header /
        suggested.
    """
    # Strip BOM before handing to csv reader
    if csv_text.startswith("\ufeff"):
        csv_text = csv_text[1:]

    lines = csv_text.splitlines()
    if not any(line.strip() for line in lines):
        return {
            "format": "unknown",
            "transactions": [],
            "error": "The file is empty — there is nothing to import.",
        }

    if mapping is not None:
        return _parse_mapped(lines, mapping)

    # Some exports (notably Bank of America detail CSVs) put a statement
    # summary before the transaction table, so the header is not physical
    # row 1. Scan for it — but only across the first PREAMBLE_SCAN_LINES.
    #
    # The bound matters. A signature match is a *set subset* test, so a data
    # row whose values happen to spell a signature's column names would be
    # taken for a header; the further into the file we look, the more rows
    # get that chance, and the one we would pick is the one that truncates
    # the import. Real preambles are short (BofA's is about eight lines), so
    # a small window buys the feature without buying that risk. It also
    # keeps an unrecognized 100k-row export from being parsed twice before
    # we can say "unknown format".
    reader = None
    fmt = "unknown"
    for index, line in enumerate(lines[:PREAMBLE_SCAN_LINES]):
        candidate = next(csv.reader([line]), [])
        candidate_headers = {h.strip().strip('"').strip("'") for h in candidate if h}
        candidate_format = detect_format(candidate_headers)
        if candidate_format != "unknown":
            fmt = candidate_format
            reader = csv.DictReader(io.StringIO("\n".join(lines[index:])))
            break

    if reader is None:
        # No named layout: a header that names a date, a description and
        # an amount is enough (same bounded scan, same reason).
        for index, line in enumerate(lines[:PREAMBLE_SCAN_LINES]):
            roles = _generic_roles(next(csv.reader([line]), []))
            if _is_generic_header(roles):
                return _parse_mapped(
                    lines[index:],
                    {**roles, "has_header": True, "date_format": "auto"},
                )
        return {
            "format": "unknown",
            "transactions": [],
            "error": UNKNOWN_LAYOUT_TEXT,
            **_layout(list(csv.reader(lines[: MAPPING_SAMPLE_ROWS + 1]))),
        }

    parsers = {
        "chase_checking": parse_chase_checking,
        "chase_credit": parse_chase_credit,
        "paypal": parse_paypal,
        "paypal_new": parse_paypal_new,
        "bofa_detail": parse_bofa_detail,
    }

    transactions = parsers[fmt](reader)
    return {"format": fmt, "transactions": transactions, "error": None}


# ── Import into DB ───────────────────────────────────────────────────────


_IMPORT_ID_PREFIX = {
    "chase_checking": "chk",
    "chase_credit": "cc",
    "paypal": "pp",
    "paypal_new": "pp",
    "bofa_detail": "bofa",
}


def assign_import_ids(fmt: str, transactions: list[dict]) -> None:
    """Assign a deterministic, collision-safe import_id to each parsed row.

    CSVs have no FITID, so the id is derived from the row's content:
    (date, amount, payee|description digest) plus an occurrence counter for
    rows that are otherwise identical within the same file. This makes
    re-importing the same file (or an overlapping date-range export) skip
    every row it already imported, WITHOUT dropping legitimate duplicates —
    two identical same-day charges get occurrence 0 and 1, so both import,
    and both are recognized on a re-import.

    Overwrites any import_id the parser attached (the parser-level ids
    were (date, amount) only — the very collision this exists to fix).
    """
    prefix = _IMPORT_ID_PREFIX.get(fmt, "csv")
    occurrences: dict[tuple, int] = {}
    for txn in transactions:
        digest = hashlib.sha256(
            f"{txn.get('payee', '')}|{txn.get('description', '')}".encode()
        ).hexdigest()[:12]
        key = (txn["date"], txn["amount"], digest)
        n = occurrences.get(key, 0)
        occurrences[key] = n + 1
        txn["import_id"] = (
            f"{prefix}_{txn['date'].isoformat()}_{txn['amount']}_{digest}_{n}"
        )


def import_csv_transactions(
    db: Session,
    bank_account_id: int,
    csv_text: str,
    format_hint: Optional[str] = None,
    mapping: Optional[dict] = None,
) -> dict:
    """Parse CSV and import into BankTransaction records.

    Dedup strategy: content-derived import_id (see assign_import_ids),
    mirroring the FITID dedup in ofx_import.import_transactions. `mapping`
    is the import dialog's column choices for a layout detection missed.
    """
    result = parse_csv(csv_text, mapping)
    if result["error"]:
        return {"imported": 0, "skipped": 0, "errors": [result["error"]], "total": 0}

    transactions = result["transactions"]
    assign_import_ids(result["format"], transactions)
    imported = 0
    skipped = 0

    for txn in transactions:
        existing = (
            db.query(BankTransaction)
            .filter(
                BankTransaction.bank_account_id == bank_account_id,
                BankTransaction.import_id == txn["import_id"],
            )
            .first()
        )
        if existing:
            skipped += 1
            continue

        description = (txn.get("description", "") or "")[:500]
        fee = txn.get("fee")
        if fee:
            # Surface the PayPal fee so the bookkeeper sees it when
            # categorizing (amount is Gross; the fee nets against it).
            description = f"{description} (fee {fee})"[:500]

        bt = BankTransaction(
            bank_account_id=bank_account_id,
            date=txn["date"],
            amount=txn["amount"],
            payee=(txn.get("payee", "") or "")[:200],
            description=description,
            check_number=txn.get("check_number"),
            import_id=txn["import_id"],
            import_source=f"csv_{result['format']}",
            match_status="unmatched",
        )
        db.add(bt)
        imported += 1

    db.commit()

    matched = 0
    if imported > 0:
        apply_bank_rules(db, bank_account_id)
        from app.services.ofx_import import _auto_match_new

        matched = _auto_match_new(db, bank_account_id)

    return {
        "imported": imported,
        "skipped": skipped,
        "matched": matched,
        "errors": [],
        "total": len(transactions),
        "format": result["format"],
    }
