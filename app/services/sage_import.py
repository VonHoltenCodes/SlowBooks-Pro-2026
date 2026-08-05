# ============================================================================
# Sage 50 (US / Peachtree lineage) import dialect.
#
#   - Chart: "Account ID" / "Account Description" / "Account Type" —
#     Sage's types are descriptive strings ("Equity-gets closed",
#     "Accumulated Depreciation", ...).
#   - General Ledger report: "Date", "Reference", "Jrnl" (journal code),
#     "Account ID", "Trans Description", "Debit Amt" / "Credit Amt".
#     Rows carry the ACCOUNT ID, resolved through the bundled chart.
#     Journals group by (Jrnl, Reference, Date).
#   - Trial Balance: "Account ID" / "Account Description" + Debit/Credit.
#   - Dates are US-style mm/dd/yyyy.
# ============================================================================

import logging
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.accounts import AccountType
from app.services.migration_common import (
    build_code_map,
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

LABEL = "Sage 50"

_DATE_FORMATS = ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d")

_SAGE_TYPE_MAP = {
    "cash": AccountType.ASSET,
    "accounts receivable": AccountType.ASSET,
    "inventory": AccountType.ASSET,
    "other current assets": AccountType.ASSET,
    "other current asset": AccountType.ASSET,
    "fixed assets": AccountType.ASSET,
    "fixed asset": AccountType.ASSET,
    "accumulated depreciation": AccountType.ASSET,  # contra-asset stays asset-typed
    "other assets": AccountType.ASSET,
    "other asset": AccountType.ASSET,
    "accounts payable": AccountType.LIABILITY,
    "other current liabilities": AccountType.LIABILITY,
    "other current liability": AccountType.LIABILITY,
    "long term liabilities": AccountType.LIABILITY,
    "long term liability": AccountType.LIABILITY,
    "equity-doesn't close": AccountType.EQUITY,
    "equity-doesnt close": AccountType.EQUITY,
    "equity-gets closed": AccountType.EQUITY,
    "equity-retained earnings": AccountType.EQUITY,
    "equity": AccountType.EQUITY,
    "income": AccountType.INCOME,
    "other income": AccountType.INCOME,
    "cost of sales": AccountType.COGS,
    "expenses": AccountType.EXPENSE,
    "expense": AccountType.EXPENSE,
    "other expense": AccountType.EXPENSE,
}

classify_filename = make_classifier()


def parse_coa(csv_text: str) -> tuple[list[dict], list[str]]:
    accounts, errors = [], []
    for i, row in enumerate(sniff_reader(csv_text), start=2):
        name = field(row, "account description", "description", "account name")
        if not name:
            continue
        sage_type = field(row, "account type", "type").lower()
        acct_type = _SAGE_TYPE_MAP.get(sage_type)
        if not acct_type:
            errors.append(f"COA row {i}: unmapped Sage account type {sage_type!r}")
            continue
        accounts.append(
            {
                "code": field(row, "account id", "account no.", "account number")
                or None,
                "name": name,
                "type": acct_type,
                "description": None,
            }
        )
    return accounts, errors


def make_parse_gl(coa_text: str | None):
    accounts, _ = parse_coa(coa_text) if coa_text else ([], [])
    codes = build_code_map(accounts)

    def parse_gl(csv_text: str) -> tuple[list[dict], list[str]]:
        rows, errors = [], []
        for i, row in enumerate(sniff_reader(csv_text), start=2):
            account_id = field(row, "account id", "account no.", "account number")
            date_raw = field(row, "date", "trans date")
            if not account_id and not date_raw:
                continue
            account = field(row, "account description", "account name")
            if not account and account_id:
                account = codes.get(account_id.strip()) or codes.get(
                    account_id.strip().replace("-", "")
                )
                if not account:
                    errors.append(
                        f"GL row {i}: account ID {account_id!r} not found in the "
                        f"chart of accounts export"
                    )
                    continue
            try:
                jrnl = field(row, "jrnl", "journal")
                reference = field(row, "reference", "ref")
                parsed_date = parse_date(date_raw, _DATE_FORMATS)
                rows.append(
                    {
                        "journal": (
                            f"{jrnl}-{reference}-{parsed_date.isoformat()}"
                            if (jrnl or reference)
                            else ""
                        ),
                        "date": parsed_date,
                        "account": account,
                        "description": field(
                            row, "trans description", "description", "memo"
                        ),
                        "reference": reference,
                        "debit": parse_amount(
                            field(row, "debit amt", "debit amount", "debit")
                        ),
                        "credit": parse_amount(
                            field(row, "credit amt", "credit amount", "credit")
                        ),
                    }
                )
            except ValueError as exc:
                errors.append(f"GL row {i}: {exc}")

        journals: dict = {}
        for row in rows:
            key = row["journal"] or f"{row['date'].isoformat()}|{row['reference']}"
            journals.setdefault(key, []).append(row)
        return list(journals.values()), errors

    return parse_gl


def parse_tb(csv_text: str) -> tuple[dict, list[str]]:
    balances, errors = {}, []
    for i, row in enumerate(sniff_reader(csv_text), start=2):
        name = field(row, "account description", "account name", "account")
        if not name:
            continue
        try:
            debit = parse_amount(field(row, "debit amt", "debit amount", "debit"))
            credit = parse_amount(field(row, "credit amt", "credit amount", "credit"))
        except ValueError as exc:
            errors.append(f"TB row {i}: {exc}")
            continue
        key = strip_code_suffix(name)
        balances[key] = balances.get(key, Decimal("0")) + debit - credit
    return balances, errors


def _parsers_for(bundle: dict) -> dict:
    return {
        "coa": parse_coa,
        "gl": make_parse_gl(bundle.get("coa")),
        "tb": parse_tb,
    }


def dry_run(db: Session, bundle: dict) -> dict:
    return dry_run_bundle(db, bundle, _parsers_for(bundle), LABEL)


def run_import(db: Session, bundle: dict) -> dict:
    return run_import_bundle(db, bundle, _parsers_for(bundle), "sage_import", LABEL)
