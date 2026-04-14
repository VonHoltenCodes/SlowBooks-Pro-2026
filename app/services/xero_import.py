from __future__ import annotations

import csv
import io
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.accounts import Account, AccountType
from app.models.transactions import TransactionLine
from app.services.accounting import create_journal_entry

REQUIRED_FILE_TYPES = [
    'chart_of_accounts',
    'general_ledger',
    'trial_balance',
    'profit_and_loss',
    'balance_sheet',
]

FILE_KEYWORDS = {
    'chart_of_accounts': ('chart', 'accounts'),
    'general_ledger': ('general', 'ledger'),
    'trial_balance': ('trial', 'balance'),
    'profit_and_loss': ('profit', 'loss'),
    'balance_sheet': ('balance', 'sheet'),
}

HEADER_ALIASES = {
    'code': {'code', 'account code', 'accountcode'},
    'name': {'name', 'account name', 'accountname'},
    'type': {'type', 'account type', 'accounttype', 'class'},
    'status': {'status'},
    'date': {'date', 'journal date'},
    'source': {'source', 'journal source', 'journal'},
    'reference': {'reference', 'ref', 'journal reference'},
    'description': {'description', 'memo', 'narration'},
    'account_code': {'account code', 'code', 'accountcode'},
    'account_name': {'account name', 'name', 'accountname'},
    'debit': {'debit', 'debits'},
    'credit': {'credit', 'credits'},
    'balance': {'balance', 'closing balance'},
    'label': {'name', 'item', 'title', 'account', 'description'},
    'amount': {'amount', 'total', 'this year', 'value'},
}


@dataclass(frozen=True)
class NormalizedAccount:
    code: str
    name: str
    account_type: AccountType
    is_active: bool


@dataclass(frozen=True)
class NormalizedJournalLine:
    journal_key: str
    txn_date: datetime.date
    reference: str
    description: str
    source: str
    account_code: str
    account_name: str
    debit: Decimal
    credit: Decimal


@dataclass(frozen=True)
class ParsedReportTotals:
    net_income: Decimal | None = None
    total_assets: Decimal | None = None
    total_liabilities: Decimal | None = None
    total_equity: Decimal | None = None


def _normalize_header(value: str) -> str:
    return ''.join(ch.lower() for ch in str(value or '') if ch.isalnum() or ch.isspace()).strip()


def _pick_key(fieldnames: list[str], alias_key: str) -> str | None:
    normalized = {_normalize_header(name): name for name in fieldnames or []}
    for alias in HEADER_ALIASES[alias_key]:
        key = normalized.get(_normalize_header(alias))
        if key:
            return key
    return None


def _parse_decimal(value) -> Decimal:
    if value in (None, ''):
        return Decimal('0.00')
    text = str(value).replace(',', '').replace('$', '').strip()
    if text in {'', '-'}:
        return Decimal('0.00')
    try:
        return Decimal(text)
    except InvalidOperation:
        return Decimal('0.00')


def _parse_date(value: str):
    value = str(value or '').strip()
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%m/%d/%Y'):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValueError(f'Unsupported date format: {value}')


def detect_file_type(filename: str) -> str | None:
    lower = filename.lower()
    for file_type, keywords in FILE_KEYWORDS.items():
        if all(keyword in lower for keyword in keywords):
            return file_type
    return None


def parse_csv_text(content: str) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(content))
    return list(reader)


def map_xero_account_type(value: str) -> AccountType:
    normalized = str(value or '').strip().lower()
    if normalized in {'bank', 'current asset', 'fixed asset', 'asset', 'accounts receivable'}:
        return AccountType.ASSET
    if normalized in {'current liability', 'liability', 'accounts payable', 'non-current liability'}:
        return AccountType.LIABILITY
    if normalized in {'equity'}:
        return AccountType.EQUITY
    if normalized in {'revenue', 'sales', 'income', 'other income'}:
        return AccountType.INCOME
    if normalized in {'direct costs', 'cost of goods sold', 'cogs'}:
        return AccountType.COGS
    return AccountType.EXPENSE


def parse_chart_of_accounts(rows: list[dict[str, str]]) -> list[NormalizedAccount]:
    if not rows:
        return []
    fieldnames = list(rows[0].keys())
    code_key = _pick_key(fieldnames, 'code')
    name_key = _pick_key(fieldnames, 'name')
    type_key = _pick_key(fieldnames, 'type')
    status_key = _pick_key(fieldnames, 'status')
    if not all([code_key, name_key, type_key]):
        raise ValueError('Chart of Accounts file is missing required columns')
    result = []
    for row in rows:
        code = str(row.get(code_key) or '').strip()
        name = str(row.get(name_key) or '').strip()
        if not code or not name:
            continue
        is_active = str(row.get(status_key) or 'active').strip().lower() not in {'archived', 'inactive'}
        result.append(NormalizedAccount(code=code, name=name, account_type=map_xero_account_type(row.get(type_key)), is_active=is_active))
    return result


def parse_general_ledger(rows: list[dict[str, str]]) -> list[NormalizedJournalLine]:
    if not rows:
        return []
    fieldnames = list(rows[0].keys())
    date_key = _pick_key(fieldnames, 'date')
    account_code_key = _pick_key(fieldnames, 'account_code')
    account_name_key = _pick_key(fieldnames, 'account_name')
    debit_key = _pick_key(fieldnames, 'debit')
    credit_key = _pick_key(fieldnames, 'credit')
    source_key = _pick_key(fieldnames, 'source')
    reference_key = _pick_key(fieldnames, 'reference')
    description_key = _pick_key(fieldnames, 'description')
    if not all([date_key, account_code_key, debit_key, credit_key]):
        raise ValueError('General Ledger file is missing required columns')
    lines = []
    for idx, row in enumerate(rows, start=1):
        account_code = str(row.get(account_code_key) or '').strip()
        if not account_code:
            continue
        txn_date = _parse_date(row.get(date_key))
        source = str(row.get(source_key) or '').strip()
        reference = str(row.get(reference_key) or '').strip()
        description = str(row.get(description_key) or '').strip()
        journal_key = '|'.join([
            txn_date.isoformat(),
            source,
            reference,
            description,
            str(idx),
        ]) if not source and not reference and not description else '|'.join([txn_date.isoformat(), source, reference, description])
        lines.append(NormalizedJournalLine(
            journal_key=journal_key,
            txn_date=txn_date,
            reference=reference,
            description=description,
            source=source,
            account_code=account_code,
            account_name=str(row.get(account_name_key) or '').strip(),
            debit=_parse_decimal(row.get(debit_key)),
            credit=_parse_decimal(row.get(credit_key)),
        ))
    return lines


def simulate_balances(accounts: list[NormalizedAccount], gl_lines: list[NormalizedJournalLine]):
    account_types = {account.code: account.account_type for account in accounts}
    balances = defaultdict(lambda: Decimal('0.00'))
    for line in gl_lines:
        acct_type = account_types.get(line.account_code)
        if acct_type in {AccountType.ASSET, AccountType.EXPENSE, AccountType.COGS}:
            balances[line.account_code] += line.debit - line.credit
        else:
            balances[line.account_code] += line.credit - line.debit
    total_income = sum(balance for code, balance in balances.items() if account_types.get(code) == AccountType.INCOME)
    total_cogs = sum(balance for code, balance in balances.items() if account_types.get(code) == AccountType.COGS)
    total_expenses = sum(balance for code, balance in balances.items() if account_types.get(code) == AccountType.EXPENSE)
    total_assets = sum(balance for code, balance in balances.items() if account_types.get(code) == AccountType.ASSET)
    total_liabilities = sum(balance for code, balance in balances.items() if account_types.get(code) == AccountType.LIABILITY)
    total_equity = sum(balance for code, balance in balances.items() if account_types.get(code) == AccountType.EQUITY)
    return {
        'balances': balances,
        'net_income': total_income - total_cogs - total_expenses,
        'total_assets': total_assets,
        'total_liabilities': total_liabilities,
        'total_equity': total_equity + (total_income - total_cogs - total_expenses),
    }


def parse_trial_balance(rows: list[dict[str, str]], accounts: list[NormalizedAccount]) -> dict[str, Decimal]:
    if not rows:
        return {}
    fieldnames = list(rows[0].keys())
    code_key = _pick_key(fieldnames, 'code') or _pick_key(fieldnames, 'account_code')
    balance_key = _pick_key(fieldnames, 'balance')
    debit_key = _pick_key(fieldnames, 'debit')
    credit_key = _pick_key(fieldnames, 'credit')
    account_types = {account.code: account.account_type for account in accounts}
    results = {}
    for row in rows:
        code = str(row.get(code_key) or '').strip() if code_key else ''
        if not code:
            continue
        if balance_key:
            results[code] = _parse_decimal(row.get(balance_key))
        else:
            debit = _parse_decimal(row.get(debit_key))
            credit = _parse_decimal(row.get(credit_key))
            acct_type = account_types.get(code)
            if acct_type in {AccountType.ASSET, AccountType.EXPENSE, AccountType.COGS}:
                results[code] = debit - credit
            else:
                results[code] = credit - debit
    return results


def parse_report_totals(rows: list[dict[str, str]]) -> ParsedReportTotals:
    totals = {}
    if not rows:
        return ParsedReportTotals()
    for row in rows:
        values = list(row.values())
        if not values:
            continue
        label = str(values[0] or '').strip().lower()
        amount = None
        for value in values[1:]:
            if str(value).strip():
                amount = _parse_decimal(value)
                break
        if amount is None:
            continue
        totals[label] = amount
    return ParsedReportTotals(
        net_income=totals.get('net profit') or totals.get('net profit (loss)') or totals.get('net income'),
        total_assets=totals.get('total assets'),
        total_liabilities=totals.get('total liabilities'),
        total_equity=totals.get('total equity'),
    )


def dry_run_import(file_map: dict[str, tuple[str, str]]) -> dict:
    missing = [file_type for file_type in REQUIRED_FILE_TYPES if file_type not in file_map]
    if missing:
        return {
            'required_files': REQUIRED_FILE_TYPES,
            'detected_files': {key: value[0] for key, value in file_map.items()},
            'missing_files': missing,
            'counts': {},
            'journal_groups': 0,
            'import_ready': False,
            'errors': [f'Missing required files: {", ".join(missing)}'],
            'warnings': [],
            'verification': {
                'trial_balance_ok': False,
                'trial_balance_mismatches': [],
                'profit_loss_ok': False,
                'profit_loss_mismatches': [],
                'balance_sheet_ok': False,
                'balance_sheet_mismatches': [],
            },
        }

    coa = parse_chart_of_accounts(parse_csv_text(file_map['chart_of_accounts'][1]))
    gl = parse_general_ledger(parse_csv_text(file_map['general_ledger'][1]))
    simulated = simulate_balances(coa, gl)
    tb = parse_trial_balance(parse_csv_text(file_map['trial_balance'][1]), coa)
    pnl = parse_report_totals(parse_csv_text(file_map['profit_and_loss'][1]))
    bs = parse_report_totals(parse_csv_text(file_map['balance_sheet'][1]))

    trial_balance_mismatches = []
    for code, expected in tb.items():
        actual = simulated['balances'].get(code, Decimal('0.00'))
        if abs(actual - expected) > Decimal('0.01'):
            trial_balance_mismatches.append(f'{code}: expected {expected}, got {actual}')

    profit_loss_mismatches = []
    if pnl.net_income is not None and abs(simulated['net_income'] - pnl.net_income) > Decimal('0.01'):
        profit_loss_mismatches.append(f'Net income: expected {pnl.net_income}, got {simulated["net_income"]}')

    balance_sheet_mismatches = []
    if bs.total_assets is not None and abs(simulated['total_assets'] - bs.total_assets) > Decimal('0.01'):
        balance_sheet_mismatches.append(f'Total assets: expected {bs.total_assets}, got {simulated["total_assets"]}')
    if bs.total_liabilities is not None and abs(simulated['total_liabilities'] - bs.total_liabilities) > Decimal('0.01'):
        balance_sheet_mismatches.append(f'Total liabilities: expected {bs.total_liabilities}, got {simulated["total_liabilities"]}')
    if bs.total_equity is not None and abs(simulated['total_equity'] - bs.total_equity) > Decimal('0.01'):
        balance_sheet_mismatches.append(f'Total equity: expected {bs.total_equity}, got {simulated["total_equity"]}')

    journal_groups = len({line.journal_key for line in gl})
    errors = []
    grouped = defaultdict(list)
    for line in gl:
        grouped[line.journal_key].append(line)
    for key, lines in grouped.items():
        debit = sum(line.debit for line in lines)
        credit = sum(line.credit for line in lines)
        if abs(debit - credit) > Decimal('0.01'):
            errors.append(f'Journal group {key} is unbalanced ({debit} vs {credit})')
    verification = {
        'trial_balance_ok': not trial_balance_mismatches,
        'trial_balance_mismatches': trial_balance_mismatches,
        'profit_loss_ok': not profit_loss_mismatches,
        'profit_loss_mismatches': profit_loss_mismatches,
        'balance_sheet_ok': not balance_sheet_mismatches,
        'balance_sheet_mismatches': balance_sheet_mismatches,
    }
    return {
        'required_files': REQUIRED_FILE_TYPES,
        'detected_files': {key: value[0] for key, value in file_map.items()},
        'missing_files': [],
        'counts': {
            'accounts': len(coa),
            'journal_lines': len(gl),
            'trial_balance_rows': len(tb),
        },
        'journal_groups': journal_groups,
        'import_ready': not errors and verification['trial_balance_ok'] and verification['profit_loss_ok'] and verification['balance_sheet_ok'],
        'errors': errors,
        'warnings': [],
        'verification': verification,
    }


def execute_import(db: Session, file_map: dict[str, tuple[str, str]]) -> dict:
    summary = dry_run_import(file_map)
    if not summary['import_ready']:
        raise HTTPException(status_code=400, detail='Xero import dry-run did not pass verification')

    coa = parse_chart_of_accounts(parse_csv_text(file_map['chart_of_accounts'][1]))
    gl = parse_general_ledger(parse_csv_text(file_map['general_ledger'][1]))
    if db.query(TransactionLine.id).join(Account).filter(Account.account_number.in_([account.code for account in coa])).first():
        raise HTTPException(status_code=400, detail='Xero import expects a clean target ledger for the imported chart/journals')

    account_by_code = {}
    imported_accounts = 0
    for account in coa:
        existing = db.query(Account).filter(Account.account_number == account.code).first()
        if existing:
            existing.name = account.name
            existing.account_type = account.account_type
            existing.is_active = account.is_active
            account_by_code[account.code] = existing
        else:
            row = Account(name=account.name, account_number=account.code, account_type=account.account_type, is_active=account.is_active)
            db.add(row)
            db.flush()
            account_by_code[account.code] = row
            imported_accounts += 1

    grouped = defaultdict(list)
    for line in gl:
        grouped[line.journal_key].append(line)

    imported_transactions = 0
    imported_lines = 0
    for key, lines in grouped.items():
        journal_lines = []
        for line in lines:
            account = account_by_code.get(line.account_code)
            if not account:
                raise HTTPException(status_code=400, detail=f'Missing mapped account for code {line.account_code}')
            journal_lines.append({
                'account_id': account.id,
                'debit': line.debit,
                'credit': line.credit,
                'description': line.description or line.account_name,
            })
            imported_lines += 1
        txn = create_journal_entry(
            db,
            lines[0].txn_date,
            lines[0].description or lines[0].source or 'Xero GL import',
            journal_lines,
            source_type='xero_import',
            reference=lines[0].reference or lines[0].source,
        )
        imported_transactions += 1
    db.commit()
    return {
        'imported_accounts': imported_accounts,
        'imported_transactions': imported_transactions,
        'imported_transaction_lines': imported_lines,
        'verification': summary['verification'],
    }
