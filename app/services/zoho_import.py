# ============================================================================
# Zoho Books import dialect.
#
#   - Chart of Accounts export: "Account Name" / "Account Type" (Zoho's
#     types: Bank, Cash, Stock, Payment Clearing, ...).
#   - Journal export: "Journal Number" (or Journal#), "Journal Date"/
#     "Date", "Account", "Debit" / "Credit", "Reference Number".
#   - Trial Balance report for verification.
#   - Date order is org-configurable in Zoho; ISO first, then dd/mm and
#     mm/dd — the dry-run's trial-balance check catches order mistakes.
# ============================================================================

import logging
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.accounts import AccountType
from app.services.migration_common import (
    dry_run_bundle,
    field,
    make_classifier,
    parse_amount,
    parse_date,
    run_import_bundle,
    sniff_reader,
    strip_code_suffix,
)

logger = logging.getLogger(__name__)

LABEL = "Zoho Books"

_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d %b %Y")

_ZOHO_TYPE_MAP = {
    "bank": AccountType.ASSET,
    "cash": AccountType.ASSET,
    "fixed asset": AccountType.ASSET,
    "stock": AccountType.ASSET,
    "inventory": AccountType.ASSET,
    "other asset": AccountType.ASSET,
    "other current asset": AccountType.ASSET,
    "accounts receivable": AccountType.ASSET,
    "payment clearing": AccountType.ASSET,
    "input tax": AccountType.ASSET,
    "equity": AccountType.EQUITY,
    "income": AccountType.INCOME,
    "other income": AccountType.INCOME,
    "expense": AccountType.EXPENSE,
    "other expense": AccountType.EXPENSE,
    "cost of goods sold": AccountType.COGS,
    "other current liability": AccountType.LIABILITY,
    "long term liability": AccountType.LIABILITY,
    "other liability": AccountType.LIABILITY,
    "accounts payable": AccountType.LIABILITY,
    "credit card": AccountType.LIABILITY,
    "output tax": AccountType.LIABILITY,
}

classify_filename = make_classifier()


def parse_coa(csv_text: str) -> tuple[list[dict], list[str]]:
    accounts, errors = [], []
    for i, row in enumerate(sniff_reader(csv_text), start=2):
        name = field(row, "account name", "name", "account")
        if not name:
            continue
        zoho_type = field(row, "account type", "type").lower()
        acct_type = _ZOHO_TYPE_MAP.get(zoho_type)
        if not acct_type:
            errors.append(f"COA row {i}: unmapped Zoho account type {zoho_type!r}")
            continue
        accounts.append(
            {
                "code": field(row, "account code", "account number") or None,
                "name": name,
                "type": acct_type,
                "description": field(row, "description") or None,
            }
        )
    return accounts, errors


def parse_gl(csv_text: str) -> tuple[list[dict], list[str]]:
    rows, errors = [], []
    for i, row in enumerate(sniff_reader(csv_text), start=2):
        account = field(row, "account", "account name")
        date_raw = field(row, "journal date", "date", "transaction date")
        if not account and not date_raw:
            continue
        try:
            rows.append(
                {
                    "journal": field(
                        row, "journal number", "journal#", "journal no", "journal no."
                    ),
                    "date": parse_date(date_raw, _DATE_FORMATS),
                    "account": account,
                    "description": field(
                        row, "description", "notes", "transaction details"
                    ),
                    "reference": field(
                        row, "reference number", "reference#", "reference"
                    ),
                    "debit": parse_amount(field(row, "debit", "debit amount")),
                    "credit": parse_amount(field(row, "credit", "credit amount")),
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
    balances, errors = {}, []
    for i, row in enumerate(sniff_reader(csv_text), start=2):
        name = field(row, "account", "account name", "name")
        if not name:
            continue
        try:
            debit = parse_amount(field(row, "debit", "debit total", "debit amount"))
            credit = parse_amount(field(row, "credit", "credit total", "credit amount"))
        except ValueError as exc:
            errors.append(f"TB row {i}: {exc}")
            continue
        key = strip_code_suffix(name)
        balances[key] = balances.get(key, Decimal("0")) + debit - credit
    return balances, errors


_PARSERS = {"coa": parse_coa, "gl": parse_gl, "tb": parse_tb}


def dry_run(db: Session, bundle: dict) -> dict:
    return dry_run_bundle(db, bundle, _PARSERS, LABEL)


def run_import(db: Session, bundle: dict) -> dict:
    return run_import_bundle(db, bundle, _PARSERS, "zoho_import", LABEL)
