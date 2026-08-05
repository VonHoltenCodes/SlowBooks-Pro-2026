# ============================================================================
# Wave import dialect.
#
#   - Accounts export: "Account Name" / "Account Type" (Wave's types are
#     descriptive: "Cash and Bank", "Operating Expense", "Retained
#     Earnings: Profit", ... — mapped explicitly, then by keyword).
#   - Accounting Transactions export: one row per line, grouped by
#     "Transaction ID"; carries either Debit/Credit columns or a single
#     signed Amount column (both supported).
#   - Trial balance report for verification.
#   - Dates are ISO in Wave exports; US-style accepted as fallback.
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

LABEL = "Wave"

_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y")

_WAVE_TYPE_MAP = {
    "cash and bank": AccountType.ASSET,
    "money in transit": AccountType.ASSET,
    "expected payments from customers": AccountType.ASSET,
    "inventory": AccountType.ASSET,
    "property, plant, equipment": AccountType.ASSET,
    "depreciation and amortization": AccountType.ASSET,
    "vendor prepayments": AccountType.ASSET,
    "other short-term asset": AccountType.ASSET,
    "other long-term asset": AccountType.ASSET,
    "credit card": AccountType.LIABILITY,
    "loan and line of credit": AccountType.LIABILITY,
    "expected payments to vendors": AccountType.LIABILITY,
    "sales taxes": AccountType.LIABILITY,
    "due for payroll": AccountType.LIABILITY,
    "due to you and other business owners": AccountType.LIABILITY,
    "other short-term liability": AccountType.LIABILITY,
    "other long-term liability": AccountType.LIABILITY,
    "business owner contribution and drawing": AccountType.EQUITY,
    "retained earnings: profit": AccountType.EQUITY,
    "income": AccountType.INCOME,
    "discount": AccountType.INCOME,
    "other income": AccountType.INCOME,
    "uncategorized income": AccountType.INCOME,
    "cost of goods sold": AccountType.COGS,
    "operating expense": AccountType.EXPENSE,
    "payment processing fee": AccountType.EXPENSE,
    "payroll expense": AccountType.EXPENSE,
    "uncategorized expense": AccountType.EXPENSE,
    "loss on foreign exchange": AccountType.EXPENSE,
}

# Keyword fallback for Wave type strings we haven't enumerated — their
# labels are self-describing enough that this rarely misfires, and the
# dry-run surfaces anything that lands on None.
_KEYWORD_RULES = (
    (("cost of goods",), AccountType.COGS),
    (("income", "discount"), AccountType.INCOME),
    (("expense", "fee", "loss"), AccountType.EXPENSE),
    (("equity", "owner", "retained"), AccountType.EQUITY),
    (
        ("liability", "credit card", "loan", "tax", "payroll", "due "),
        AccountType.LIABILITY,
    ),
    (
        ("asset", "cash", "bank", "inventory", "transit", "prepayment", "plant"),
        AccountType.ASSET,
    ),
)


def _map_type(raw: str):
    lowered = raw.lower().strip()
    if lowered in _WAVE_TYPE_MAP:
        return _WAVE_TYPE_MAP[lowered]
    for keywords, acct_type in _KEYWORD_RULES:
        if any(k in lowered for k in keywords):
            return acct_type
    return None


classify_filename = make_classifier()


def parse_coa(csv_text: str) -> tuple[list[dict], list[str]]:
    accounts, errors = [], []
    for i, row in enumerate(sniff_reader(csv_text), start=2):
        name = field(row, "account name", "name", "account")
        if not name:
            continue
        raw_type = field(row, "account type", "type", "account group")
        acct_type = _map_type(raw_type)
        if not acct_type:
            errors.append(f"COA row {i}: unmapped Wave account type {raw_type!r}")
            continue
        accounts.append(
            {
                "code": field(row, "account id", "account code") or None,
                "name": name,
                "type": acct_type,
                "description": field(row, "description") or None,
            }
        )
    return accounts, errors


def parse_gl(csv_text: str) -> tuple[list[dict], list[str]]:
    rows, errors = [], []
    for i, row in enumerate(sniff_reader(csv_text), start=2):
        account = field(row, "account name", "account")
        date_raw = field(row, "transaction date", "date")
        if not account and not date_raw:
            continue
        try:
            debit = parse_amount(field(row, "debit amount", "debit"))
            credit = parse_amount(field(row, "credit amount", "credit"))
            if debit == 0 and credit == 0:
                # Single signed Amount column: positive = debit
                signed = parse_amount(field(row, "amount (one column)", "amount"))
                if signed > 0:
                    debit = signed
                elif signed < 0:
                    credit = -signed
            rows.append(
                {
                    "journal": field(row, "transaction id", "id"),
                    "date": parse_date(date_raw, _DATE_FORMATS),
                    "account": account,
                    "description": field(
                        row,
                        "transaction line description",
                        "transaction description",
                        "description",
                        "notes / memo",
                    ),
                    "reference": field(row, "transaction id", "id"),
                    "debit": debit,
                    "credit": credit,
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
        name = field(row, "account name", "account", "name")
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
    return run_import_bundle(db, bundle, _PARSERS, "wave_import", LABEL)
