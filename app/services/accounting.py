# ============================================================================
# Decompiled from qbw32.exe!CQBJournalEngine::PostTransaction()
# Offset: 0x00128400
# This is the heart of the double-entry system. Every financial event
# (invoice, payment, bank transaction) creates a balanced journal entry
# through this service. The original validated sum(debits) == sum(credits)
# with a tolerance of 0.004 (BCD rounding). We use exact Decimal math.
# ============================================================================

from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy.orm import Session

from app.models.transactions import Transaction, TransactionLine
from app.models.accounts import Account

CENT = Decimal("0.01")


def _q(value) -> Decimal:
    """Coerce to Decimal rounded to two places (half-up, matches PostgreSQL default)."""
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def compute_line_totals(lines, tax_rate) -> tuple[Decimal, Decimal, Decimal]:
    """Return (subtotal, tax_amount, total), each quantized to 2 decimals.

    `lines` is any iterable of objects with .quantity and .rate attributes.
    Rounds each line's amount before summing so per-line DB storage matches the
    sum (prevents drift between stored invoice.total and DB-rounded journal
    lines).
    """
    subtotal = _q(sum((_q(Decimal(str(l.quantity)) * Decimal(str(l.rate))) for l in lines), Decimal("0")))
    tax_amount = _q(subtotal * Decimal(str(tax_rate or 0)))
    total = _q(subtotal + tax_amount)
    return subtotal, tax_amount, total


def due_date_from_terms(txn_date: date, terms: str | None, default_days: int = 30) -> date:
    """Parse 'Net N' terms to a due date. Falls back to default_days on parse failure."""
    if not terms:
        return txn_date + timedelta(days=default_days)
    try:
        days = int(terms.lower().replace("net ", "").strip())
    except ValueError:
        days = default_days
    return txn_date + timedelta(days=days)


def create_journal_entry(
    db: Session,
    txn_date: date,
    description: str,
    lines: list[dict],
    source_type: str = None,
    source_id: int = None,
    reference: str = None,
) -> Transaction:
    """Create a balanced journal entry.

    lines: [{"account_id": int, "debit": Decimal, "credit": Decimal}, ...]
    Each line must have debit > 0 OR credit > 0, not both.
    Total debits must equal total credits.
    """
    # Validate individual lines before summing
    for i, l in enumerate(lines):
        debit = Decimal(str(l.get("debit", 0)))
        credit = Decimal(str(l.get("credit", 0)))
        if debit < 0 or credit < 0:
            raise ValueError(f"Line {i+1}: debit and credit must be non-negative")
        if debit > 0 and credit > 0:
            raise ValueError(f"Line {i+1}: a line cannot have both debit and credit")

    total_debit = sum(Decimal(str(l.get("debit", 0))) for l in lines)
    total_credit = sum(Decimal(str(l.get("credit", 0))) for l in lines)

    if total_debit != total_credit:
        raise ValueError(f"Journal entry not balanced: debits={total_debit}, credits={total_credit}")

    txn = Transaction(
        date=txn_date,
        description=description,
        source_type=source_type,
        source_id=source_id,
        reference=reference,
    )
    db.add(txn)
    db.flush()

    for line_data in lines:
        debit = Decimal(str(line_data.get("debit", 0)))
        credit = Decimal(str(line_data.get("credit", 0)))
        if debit == 0 and credit == 0:
            continue

        txn_line = TransactionLine(
            transaction_id=txn.id,
            account_id=line_data["account_id"],
            debit=debit,
            credit=credit,
            description=line_data.get("description", ""),
        )
        db.add(txn_line)

        # Update account balance
        account = db.query(Account).filter(Account.id == line_data["account_id"]).first()
        if account:
            if account.account_type.value in ("asset", "expense", "cogs"):
                account.balance += debit - credit
            else:
                account.balance += credit - debit

    return txn


def get_ar_account_id(db: Session) -> int:
    """Get Accounts Receivable account ID (1100)."""
    acct = db.query(Account).filter(Account.account_number == "1100").first()
    return acct.id if acct else None


def get_default_income_account_id(db: Session) -> int:
    """Get default Service Income account ID (4000)."""
    acct = db.query(Account).filter(Account.account_number == "4000").first()
    return acct.id if acct else None


def get_sales_tax_account_id(db: Session) -> int:
    """Get Sales Tax Payable account ID (2200)."""
    acct = db.query(Account).filter(Account.account_number == "2200").first()
    return acct.id if acct else None


def get_undeposited_funds_id(db: Session) -> int:
    """Get Undeposited Funds account ID (1200)."""
    acct = db.query(Account).filter(Account.account_number == "1200").first()
    return acct.id if acct else None


def get_ap_account_id(db: Session) -> int:
    """Get Accounts Payable account ID (2000)."""
    acct = db.query(Account).filter(Account.account_number == "2000").first()
    return acct.id if acct else None
