# ============================================================================
# GnuCash import dialect.
#
#   - Accounts export (File → Export → Accounts to CSV): "Type"
#     (BANK/CASH/ASSET/CREDIT/LIABILITY/EQUITY/INCOME/EXPENSE/
#     RECEIVABLE/PAYABLE/STOCK/MUTUAL), "Full Account Name", "Name".
#     We import the FULL account name ("Assets:Current:Checking") so the
#     tree stays readable after flattening.
#   - Transactions export: one row per split, grouped by "Transaction
#     ID"; a single SIGNED "Amount Num." column (positive = debit).
#   - GnuCash has no trial-balance CSV export; verification is skipped
#     with the standard warning unless one is hand-supplied.
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

LABEL = "GnuCash"

_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%d.%m.%Y")

_GNUCASH_TYPE_MAP = {
    "bank": AccountType.ASSET,
    "cash": AccountType.ASSET,
    "asset": AccountType.ASSET,
    "receivable": AccountType.ASSET,
    "stock": AccountType.ASSET,
    "mutual": AccountType.ASSET,
    "credit": AccountType.LIABILITY,
    "liability": AccountType.LIABILITY,
    "payable": AccountType.LIABILITY,
    "equity": AccountType.EQUITY,
    "income": AccountType.INCOME,
    "expense": AccountType.EXPENSE,
}


def _is_placeholder(row: dict) -> bool:
    flag = field(row, "placeholder", "hidden").lower()
    return flag in ("t", "true", "y", "yes", "1")


classify_filename = make_classifier()


def parse_coa(csv_text: str) -> tuple[list[dict], list[str]]:
    accounts, errors = [], []
    for i, row in enumerate(sniff_reader(csv_text), start=2):
        name = field(row, "full account name", "account name", "name")
        if not name:
            continue
        if _is_placeholder(row):
            continue  # structural placeholder accounts are non-postable
        gnc_type = field(row, "type", "account type").lower()
        if gnc_type == "root":
            continue
        acct_type = _GNUCASH_TYPE_MAP.get(gnc_type)
        if not acct_type:
            errors.append(f"COA row {i}: unmapped GnuCash account type {gnc_type!r}")
            continue
        accounts.append(
            {
                "code": field(row, "account code", "code") or None,
                "name": name,
                "type": acct_type,
                "description": field(row, "description", "notes") or None,
            }
        )
    return accounts, errors


def parse_gl(csv_text: str) -> tuple[list[dict], list[str]]:
    rows, errors = [], []
    for i, row in enumerate(sniff_reader(csv_text), start=2):
        account = field(row, "full account name", "account name", "account")
        date_raw = field(row, "date")
        if not account and not date_raw:
            continue
        try:
            # One signed amount column: positive = debit, negative = credit
            signed = parse_amount(
                field(row, "amount num.", "amount num", "amount", "value")
            )
            rows.append(
                {
                    "journal": field(row, "transaction id", "id"),
                    "date": parse_date(date_raw, _DATE_FORMATS),
                    "account": account,
                    "description": field(row, "description", "memo", "notes"),
                    "reference": field(row, "number", "num", "transaction id"),
                    "debit": signed if signed > 0 else Decimal("0"),
                    "credit": -signed if signed < 0 else Decimal("0"),
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
        name = field(row, "full account name", "account name", "account", "name")
        if not name:
            continue
        try:
            debit = parse_amount(field(row, "debit", "debit amount"))
            credit = parse_amount(field(row, "credit", "credit amount"))
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
    return run_import_bundle(db, bundle, _PARSERS, "gnucash_import", LABEL)
