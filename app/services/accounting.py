# ============================================================================
# Decompiled from qbw32.exe!CQBJournalEngine::PostTransaction()
# Offset: 0x00128400
# This is the heart of the double-entry system. Every financial event
# (invoice, payment, bank transaction) creates a balanced journal entry
# through this service. The original validated sum(debits) == sum(credits)
# with a tolerance of 0.004 (BCD rounding). We use exact Decimal math.
# ============================================================================

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.transactions import Transaction, TransactionLine
from app.models.accounts import Account, AccountType
from app.models.settings import Settings


@dataclass(frozen=True)
class SystemAccountRoleDefinition:
    key: str
    label: str
    description: str
    account_type: AccountType
    fallback_numbers: tuple[str, ...] = ()
    fallback_names: tuple[str, ...] = ()
    prefer_first_by_type: bool = False
    auto_create_on_use: bool = False


def get_or_create_system_account(
    db: Session,
    account_number: str,
    name: str,
    account_type: AccountType,
) -> int | None:
    acct = db.query(Account).filter(Account.account_number == account_number).first()
    if acct:
        acct.name = name
        acct.account_type = account_type
        acct.is_system = True
        acct.is_active = True
        db.flush()
        return acct.id

    acct = Account(
        name=name,
        account_number=account_number,
        account_type=account_type,
        is_system=True,
        is_active=True,
    )
    db.add(acct)
    db.flush()
    return acct.id if acct else None


def _account_id_from_setting(db: Session, key: str, account_type: AccountType | None = None) -> int | None:
    row = db.query(Settings).filter(Settings.key == key).first()
    if not row or not row.value:
        return None
    try:
        account_id = int(str(row.value).strip())
    except (TypeError, ValueError):
        return None
    account = db.query(Account).filter(Account.id == account_id, Account.is_active == True).first()
    if not account:
        return None
    if account_type and account.account_type != account_type:
        return None
    return account.id


def _setting_value(db: Session, key: str) -> str | None:
    row = db.query(Settings).filter(Settings.key == key).first()
    return row.value if row else None


def _first_matching_account(
    db: Session,
    account_type: AccountType | None = None,
    account_numbers: tuple[str, ...] = (),
    account_names: tuple[str, ...] = (),
    prefer_first_by_type: bool = False,
) -> int | None:
    if account_numbers:
        account = (
            db.query(Account)
            .filter(Account.account_number.in_(account_numbers), Account.is_active == True)
            .order_by(Account.account_number)
            .first()
        )
        if account and (not account_type or account.account_type == account_type):
            return account.id

    for name in account_names:
        account = (
            db.query(Account)
            .filter(func.lower(Account.name) == name.lower(), Account.is_active == True)
            .first()
        )
        if account and (not account_type or account.account_type == account_type):
            return account.id

    if prefer_first_by_type and account_type:
        account = (
            db.query(Account)
            .filter(Account.account_type == account_type, Account.is_active == True)
            .order_by(Account.account_number, Account.name)
            .first()
        )
        if account:
            return account.id

    return None


def _resolve_system_account_id(
    db: Session,
    setting_key: str,
    account_type: AccountType | None = None,
    fallback_numbers: tuple[str, ...] = (),
    fallback_names: tuple[str, ...] = (),
    prefer_first_by_type: bool = False,
    create_spec: tuple[str, str, AccountType] | None = None,
    allow_create: bool = True,
) -> int | None:
    explicit = _account_id_from_setting(db, setting_key, account_type=account_type)
    if explicit:
        return explicit

    fallback = _first_matching_account(
        db,
        account_type=account_type,
        account_numbers=fallback_numbers,
        account_names=fallback_names,
        prefer_first_by_type=prefer_first_by_type,
    )
    if fallback:
        return fallback

    if create_spec and allow_create:
        account_number, name, create_type = create_spec
        return get_or_create_system_account(db, account_number, name, create_type)
    return None


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


def reverse_journal_entry(
    db: Session,
    transaction_id: int,
    reversal_date: date,
    description: str,
    source_type: str = None,
    source_id: int = None,
    reference: str = None,
) -> Transaction | None:
    """Create a reversing entry for an existing journal transaction."""
    original = db.query(Transaction).filter(Transaction.id == transaction_id).first()
    if not original:
        return None

    reverse_lines = [
        {
            "account_id": line.account_id,
            "debit": line.credit,
            "credit": line.debit,
            "description": f"REVERSAL: {line.description or ''}",
        }
        for line in original.lines
    ]
    if not reverse_lines:
        return None

    return create_journal_entry(
        db,
        reversal_date,
        description,
        reverse_lines,
        source_type=source_type,
        source_id=source_id,
        reference=reference,
    )


def get_ar_account_id(db: Session, allow_create: bool = True) -> int:
    """Get Accounts Receivable account ID."""
    return _resolve_system_account_id(
        db,
        "system_account_accounts_receivable_id",
        account_type=AccountType.ASSET,
        fallback_numbers=("1100",),
        fallback_names=("Accounts Receivable", "Trade Debtors"),
        allow_create=allow_create,
    )


def get_default_income_account_id(db: Session, allow_create: bool = True) -> int:
    """Get default sales income account ID."""
    return _resolve_system_account_id(
        db,
        "system_account_default_sales_income_id",
        account_type=AccountType.INCOME,
        fallback_numbers=("4000",),
        fallback_names=("Service Income", "Sales"),
        prefer_first_by_type=True,
        allow_create=allow_create,
    )


def get_gst_account_id(db: Session, allow_create: bool = True) -> int:
    """Get or create the NZ GST control account."""
    explicit = _account_id_from_setting(db, "system_account_gst_control_id", account_type=AccountType.LIABILITY)
    if explicit:
        return explicit

    account = (
        db.query(Account)
        .filter(
            Account.is_active == True,
            (
                (Account.account_number == "2200")
                | (func.lower(Account.name) == "gst")
                | (func.lower(Account.name) == "sales tax payable")
            ),
        )
        .order_by(Account.account_number)
        .first()
    )
    if account:
        if account.name == "Sales Tax Payable":
            account.name = "GST"
        account.account_type = AccountType.LIABILITY
        account.is_system = True
        db.flush()
        return account.id

    if allow_create:
        return get_or_create_system_account(db, "2200", "GST", AccountType.LIABILITY)
    return None


def get_sales_tax_account_id(db: Session) -> int:
    """Compatibility alias for the NZ GST account."""
    return get_gst_account_id(db)


def get_undeposited_funds_id(db: Session, allow_create: bool = True) -> int:
    """Get undeposited funds / receipt clearing account ID."""
    return _resolve_system_account_id(
        db,
        "system_account_undeposited_funds_id",
        account_type=AccountType.ASSET,
        fallback_numbers=("1200",),
        fallback_names=("Undeposited Funds", "Receipt Clearing"),
        prefer_first_by_type=True,
        allow_create=allow_create,
    )


def get_ap_account_id(db: Session, allow_create: bool = True) -> int:
    """Get accounts payable account ID."""
    return _resolve_system_account_id(
        db,
        "system_account_accounts_payable_id",
        account_type=AccountType.LIABILITY,
        fallback_numbers=("2000",),
        fallback_names=("Accounts Payable", "Trade Creditors"),
        allow_create=allow_create,
    )


def get_default_expense_account_id(db: Session, allow_create: bool = True) -> int | None:
    return _resolve_system_account_id(
        db,
        "system_account_default_expense_id",
        account_type=AccountType.EXPENSE,
        fallback_numbers=("6000",),
        fallback_names=("Expenses", "Advertising & Marketing", "Purchases"),
        prefer_first_by_type=True,
        allow_create=allow_create,
    )


def get_default_bank_account_id(db: Session, allow_create: bool = True) -> int | None:
    return _resolve_system_account_id(
        db,
        "system_account_default_bank_id",
        account_type=AccountType.ASSET,
        fallback_numbers=("1000", "1010"),
        fallback_names=("Checking", "Business Bank Account", "Operating Account"),
        prefer_first_by_type=True,
        allow_create=allow_create,
    )


def get_wages_expense_account_id(db: Session, allow_create: bool = True) -> int | None:
    return _resolve_system_account_id(
        db,
        "system_account_wages_expense_id",
        account_type=AccountType.EXPENSE,
        fallback_numbers=("7000",),
        fallback_names=("Wages & Salaries Expense", "Salaries"),
        create_spec=("7000", "Wages & Salaries Expense", AccountType.EXPENSE),
        allow_create=allow_create,
    )


def get_employer_kiwisaver_expense_account_id(db: Session, allow_create: bool = True) -> int | None:
    return _resolve_system_account_id(
        db,
        "system_account_employer_kiwisaver_expense_id",
        account_type=AccountType.EXPENSE,
        fallback_numbers=("7010",),
        fallback_names=("Employer KiwiSaver Expense", "KiwiSaver Employer Contributions"),
        create_spec=("7010", "Employer KiwiSaver Expense", AccountType.EXPENSE),
        allow_create=allow_create,
    )


def get_paye_payable_account_id(db: Session, allow_create: bool = True) -> int | None:
    return _resolve_system_account_id(
        db,
        "system_account_paye_payable_id",
        account_type=AccountType.LIABILITY,
        fallback_numbers=("2310",),
        fallback_names=("PAYE Payable", "PAYE Liability"),
        create_spec=("2310", "PAYE Payable", AccountType.LIABILITY),
        allow_create=allow_create,
    )


def get_kiwisaver_payable_account_id(db: Session, allow_create: bool = True) -> int | None:
    return _resolve_system_account_id(
        db,
        "system_account_kiwisaver_payable_id",
        account_type=AccountType.LIABILITY,
        fallback_numbers=("2315",),
        fallback_names=("KiwiSaver Payable", "KiwiSaver Liability"),
        create_spec=("2315", "KiwiSaver Payable", AccountType.LIABILITY),
        allow_create=allow_create,
    )


def get_esct_payable_account_id(db: Session, allow_create: bool = True) -> int | None:
    return _resolve_system_account_id(
        db,
        "system_account_esct_payable_id",
        account_type=AccountType.LIABILITY,
        fallback_numbers=("2320",),
        fallback_names=("ESCT Payable", "ESCT Liability"),
        create_spec=("2320", "ESCT Payable", AccountType.LIABILITY),
        allow_create=allow_create,
    )


def get_child_support_payable_account_id(db: Session, allow_create: bool = True) -> int | None:
    return _resolve_system_account_id(
        db,
        "system_account_child_support_payable_id",
        account_type=AccountType.LIABILITY,
        fallback_numbers=("2325",),
        fallback_names=("Child Support Payable", "Child Support Liability"),
        create_spec=("2325", "Child Support Payable", AccountType.LIABILITY),
        allow_create=allow_create,
    )


def get_payroll_clearing_account_id(db: Session, allow_create: bool = True) -> int | None:
    return _resolve_system_account_id(
        db,
        "system_account_payroll_clearing_id",
        account_type=AccountType.LIABILITY,
        fallback_numbers=("2330",),
        fallback_names=("Payroll Clearing", "Net Wages Clearing"),
        create_spec=("2330", "Payroll Clearing", AccountType.LIABILITY),
        allow_create=allow_create,
    )


SYSTEM_ACCOUNT_ROLE_DEFINITIONS = (
    SystemAccountRoleDefinition(
        key="system_account_accounts_receivable_id",
        label="Accounts Receivable",
        description="Used for invoice receivables and payment allocations.",
        account_type=AccountType.ASSET,
        fallback_numbers=("1100",),
        fallback_names=("Accounts Receivable", "Trade Debtors"),
    ),
    SystemAccountRoleDefinition(
        key="system_account_accounts_payable_id",
        label="Accounts Payable",
        description="Used for bill liabilities and bill payment allocations.",
        account_type=AccountType.LIABILITY,
        fallback_numbers=("2000",),
        fallback_names=("Accounts Payable", "Trade Creditors"),
    ),
    SystemAccountRoleDefinition(
        key="system_account_gst_control_id",
        label="GST Control",
        description="Used for NZ GST output and input control postings.",
        account_type=AccountType.LIABILITY,
        fallback_numbers=("2200",),
        fallback_names=("GST", "Sales Tax Payable"),
        auto_create_on_use=True,
    ),
    SystemAccountRoleDefinition(
        key="system_account_undeposited_funds_id",
        label="Undeposited Funds",
        description="Used as the receipt clearing account for customer payments.",
        account_type=AccountType.ASSET,
        fallback_numbers=("1200",),
        fallback_names=("Undeposited Funds", "Receipt Clearing"),
        prefer_first_by_type=True,
    ),
    SystemAccountRoleDefinition(
        key="system_account_default_sales_income_id",
        label="Default Sales Income",
        description="Default income account when items or lines do not specify one.",
        account_type=AccountType.INCOME,
        fallback_numbers=("4000",),
        fallback_names=("Service Income", "Sales"),
        prefer_first_by_type=True,
    ),
    SystemAccountRoleDefinition(
        key="system_account_default_expense_id",
        label="Default Expense",
        description="Default expense account when purchase lines do not specify one.",
        account_type=AccountType.EXPENSE,
        fallback_numbers=("6000",),
        fallback_names=("Expenses", "Advertising & Marketing", "Purchases"),
        prefer_first_by_type=True,
    ),
    SystemAccountRoleDefinition(
        key="system_account_default_bank_id",
        label="Default Bank",
        description="Default bank account for outbound cash payments.",
        account_type=AccountType.ASSET,
        fallback_numbers=("1000", "1010"),
        fallback_names=("Checking", "Business Bank Account", "Operating Account"),
        prefer_first_by_type=True,
    ),
    SystemAccountRoleDefinition(
        key="system_account_wages_expense_id",
        label="Wages Expense",
        description="Payroll gross wages expense account.",
        account_type=AccountType.EXPENSE,
        fallback_numbers=("7000",),
        fallback_names=("Wages & Salaries Expense", "Salaries"),
        auto_create_on_use=True,
    ),
    SystemAccountRoleDefinition(
        key="system_account_employer_kiwisaver_expense_id",
        label="Employer KiwiSaver Expense",
        description="Payroll employer KiwiSaver expense account.",
        account_type=AccountType.EXPENSE,
        fallback_numbers=("7010",),
        fallback_names=("Employer KiwiSaver Expense", "KiwiSaver Employer Contributions"),
        auto_create_on_use=True,
    ),
    SystemAccountRoleDefinition(
        key="system_account_paye_payable_id",
        label="PAYE Payable",
        description="Payroll liability for PAYE, ACC earners levy, and student loan deductions.",
        account_type=AccountType.LIABILITY,
        fallback_numbers=("2310",),
        fallback_names=("PAYE Payable", "PAYE Liability"),
        auto_create_on_use=True,
    ),
    SystemAccountRoleDefinition(
        key="system_account_kiwisaver_payable_id",
        label="KiwiSaver Payable",
        description="Payroll liability for employee and employer KiwiSaver payable amounts.",
        account_type=AccountType.LIABILITY,
        fallback_numbers=("2315",),
        fallback_names=("KiwiSaver Payable", "KiwiSaver Liability"),
        auto_create_on_use=True,
    ),
    SystemAccountRoleDefinition(
        key="system_account_esct_payable_id",
        label="ESCT Payable",
        description="Payroll liability for ESCT deductions.",
        account_type=AccountType.LIABILITY,
        fallback_numbers=("2320",),
        fallback_names=("ESCT Payable", "ESCT Liability"),
        auto_create_on_use=True,
    ),
    SystemAccountRoleDefinition(
        key="system_account_child_support_payable_id",
        label="Child Support Payable",
        description="Payroll liability for child support deductions.",
        account_type=AccountType.LIABILITY,
        fallback_numbers=("2325",),
        fallback_names=("Child Support Payable", "Child Support Liability"),
        auto_create_on_use=True,
    ),
    SystemAccountRoleDefinition(
        key="system_account_payroll_clearing_id",
        label="Payroll Clearing",
        description="Payroll liability for net wages payable.",
        account_type=AccountType.LIABILITY,
        fallback_numbers=("2330",),
        fallback_names=("Payroll Clearing", "Net Wages Clearing"),
        auto_create_on_use=True,
    ),
)

SYSTEM_ACCOUNT_ROLE_DEFINITION_MAP = {definition.key: definition for definition in SYSTEM_ACCOUNT_ROLE_DEFINITIONS}


def get_system_account_role_definition(role_key: str) -> SystemAccountRoleDefinition | None:
    return SYSTEM_ACCOUNT_ROLE_DEFINITION_MAP.get(role_key)


def list_system_account_role_definitions() -> tuple[SystemAccountRoleDefinition, ...]:
    return SYSTEM_ACCOUNT_ROLE_DEFINITIONS


def _get_account_by_id(db: Session, account_id: int | None) -> Account | None:
    if not account_id:
        return None
    return db.query(Account).filter(Account.id == account_id).first()


def _role_resolved_account_id(db: Session, role_key: str, allow_create: bool = False) -> int | None:
    if role_key == "system_account_accounts_receivable_id":
        return get_ar_account_id(db, allow_create=allow_create)
    if role_key == "system_account_accounts_payable_id":
        return get_ap_account_id(db, allow_create=allow_create)
    if role_key == "system_account_gst_control_id":
        return get_gst_account_id(db, allow_create=allow_create)
    if role_key == "system_account_undeposited_funds_id":
        return get_undeposited_funds_id(db, allow_create=allow_create)
    if role_key == "system_account_default_sales_income_id":
        return get_default_income_account_id(db, allow_create=allow_create)
    if role_key == "system_account_default_expense_id":
        return get_default_expense_account_id(db, allow_create=allow_create)
    if role_key == "system_account_default_bank_id":
        return get_default_bank_account_id(db, allow_create=allow_create)
    if role_key == "system_account_wages_expense_id":
        return get_wages_expense_account_id(db, allow_create=allow_create)
    if role_key == "system_account_employer_kiwisaver_expense_id":
        return get_employer_kiwisaver_expense_account_id(db, allow_create=allow_create)
    if role_key == "system_account_paye_payable_id":
        return get_paye_payable_account_id(db, allow_create=allow_create)
    if role_key == "system_account_kiwisaver_payable_id":
        return get_kiwisaver_payable_account_id(db, allow_create=allow_create)
    if role_key == "system_account_esct_payable_id":
        return get_esct_payable_account_id(db, allow_create=allow_create)
    if role_key == "system_account_child_support_payable_id":
        return get_child_support_payable_account_id(db, allow_create=allow_create)
    if role_key == "system_account_payroll_clearing_id":
        return get_payroll_clearing_account_id(db, allow_create=allow_create)
    return None


def get_system_account_role_status(db: Session, role_key: str) -> dict | None:
    definition = get_system_account_role_definition(role_key)
    if not definition:
        return None

    raw_value = _setting_value(db, role_key)
    configured_account = None
    configured_account_valid = False
    warning = None
    if raw_value:
        try:
            configured_account = _get_account_by_id(db, int(str(raw_value).strip()))
        except (TypeError, ValueError):
            warning = "Stored mapping is not a valid account id."
        else:
            if not configured_account:
                warning = "Configured account no longer exists."
            elif not configured_account.is_active:
                warning = "Configured account is inactive; runtime will fall back."
            elif configured_account.account_type != definition.account_type:
                warning = (
                    f"Configured account type is {configured_account.account_type.value}; "
                    f"{definition.account_type.value} is required."
                )
            else:
                configured_account_valid = True

    resolved_account = _get_account_by_id(db, _role_resolved_account_id(db, role_key, allow_create=False))
    if configured_account_valid:
        status = "configured"
    elif resolved_account:
        status = "fallback"
        if not warning:
            warning = "Runtime is using fallback account resolution for this role."
    else:
        status = "missing"
        if definition.auto_create_on_use:
            warning = "No explicit or fallback account is available; runtime will auto-create this role on use."
        elif not warning:
            warning = "No explicit or fallback account is available for this role."

    return {
        "role_key": definition.key,
        "label": definition.label,
        "description": definition.description,
        "account_type": definition.account_type,
        "status": status,
        "auto_create_on_use": definition.auto_create_on_use,
        "configured_account_valid": configured_account_valid,
        "configured_account": configured_account,
        "resolved_account": resolved_account,
        "warning": warning,
    }


def list_system_account_role_statuses(db: Session) -> list[dict]:
    return [get_system_account_role_status(db, definition.key) for definition in SYSTEM_ACCOUNT_ROLE_DEFINITIONS]


def set_system_account_role_mapping(db: Session, role_key: str, account_id: int | None) -> dict | None:
    definition = get_system_account_role_definition(role_key)
    if not definition:
        return None

    row = db.query(Settings).filter(Settings.key == role_key).first()
    if account_id is None:
        if row:
            row.value = ""
        else:
            db.add(Settings(key=role_key, value=""))
        db.commit()
        return get_system_account_role_status(db, role_key)

    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise ValueError("Account not found")
    if not account.is_active:
        raise ValueError("Account must be active")
    if account.account_type != definition.account_type:
        raise ValueError(f"Account must be of type {definition.account_type.value}")

    if row:
        row.value = str(account.id)
    else:
        db.add(Settings(key=role_key, value=str(account.id)))
    db.commit()
    return get_system_account_role_status(db, role_key)
