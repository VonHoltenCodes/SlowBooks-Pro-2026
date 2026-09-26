"""Import QBO's posted General Ledger activity as balanced local journals.

QBO's Account resource supplies balances, not their history. The General
Ledger report includes postings from purchases, deposits, transfers, bills,
invoices, payments, and journal entries in one source. We import the postings
once and leave the separately imported business documents as document records.
"""

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.accounts import Account, AccountType
from app.models.qbo_mapping import QBOMapping
from app.models.transactions import Transaction
from app.services.accounting import create_journal_entry
from app.services.closing_date import check_closing_date
from app.services.qbo_common import (
    apply_rollup_repair,
    get_mapping_by_qbo_id,
    is_journal_entry_type,
    journal_posting_matches,
    legacy_rollup_repair,
    posting_mismatch,
    rebase_account_balances,
)
from app.services.qbo_service import get_qbo_client
from app.services.safe_errors import DataProblem
from app.services import qbo_progress

_DEBIT_NORMAL = {AccountType.ASSET, AccountType.EXPENSE, AccountType.COGS}


def _periods(start: date, end: date):
    """Request historical data in years, then split busy years as needed."""
    cursor = start
    if cursor.year < 2000:
        last = min(end, date(1999, 12, 31))
        yield cursor, last
        cursor = date(2000, 1, 1)
    while cursor <= end:
        last = min(end, date(cursor.year, 12, 31))
        yield cursor, last
        cursor = date(cursor.year + 1, 1, 1)


def _report_periods(client, first: date, last: date):
    """Reports cannot be paged. Split large periods before accepting them."""
    qbo_progress.emit(
        "query", f"Fetching General Ledger: {first} to {last}", item_id=""
    )
    report = client.get_report(
        "GeneralLedger",
        qs={
            "start_date": first.isoformat(),
            "end_date": last.isoformat(),
            "accounting_method": "Accrual",
        },
    )
    header = report.get("Header", {})
    if (
        header.get("StartPeriod") != first.isoformat()
        or header.get("EndPeriod") != last.isoformat()
    ):
        raise DataProblem("QBO General Ledger returned a different date range")
    rows = list(_rows(report))
    qbo_progress.emit(
        "fetch",
        f"Fetched {len(rows)} ledger rows for {first} to {last}",
        fetched=len(rows),
        item_id="",
    )
    if len(rows) >= 500:
        if first == last:
            raise DataProblem(
                f"QBO General Ledger has too many rows on {first}; "
                "the report may be truncated"
            )
        middle = first + timedelta(days=(last - first).days // 2)
        yield from _report_periods(client, first, middle)
        yield from _report_periods(client, middle + timedelta(days=1), last)
    else:
        yield first, last, rows


def _money(value) -> Decimal:
    text = str(value or "").strip().replace(",", "")
    if not text:
        return Decimal("0")
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    try:
        amount = Decimal(text)
    except InvalidOperation as exc:
        raise DataProblem("QBO General Ledger contains an unreadable amount") from exc
    if not amount.is_finite() or amount != amount.quantize(Decimal("0.01")):
        raise DataProblem("QBO General Ledger contains an amount beyond cents")
    return amount


def _rows(report: dict):
    """Yield (QBO account ID, report cells) through account sections."""
    columns = [
        col.get("ColTitle") for col in report.get("Columns", {}).get("Column", [])
    ]
    expected = [
        "Date",
        "Transaction Type",
        "Num",
        "Name",
        "Memo/Description",
        "Split",
        "Amount",
        "Balance",
    ]
    if columns != expected:
        raise DataProblem(
            "QBO General Ledger columns changed; no activity was imported"
        )
    if str(report.get("Header", {}).get("ReportBasis", "")).lower() != "accrual":
        raise DataProblem("QBO General Ledger was not returned on the accrual basis")

    def walk(container, account_id=None):
        for row in container.get("Row", []):
            if row.get("type") == "Section":
                header = row.get("Header", {}).get("ColData", [])
                nested_account = (header[0].get("id") if header else None) or account_id
                yield from walk(row.get("Rows", {}), nested_account)
            elif row.get("type") == "Data":
                cells = row.get("ColData", [])
                if len(cells) != len(expected):
                    raise DataProblem("QBO General Ledger has an unexpected row shape")
                yield account_id, cells

    yield from walk(report.get("Rows", {}))


def _source_key(txn_type: str, qbo_id: str) -> str:
    key = f"{txn_type}:{qbo_id}"
    if len(key) > 100:
        raise DataProblem("QBO transaction ID is too long to track safely")
    return key


def _fingerprint(entry: dict) -> str:
    payload = {
        "date": entry["date"].isoformat(),
        "type": entry["type"],
        "number": entry["number"],
        "lines": sorted(
            (line["account_id"], str(line["debit"]), str(line["credit"]))
            for line in entry["lines"]
        ),
    }
    return sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:50]


def _account_map(db: Session) -> dict[str, Account]:
    mapped = defaultdict(set)
    for qbo_id, account_id in (
        db.query(QBOMapping.qbo_id, QBOMapping.slowbooks_id)
        .filter(QBOMapping.entity_type == "account")
        .all()
    ):
        mapped[qbo_id].add(account_id)
    ambiguous = [qbo_id for qbo_id, ids in mapped.items() if len(ids) != 1]
    if ambiguous:
        raise DataProblem(
            "Ambiguous QBO account mappings: "
            + "; ".join(
                f"account QBO #{qbo_id} maps to local IDs {', '.join(str(n) for n in sorted(mapped[qbo_id]))}"
                for qbo_id in sorted(ambiguous)
            )
        )
    accounts = {
        account.id: account
        for account in db.query(Account).filter(
            Account.id.in_([next(iter(ids)) for ids in mapped.values()])
        )
    }
    return {qbo_id: accounts.get(next(iter(ids))) for qbo_id, ids in mapped.items()}


@qbo_progress.stage("ledger")
def import_ledger(
    db: Session,
    *,
    start: date = date(1900, 1, 1),
    end: date | None = None,
    dry_run: bool = False,
) -> dict:
    """Import all report postings atomically; return blockers before writing.

    Historical QBO documents already imported as invoices/payments are not
    posted by that importer. This path posts their report lines as journals,
    preserving QBO's financial history without inventing editable documents.
    """
    end = end or date.today()
    if start > end:
        raise DataProblem("QBO General Ledger date range is invalid")
    client = get_qbo_client(db)
    accounts = _account_map(db)
    entries = {}
    missing_accounts = defaultdict(set)
    errors = []

    try:
        for broad_first, broad_last in _periods(start, end):
            for first, last, rows in _report_periods(client, broad_first, broad_last):
                for qbo_account_id, cells in rows:
                    txn_type = str(cells[1].get("value") or "").strip()
                    qbo_id = str(cells[1].get("id") or "").strip()
                    context = f"{txn_type or '(missing type)'} QBO #{qbo_id or '(missing ID)'}, document {cells[2].get('value') or '(missing number)'}, account QBO #{qbo_account_id or '(missing ID)'}"
                    try:
                        amount = _money(cells[6].get("value"))
                    except DataProblem as exc:
                        raise DataProblem(
                            f"{context}: {exc.user_text}; amount={cells[6].get('value')!r}"
                        ) from exc
                    if amount == 0:
                        continue
                    if not qbo_account_id or not txn_type or not qbo_id:
                        raise DataProblem(
                            f"{context}: General Ledger posting is missing an account or transaction ID"
                        )
                    key = _source_key(txn_type, qbo_id)
                    account = accounts.get(str(qbo_account_id))
                    if account is None:
                        missing_accounts[(key, str(cells[2].get("value") or ""))].add(
                            str(qbo_account_id)
                        )
                        continue
                    try:
                        txn_date = date.fromisoformat(str(cells[0].get("value")))
                    except ValueError as exc:
                        raise DataProblem(
                            f"{context}: invalid transaction date {cells[0].get('value')!r}"
                        ) from exc
                    if not first <= txn_date <= last:
                        raise DataProblem(
                            f"{context}: posting date {txn_date} is outside the requested period {first} to {last}"
                        )
                    entry = entries.setdefault(
                        key,
                        {
                            "date": txn_date,
                            "type": txn_type,
                            "number": str(cells[2].get("value") or "").strip(),
                            "name": str(cells[3].get("value") or "").strip(),
                            "memo": str(cells[4].get("value") or "").strip(),
                            "lines": [],
                        },
                    )
                    if entry["date"] != txn_date:
                        raise DataProblem(
                            f"{context}: transaction ID appears on different dates, {entry['date']} and {txn_date}"
                        )
                    signed = (
                        amount if account.account_type in _DEBIT_NORMAL else -amount
                    )
                    entry["lines"].append(
                        {
                            "account_id": account.id,
                            "debit": max(signed, Decimal("0")),
                            "credit": max(-signed, Decimal("0")),
                        }
                    )
    except DataProblem as exc:
        return {
            "imported": 0,
            "errors": [{"entity": "ledger", "message": exc.user_text}],
        }

    for (key, number), account_ids in missing_accounts.items():
        qbo_progress.append_error(
            errors,
            {
                "entity": "ledger",
                "qbo_id": key,
                "document_number": number,
                "message": (
                    f"Ledger QBO #{key}, document {number or '(missing number)'}: account QBO IDs "
                    f"{', '.join(sorted(account_ids))} have no local mapping; import all QBO Accounts before ledger activity"
                ),
            },
        )

    tracked = {
        mapping.qbo_id: mapping
        for mapping in db.query(QBOMapping)
        .filter(QBOMapping.entity_type == "ledger")
        .all()
    }
    pending = []
    repairs = []
    token_updates = []
    for key, entry in entries.items():
        qbo_progress.item(key, entry["number"])
        if (key, entry["number"]) in missing_accounts:
            continue
        debits = sum((line["debit"] for line in entry["lines"]), Decimal("0"))
        credits = sum((line["credit"] for line in entry["lines"]), Decimal("0"))
        if debits != credits or debits <= 0:
            qbo_progress.append_error(
                errors,
                {
                    "entity": "ledger",
                    "qbo_id": key,
                    "message": f"QBO posting #{key}, document {entry['number'] or '(missing number)'}, date {entry['date']} does not balance: debit {debits:.2f}, credit {credits:.2f}, difference {debits - credits:.2f}; local account IDs {', '.join(str(line['account_id']) for line in entry['lines'])}",
                },
            )
            continue
        if is_journal_entry_type(entry["type"]):
            journal_map = get_mapping_by_qbo_id(
                db, "journal_entry", key.partition(":")[2]
            )
            if journal_map:
                txn = db.get(Transaction, journal_map.slowbooks_id)
                if not journal_posting_matches(txn, entry["date"], entry["lines"]):
                    qbo_progress.append_error(
                        errors,
                        {
                            "entity": "ledger",
                            "qbo_id": key,
                            "code": "IMPORT_POSTING_MISMATCH",
                            "message": str(
                                posting_mismatch(
                                    txn,
                                    journal_map.slowbooks_id,
                                    entry["date"],
                                    entry["lines"],
                                    accounts,
                                )
                            ),
                        },
                    )
                else:
                    qbo_progress.skipped(
                        "Already imported through the JournalEntry API"
                    )
                continue
        fingerprint = _fingerprint(entry)
        existing = tracked.get(key)
        if existing:
            txn = db.get(Transaction, existing.slowbooks_id)
            if not journal_posting_matches(txn, entry["date"], entry["lines"]):
                repair = legacy_rollup_repair(
                    txn, entry["date"], entry["lines"], accounts
                )
                if repair:
                    try:
                        check_closing_date(db, entry["date"])
                    except HTTPException as exc:
                        qbo_progress.append_error(
                            errors,
                            {
                                "entity": "ledger",
                                "qbo_id": key,
                                "message": f"Local transaction #{txn.id}: {exc.detail}",
                            },
                        )
                        continue
                    repairs.append((key, entry, existing, fingerprint, repair))
                    qbo_progress.validated(key)
                    continue
                qbo_progress.append_error(
                    errors,
                    {
                        "entity": "ledger",
                        "qbo_id": key,
                        "code": "IMPORT_POSTING_MISMATCH",
                        "message": str(
                            posting_mismatch(
                                txn,
                                existing.slowbooks_id,
                                entry["date"],
                                entry["lines"],
                                accounts,
                            )
                        ),
                    },
                )
            else:
                token_updates.append((existing, fingerprint))
                qbo_progress.skipped()
            continue
        try:
            check_closing_date(db, entry["date"])
        except HTTPException as exc:
            qbo_progress.append_error(
                errors, {"entity": "ledger", "qbo_id": key, "message": str(exc.detail)}
            )
            continue
        pending.append((key, entry, fingerprint))
        qbo_progress.validated(key)

    if errors:
        qbo_progress.emit(
            "block",
            f"Ledger batch not posted: {len(errors)} validation error(s); "
            f"{len(pending)} new posting(s) and {len(repairs)} repair(s) waiting.",
            level="warning",
            code="IMPORT_BATCH_BLOCKED",
            item_id="",
        )
        return {"imported": 0, "errors": errors}

    if dry_run:
        return {"imported": 0, "ready": len(pending) + len(repairs), "errors": []}

    key = ""
    try:
        with db.begin_nested():
            for key, entry, mapping, fingerprint, repair in repairs:
                qbo_progress.posting(key, entry["number"])
                apply_rollup_repair(
                    db.get(Transaction, mapping.slowbooks_id), repair, accounts
                )
                mapping.qbo_sync_token = fingerprint
            db.flush()
            # Account import seeded QBO's *current* balance. Rebase the local
            # cached balance on actual local postings before adding history,
            # otherwise importing the history would count it twice.
            rebase_account_balances(db, accounts.values())
            for mapping, fingerprint in token_updates:
                mapping.qbo_sync_token = fingerprint
            for key, entry, fingerprint in sorted(
                pending, key=lambda row: (row[1]["date"], row[0])
            ):
                qbo_progress.posting(key, entry["number"])
                parts = [entry["type"], entry["name"], entry["memo"]]
                description = "QBO " + " — ".join(part for part in parts if part)
                txn = create_journal_entry(
                    db,
                    entry["date"],
                    description,
                    entry["lines"],
                    source_type=(
                        "qbo_journal"
                        if is_journal_entry_type(entry["type"])
                        else "qbo_ledger"
                    ),
                    reference=(entry["number"] or key)[:100],
                )
                db.add(
                    QBOMapping(
                        entity_type="ledger",
                        slowbooks_id=txn.id,
                        qbo_id=key,
                        qbo_sync_token=fingerprint,
                    )
                )
                qbo_progress.created(key)
            db.flush()
    except Exception as exc:
        return {
            "imported": 0,
            "errors": [
                {
                    "entity": "ledger",
                    "qbo_id": key,
                    "message": f"Ledger QBO #{key or '(batch)'}: "
                    + qbo_progress.error_message(exc, "QBO ledger import"),
                }
            ],
        }
    return {"imported": len(pending), "errors": []}
