# ============================================================================
# Xero CSV import — chart of accounts + general ledger, with dry-run.
#
# The Xero DIALECT only; the dry-run/verify/import contract lives in
# migration_common (shared with the MYOB importer). Implements the
# joelmacklow fork's Xero-import spec: files detected by name, Xero
# column aliases tolerated, dry-run gates the import.
#
# CSV-only; no XLSX, no live Xero API (per the spec's constraints).
# ============================================================================

import logging
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.accounts import AccountType
from app.services.migration_common import (
    dry_run_bundle,
    field,
    parse_amount,
    parse_date,
    run_import_bundle,
    sniff_reader,
    strip_code_suffix,
)

logger = logging.getLogger(__name__)

_DATE_FORMATS = ("%d %b %Y", "%d/%m/%Y", "%m/%d/%Y", "%Y-%m-%d", "%d %B %Y")

# Xero account "Type"/"Class" values → our AccountType
_XERO_TYPE_MAP = {
    "bank": AccountType.ASSET,
    "current asset": AccountType.ASSET,
    "current assets": AccountType.ASSET,
    "fixed asset": AccountType.ASSET,
    "fixed assets": AccountType.ASSET,
    "inventory": AccountType.ASSET,
    "non-current asset": AccountType.ASSET,
    "prepayment": AccountType.ASSET,
    "asset": AccountType.ASSET,
    "current liability": AccountType.LIABILITY,
    "current liabilities": AccountType.LIABILITY,
    "liability": AccountType.LIABILITY,
    "non-current liability": AccountType.LIABILITY,
    "equity": AccountType.EQUITY,
    "revenue": AccountType.INCOME,
    "sales": AccountType.INCOME,
    "income": AccountType.INCOME,
    "other income": AccountType.INCOME,
    "direct costs": AccountType.COGS,
    "cost of goods sold": AccountType.COGS,
    "expense": AccountType.EXPENSE,
    "expenses": AccountType.EXPENSE,
    "overheads": AccountType.EXPENSE,
    "depreciation": AccountType.EXPENSE,
}

# Filename fragments → bundle slot
_FILE_KINDS = (
    ("chart", "coa"),
    ("account", "coa"),
    ("general", "gl"),
    ("ledger", "gl"),
    ("journal", "gl"),
    ("trial", "tb"),
)


def classify_filename(name: str) -> str | None:
    lowered = (name or "").lower()
    for fragment, kind in _FILE_KINDS:
        if fragment in lowered:
            return kind
    return None


def parse_coa(csv_text: str) -> tuple[list[dict], list[str]]:
    accounts, errors = [], []
    for i, row in enumerate(sniff_reader(csv_text), start=2):
        name = field(row, "name", "account name", "account")
        if not name:
            continue
        xero_type = field(row, "type", "account type", "class").lower()
        acct_type = _XERO_TYPE_MAP.get(xero_type)
        if not acct_type:
            errors.append(f"COA row {i}: unmapped Xero account type {xero_type!r}")
            continue
        accounts.append(
            {
                "code": field(row, "code", "account code", "*code") or None,
                "name": name,
                "type": acct_type,
                "description": field(row, "description") or None,
            }
        )
    return accounts, errors


def parse_gl(csv_text: str) -> tuple[list[dict], list[str]]:
    """Parse GL rows and group them into journals.

    Grouping key: Xero's journal number when present, otherwise
    (date, reference/source) — every group must balance to zero.
    """
    rows, errors = [], []
    for i, row in enumerate(sniff_reader(csv_text), start=2):
        acct = field(row, "account", "account name")
        date_raw = field(row, "date", "journal date")
        if not acct and not date_raw:
            continue  # blank/summary row
        try:
            rows.append(
                {
                    "journal": field(
                        row,
                        "journal number",
                        "journal no",
                        "journal #",
                        "journalnumber",
                    ),
                    "date": parse_date(date_raw, _DATE_FORMATS),
                    "account": acct,
                    "description": field(row, "description", "details", "narration"),
                    "reference": field(row, "reference", "source"),
                    "debit": parse_amount(field(row, "debit", "debit (source)")),
                    "credit": parse_amount(field(row, "credit", "credit (source)")),
                }
            )
        except ValueError as exc:
            errors.append(f"GL row {i}: {exc}")

    journals: dict = {}
    for row in rows:
        key = row["journal"] or f"{row['date'].isoformat()}|{row['reference']}"
        journals.setdefault(key, []).append(row)
    return list(journals.values()), errors


def parse_tb(csv_text: str) -> tuple[dict, list[str]]:
    """Trial balance: {account_name_lower: net_debit_minus_credit}."""
    balances, errors = {}, []
    for i, row in enumerate(sniff_reader(csv_text), start=2):
        name = field(row, "account", "account name", "name")
        if not name:
            continue
        try:
            debit = parse_amount(field(row, "debit", "debit ytd", "ytd debit"))
            credit = parse_amount(field(row, "credit", "credit ytd", "ytd credit"))
        except ValueError as exc:
            errors.append(f"TB row {i}: {exc}")
            continue
        key = strip_code_suffix(name)
        balances[key] = balances.get(key, Decimal("0")) + debit - credit
    return balances, errors


_PARSERS = {"coa": parse_coa, "gl": parse_gl, "tb": parse_tb}


def dry_run(db: Session, bundle: dict) -> dict:
    return dry_run_bundle(db, bundle, _PARSERS, "Xero")


def run_import(db: Session, bundle: dict) -> dict:
    return run_import_bundle(db, bundle, _PARSERS, "xero_import", "Xero")
