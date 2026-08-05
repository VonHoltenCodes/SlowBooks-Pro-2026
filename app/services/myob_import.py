# ============================================================================
# MYOB import — chart of accounts + transaction journals, with dry-run.
#
# The MYOB DIALECT over the shared migration engine. Written against
# MYOB AccountRight's classic exports (tab-separated .TXT with
# dd/mm/yyyy dates — MYOB's home market is AU/NZ) and tolerant of the
# comma CSVs the cloud product produces:
#
#   - Accounts list: "Account Number" / "Account Name" / "Account Type",
#     with a Header column marking non-postable header accounts (skipped).
#   - Transaction/general journals: "ID No." groups the lines of one
#     journal; "Debit Amount" / "Credit Amount"; rows may carry the
#     account NUMBER only, so GL parsing resolves numbers through the
#     bundled chart before the engine's name-based matching runs.
#   - Trial balance: per-account Debit/Credit for verification.
#
# Same contract as the Xero importer: dry-run gates the import.
# ============================================================================

import logging
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.accounts import AccountType
from app.services.migration_common import (
    build_code_map,
    skip_report_preamble,
    dry_run_bundle,
    field,
    parse_amount,
    parse_date,
    run_import_bundle,
    sniff_reader,
    strip_code_suffix,
)

logger = logging.getLogger(__name__)

# dd/mm first — MYOB is an AU/NZ product; ISO accepted for hand-fixed files.
_DATE_FORMATS = ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d %b %Y")

_MYOB_TYPE_MAP = {
    "bank": AccountType.ASSET,
    "accounts receivable": AccountType.ASSET,
    "other current asset": AccountType.ASSET,
    "current asset": AccountType.ASSET,
    "fixed asset": AccountType.ASSET,
    "other asset": AccountType.ASSET,
    "asset": AccountType.ASSET,
    "credit card": AccountType.LIABILITY,
    "accounts payable": AccountType.LIABILITY,
    "other current liability": AccountType.LIABILITY,
    "current liability": AccountType.LIABILITY,
    "long term liability": AccountType.LIABILITY,
    "other liability": AccountType.LIABILITY,
    "liability": AccountType.LIABILITY,
    "equity": AccountType.EQUITY,
    "income": AccountType.INCOME,
    "other income": AccountType.INCOME,
    "cost of sales": AccountType.COGS,
    "expense": AccountType.EXPENSE,
    "other expense": AccountType.EXPENSE,
}

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


def _is_header_account(row: dict) -> bool:
    flag = field(row, "header", "h").lower()
    return flag in ("h", "header", "y", "yes", "true", "1")


def parse_coa(csv_text: str) -> tuple[list[dict], list[str]]:
    accounts, errors = [], []
    for i, row in enumerate(sniff_reader(csv_text), start=2):
        name = field(row, "account name", "name", "account")
        if not name:
            continue
        if _is_header_account(row):
            continue  # non-postable header rows structure the list only
        myob_type = field(row, "account type", "type").lower()
        acct_type = _MYOB_TYPE_MAP.get(myob_type)
        if not acct_type:
            errors.append(f"COA row {i}: unmapped MYOB account type {myob_type!r}")
            continue
        accounts.append(
            {
                "code": field(row, "account number", "account no.", "number", "code")
                or None,
                "name": name,
                "type": acct_type,
                "description": field(row, "description") or None,
            }
        )

    # MYOB allows the same name under different account numbers (Clearwater
    # ships two postable "Wages & Salaries"). Disambiguate with the code so
    # both import as distinct accounts; the engine strips the suffix when
    # cross-checking against the trial balance (which carries names only).
    seen: dict[str, int] = {}
    for spec in accounts:
        seen[spec["name"].lower()] = seen.get(spec["name"].lower(), 0) + 1
    for spec in accounts:
        if seen[spec["name"].lower()] > 1 and spec["code"]:
            spec["name"] = f"{spec['name']} ({spec['code']})"
    return accounts, errors


def _code_to_name_map(coa_text: str | None) -> dict[str, str]:
    if not coa_text:
        return {}
    accounts, _ = parse_coa(coa_text)
    return build_code_map(accounts)


def make_parse_gl(coa_text: str | None):
    """GL parser bound to the bundle's chart, so rows that carry only an
    account NUMBER resolve to a name before the engine's matching runs."""
    codes = _code_to_name_map(coa_text)

    def parse_gl(csv_text: str) -> tuple[list[dict], list[str]]:
        rows, errors = [], []
        for i, row in enumerate(sniff_reader(csv_text), start=2):
            name = field(row, "account name", "account")
            number = field(row, "account number", "account no.", "acct no.")
            date_raw = field(row, "date", "journal date")
            if not (name or number) and not date_raw:
                continue  # blank/summary row
            account = name
            if not account and number:
                account = codes.get(number.strip()) or codes.get(
                    number.strip().replace("-", "")
                )
                if not account:
                    errors.append(
                        f"GL row {i}: account number {number!r} not found in the "
                        f"chart of accounts export"
                    )
                    continue
            try:
                rows.append(
                    {
                        "journal": field(
                            row, "id no.", "id no", "id", "journal number"
                        ),
                        "date": parse_date(date_raw, _DATE_FORMATS),
                        "account": account,
                        "description": field(row, "memo", "description", "narration"),
                        "reference": field(row, "id no.", "id no", "reference"),
                        "debit": parse_amount(
                            field(row, "debit amount", "debit", "debit amt")
                        ),
                        "credit": parse_amount(
                            field(row, "credit amount", "credit", "credit amt")
                        ),
                    }
                )
            except ValueError as exc:
                errors.append(f"GL row {i}: {exc}")

        journals: dict = {}
        for row in rows:
            # MYOB reuses ID No. across transactions ("EP" for every
            # electronic payment; blank for many journals), so the number
            # alone under-groups. The transaction memo is repeated on
            # every line of a transaction, so (number, date, memo) is the
            # stable grouping key; same-day rows sharing all three (e.g.
            # one pay run's paycheques) merge into one balanced batch.
            key = f"{row['journal']}|{row['date'].isoformat()}|{row['description']}"
            journals.setdefault(key, []).append(row)
        return list(journals.values()), errors

    return parse_gl


def parse_tb(csv_text: str) -> tuple[dict, list[str]]:
    # MYOB's trial balance is a REPORT export: company address block,
    # title, period, and print date precede the table.
    csv_text = skip_report_preamble(csv_text)
    balances, errors = {}, []
    for i, row in enumerate(sniff_reader(csv_text), start=2):
        name = field(row, "account name", "account", "name")
        if not name or name.lower().rstrip(":").strip() in ("total", "grand total"):
            continue
        try:
            # Prefer YTD columns: the period Debit/Credit pair only covers
            # the report month, but the journals span the whole year.
            debit = parse_amount(field(row, "ytd debit", "debit", "debit amount"))
            credit = parse_amount(field(row, "ytd credit", "credit", "credit amount"))
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
    return dry_run_bundle(db, bundle, _parsers_for(bundle), "MYOB")


def run_import(db: Session, bundle: dict) -> dict:
    return run_import_bundle(db, bundle, _parsers_for(bundle), "myob_import", "MYOB")
