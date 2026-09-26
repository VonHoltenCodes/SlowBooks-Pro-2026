# ============================================================================
# The heart of the double-entry system. Every financial event (invoice,
# payment, bank transaction) creates a balanced journal entry through this
# service — sum(debits) == sum(credits), exact Decimal math.
# ============================================================================

from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy.orm import Session

from app.models.transactions import Transaction, TransactionLine
from app.models.accounts import Account, AccountType
from app.services import control_accounts
from app.services.safe_errors import DataProblem

CENT = Decimal("0.01")


def quantize_to(value, exp: Decimal, rounding=ROUND_HALF_UP) -> Decimal:
    """Coerce to Decimal quantized to an arbitrary exponent (half-up).

    For non-money precisions (4-decimal unit costs, quantities). None/falsy
    coerce to 0 so callers can quantize optional/nullable amounts directly.
    """
    if not isinstance(value, Decimal):
        value = Decimal(str(value or 0))
    return value.quantize(exp, rounding=rounding)


def _q(value) -> Decimal:
    """Coerce to Decimal rounded to two places (half-up, matches PostgreSQL default).

    This is the single canonical money-quantize helper for the app. None/falsy
    coerce to 0.00 so callers can quantize optional/nullable amounts directly.
    """
    return quantize_to(value, CENT)


# Public alias — reads more clearly at call sites that prefer a descriptive name.
quantize_cents = _q


def compute_line_totals(lines, tax_rate) -> tuple[Decimal, Decimal, Decimal]:
    """Return (subtotal, tax_amount, total), each quantized to 2 decimals.

    `lines` is any iterable of objects with .quantity and .rate attributes.
    Rounds each line's amount before summing so per-line DB storage matches the
    sum (prevents drift between stored invoice.total and DB-rounded journal
    lines).
    """
    lines = list(lines)
    subtotal = _q(
        sum(
            (
                _q(Decimal(str(line.quantity)) * Decimal(str(line.rate)))
                for line in lines
            ),
            Decimal("0"),
        )
    )
    tax_amount = _q(taxable_subtotal(lines) * Decimal(str(tax_rate or 0)))
    total = _q(subtotal + tax_amount)
    return subtotal, tax_amount, total


def taxed_copy_lines(lines, customer):
    """Lines a NEW document is built from (an estimate converting, an invoice
    being duplicated, a recurring template running), with each line's tax
    flag as the customer stands TODAY: a customer marked non-taxable pays no
    tax on any line, whatever the source said. Returns lightweight objects
    carrying quantity, rate and is_taxable for compute_line_totals, in the
    source order, plus the flags to store on the new lines.

    Copying the source's tax instead charged a reseller tax on a new invoice
    converted from an estimate saved before the exemption applied (2.16.2
    gate, skytech — booked to Sales Tax Payable)."""
    from types import SimpleNamespace

    exempt = customer is not None and customer.is_taxable is False
    out = []
    for ln in lines:
        flag = False if exempt else (getattr(ln, "is_taxable", None) is not False)
        out.append(SimpleNamespace(quantity=ln.quantity, rate=ln.rate, is_taxable=flag))
    return out


def taxable_subtotal(lines) -> Decimal:
    """Sum of the lines the tax rate applies to. A line without an
    is_taxable attribute (or with it None) counts as taxable — the pre-
    per-line behaviour — so every caller that never heard of the flag keeps
    its numbers."""
    return _q(
        sum(
            (
                _q(Decimal(str(line.quantity)) * Decimal(str(line.rate)))
                for line in lines
                if getattr(line, "is_taxable", None) is not False
            ),
            Decimal("0"),
        )
    )


def due_date_from_terms(
    txn_date: date, terms: str | None, default_days: int = 30
) -> date:
    """Parse 'Net N' terms to a due date. Falls back to default_days on parse failure."""
    if not terms:
        return txn_date + timedelta(days=default_days)
    try:
        days = int(terms.lower().replace("net ", "").strip())
    except ValueError:
        days = default_days
    return txn_date + timedelta(days=days)


def _cost_type_of(db: Session, cost_code_id) -> str | None:
    """The cost type a coded line belongs to, so job roll-ups by type never
    have to re-join the code table."""
    if not cost_code_id:
        return None
    from app.models.cost_codes import CostCode

    code = db.get(CostCode, cost_code_id)
    return code.cost_type if code else None


def reversing_lines(lines) -> list[dict]:
    """Mirror-image line dicts for a void: debit and credit swapped, the
    description prefixed VOID:, and every dimension (job, class, cost code,
    cost type, function) carried over so the by-class, by-job and
    functional reports net to zero for the voided document instead of
    leaving the tag on one side."""
    return [
        {
            "account_id": ol.account_id,
            "debit": ol.credit,
            "credit": ol.debit,
            "description": f"VOID: {ol.description or ''}",
            "job_id": ol.job_id,
            "class_id": ol.class_id,
            "cost_code_id": ol.cost_code_id,
            "cost_type": ol.cost_type,
            "function": ol.function,
        }
        for ol in lines
    ]


def create_journal_entry(
    db: Session,
    txn_date: date,
    description: str,
    lines: list[dict],
    source_type: str = None,
    source_id: int = None,
    reference: str = None,
    bypass_closing_date: bool = False,
    class_id: int = None,
    job_id: int = None,
    *,
    existing_transaction: Transaction = None,
) -> Transaction:
    """Create a balanced journal entry.

    lines: [{"account_id": int, "debit": Decimal, "credit": Decimal}, ...]
    Each line must have debit > 0 OR credit > 0, not both. A line may carry
    its own "job_id" / "class_id"; otherwise it inherits the header values,
    so job and class reports can always group on the line. A line's
    "function" (nonprofit: program / management / fundraising) is taken as
    given when the key is present — including an explicit None — and
    otherwise defaulted from the class it lands in.
    Total debits must equal total credits.

    Closing-date enforcement runs here so every JE-posting path inherits it
    (recurring invoices, IIF/QBO imports, inventory hooks, and all route
    handlers). Route handlers also call check_closing_date earlier for better
    UX; the redundancy is intentional. `bypass_closing_date` is an escape
    hatch for operators with no current caller — do not set it in app code.
    """
    if not bypass_closing_date:
        # Local import: closing_date imports the settings model, so importing
        # it at module scope risks a circular import as the model layer grows.
        from app.services.closing_date import check_closing_date

        check_closing_date(db, txn_date)

    # Validate individual lines before summing
    for i, line in enumerate(lines):
        debit = Decimal(str(line.get("debit", 0)))
        credit = Decimal(str(line.get("credit", 0)))
        if debit < 0 or credit < 0:
            raise DataProblem(f"Line {i+1}: debit and credit must be non-negative")
        if debit > 0 and credit > 0:
            raise DataProblem(f"Line {i+1}: a line cannot have both debit and credit")

    total_debit = sum(Decimal(str(line.get("debit", 0))) for line in lines)
    total_credit = sum(Decimal(str(line.get("credit", 0))) for line in lines)

    if total_debit != total_credit:
        # The sentence a person reads when they save an unbalanced entry
        # (the journal form's own live wording, not "debits=5000, ...").
        raise DataProblem(
            "Debits and credits must be equal: this entry is out of balance "
            f"by ${abs(total_debit - total_credit):,.2f}"
        )

    # Invoice edits retain their header identity after removing old splits.
    # Reject nonempty journals so this path cannot double-post their balances.
    if existing_transaction is not None and (
        db.query(TransactionLine)
        .filter(TransactionLine.transaction_id == existing_transaction.id)
        .first()
        is not None
    ):
        raise DataProblem("Replacement journal must have its old splits removed")

    header = dict(
        date=txn_date,
        description=description,
        source_type=source_type,
        source_id=source_id,
        reference=reference,
        class_id=class_id,
        job_id=job_id,
    )
    txn = existing_transaction
    if txn is None:
        txn = Transaction(**header)
        db.add(txn)
    else:
        for key, value in header.items():
            setattr(txn, key, value)
    db.flush()

    from app.services.classes_service import default_function_of

    fn_cache: dict = {}
    for line_data in lines:
        debit = Decimal(str(line_data.get("debit", 0)))
        credit = Decimal(str(line_data.get("credit", 0)))
        if debit == 0 and credit == 0:
            continue

        line_class_id = line_data.get("class_id") or class_id
        if "function" in line_data:
            function = line_data["function"]
        else:
            function = default_function_of(db, line_class_id, fn_cache)

        txn_line = TransactionLine(
            transaction_id=txn.id,
            account_id=line_data["account_id"],
            debit=debit,
            credit=credit,
            description=line_data.get("description", ""),
            job_id=line_data.get("job_id") or job_id,
            class_id=line_class_id,
            function=function,
            cost_code_id=line_data.get("cost_code_id"),
            cost_type=line_data.get("cost_type")
            or _cost_type_of(db, line_data.get("cost_code_id")),
            is_billable=bool(line_data.get("is_billable", False)),
        )
        db.add(txn_line)

        # Update account balance
        account = (
            db.query(Account).filter(Account.id == line_data["account_id"]).first()
        )
        if account:
            if account.account_type.value in ("asset", "expense", "cogs"):
                account.balance += debit - credit
            else:
                account.balance += credit - debit

    return txn


# The control-account resolvers RAISE when the account is missing; they never
# return None (issue #119). A caller that treated None as "skip the journal
# entry" produced a document that looked saved and never reached the books —
# with a trial balance that still balanced, so nothing downstream noticed.
# See app/services/control_accounts.py for the registry and the reasoning.


def get_ar_account_id(db: Session) -> int:
    """Accounts Receivable (1100). Raises MissingControlAccount."""
    return control_accounts.resolve(db, "1100")


def get_default_income_account_id(db: Session) -> int:
    """Default Service Income (4000). Raises MissingControlAccount."""
    return control_accounts.resolve(db, "4000")


def get_sales_tax_account_id(db: Session) -> int:
    """Sales Tax Payable (2200). Raises MissingControlAccount."""
    return control_accounts.resolve(db, "2200")


def get_undeposited_funds_id(db: Session) -> int:
    """Undeposited Funds (1200). Raises MissingControlAccount."""
    return control_accounts.resolve(db, "1200")


def get_ap_account_id(db: Session) -> int:
    """Accounts Payable (2000). Raises MissingControlAccount."""
    return control_accounts.resolve(db, "2000")


def get_cc_account_id(db: Session) -> int:
    """Credit Card (2100). Raises MissingControlAccount."""
    return control_accounts.resolve(db, "2100")


def get_opening_balance_equity_id(db: Session) -> int:
    """3900 Opening Balance Equity, created on demand: the offset for a bank
    or card account's opening balance (issue #114)."""
    return ensure_account(db, "3900", "Opening Balance Equity", AccountType.EQUITY).id


def ensure_account(
    db: Session, number: str, name: str, account_type: AccountType
) -> Account:
    """Find-or-create a system account by name, keeping the suggested
    number only if the chart hasn't used it (account_number is unique and
    an imported chart may already own 3300 or 6960). Flushes; the caller
    commits. Pattern shared with the job-costing offset accounts."""
    acct = db.query(Account).filter(Account.name == name).first()
    if acct:
        return acct
    taken = db.query(Account.id).filter(Account.account_number == number).first()
    acct = Account(
        name=name,
        account_type=account_type,
        account_number=None if taken else number,
        is_system=True,
        balance=Decimal("0"),
    )
    db.add(acct)
    db.flush()
    return acct


# Nonprofit accounts, created on demand (Settings -> Company Type, or the
# first document that needs them). Numbers follow the seed chart's blocks;
# 4300 is already Labor Income, so in-kind income sits at 4400.
NONPROFIT_ACCOUNTS = (
    ("3300", "Net Assets Without Donor Restrictions", AccountType.EQUITY),
    ("3400", "Net Assets With Donor Restrictions", AccountType.EQUITY),
    ("4400", "In-Kind Contributions", AccountType.INCOME),
    ("6960", "Bad Debt Expense", AccountType.EXPENSE),
)


def ensure_nonprofit_accounts(db: Session) -> dict[str, Account]:
    """Create every nonprofit account that is missing; returns them keyed by
    their suggested number. Idempotent."""
    return {
        number: ensure_account(db, number, name, atype)
        for number, name, atype in NONPROFIT_ACCOUNTS
    }


def get_net_assets_without_restriction_id(db: Session) -> int:
    return ensure_nonprofit_accounts(db)["3300"].id


def get_net_assets_with_restriction_id(db: Session) -> int:
    return ensure_nonprofit_accounts(db)["3400"].id


def get_in_kind_income_account_id(db: Session) -> int:
    return ensure_nonprofit_accounts(db)["4400"].id


def get_bad_debt_account_id(db: Session) -> int:
    return ensure_nonprofit_accounts(db)["6960"].id
