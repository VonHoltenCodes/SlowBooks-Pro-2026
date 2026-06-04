# ============================================================================
# NACHA ACH Export Service — direct-deposit file generation
# ----------------------------------------------------------------------------
# Produces the fixed-width 94-character record format banks accept for ACH
# origination. Builds the standard record hierarchy:
#
#   1  File Header
#   5  Batch Header
#   6  Entry Detail        (one per employee bank-account credit)
#   8  Batch Control
#   9  File Control
#   9...  9-filled padding so total record count is a multiple of 10
#
# Net pay is split across an employee's active bank accounts honoring the
# deposit_type (FULL / FIXED / PERCENT / REMAINDER) in priority order.
#
# DISCLAIMER: NACHA Operating Rules evolve. Verify field placement / SEC
# codes against your originating bank's ACH origination spec before live use.
# ============================================================================

from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy.orm import Session, joinedload

from app.models.payroll import PayRun, PayRunStatus, PayStub
from app.models.bank_accounts import (
    EmployeeBankAccount,
    BankAccountKind,
    DepositType,
    PrenoteStatus,
)
from app.services.encryption import decrypt

CENT = Decimal("0.01")
RECORD_LENGTH = 94
BLOCKING_FACTOR = 10

# Service Class Codes
SCC_MIXED = "200"  # mixed debits and credits
# Standard Entry Class — PPD is the consumer direct-deposit code.
SEC_CODE = "PPD"

# Entry detail transaction codes
TXN_CHECKING_CREDIT = "22"
TXN_SAVINGS_CREDIT = "32"
TXN_CHECKING_PRENOTE = "23"
TXN_SAVINGS_PRENOTE = "33"


# --- fixed-width field formatting helpers -----------------------------------


def _num(value, width: int) -> str:
    """Right-justified, zero-filled numeric field, truncated to width."""
    s = "".join(ch for ch in str(value) if ch.isdigit())
    if len(s) > width:
        s = s[-width:]
    return s.rjust(width, "0")


def _alpha(value, width: int) -> str:
    """Left-justified, space-filled alphanumeric field, truncated to width."""
    s = "" if value is None else str(value)
    if len(s) > width:
        s = s[:width]
    return s.ljust(width, " ")


def _q(value) -> Decimal:
    """Quantize a money value to 2 decimal places."""
    if not isinstance(value, Decimal):
        value = Decimal(str(value or 0))
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _cents(value: Decimal) -> int:
    """Convert a Decimal dollar amount to whole cents."""
    return int((_q(value) * 100).to_integral_value(rounding=ROUND_HALF_UP))


def _routing_prefix(routing: str | None) -> str:
    """First 8 digits of a 9-digit routing number (the receiving-DFI id)."""
    digits = "".join(ch for ch in (routing or "") if ch.isdigit())
    return digits[:8].rjust(8, "0")


def _check_digit(routing: str | None) -> str:
    """The 9th digit of the routing number (ABA check digit)."""
    digits = "".join(ch for ch in (routing or "") if ch.isdigit())
    return digits[8] if len(digits) >= 9 else "0"


# --- credit allocation ------------------------------------------------------


def _split_net_pay(net_pay: Decimal, accounts: list) -> list[tuple]:
    """Allocate net pay across an employee's bank accounts.

    Returns a list of (EmployeeBankAccount, amount) tuples. Honors deposit_type
    in priority order; the REMAINDER account receives whatever is left after
    FIXED / PERCENT / FULL splits.
    """
    net_pay = _q(net_pay)
    ordered = sorted(
        accounts,
        key=lambda a: (a.deposit_type == DepositType.REMAINDER, a.priority or 0),
    )

    allocations: list[tuple] = []
    remaining = net_pay
    remainder_acct = None

    for acct in ordered:
        if acct.deposit_type == DepositType.REMAINDER:
            remainder_acct = acct
            continue
        if acct.deposit_type == DepositType.FULL:
            amount = remaining
        elif acct.deposit_type == DepositType.FIXED:
            amount = _q(acct.deposit_value)
        elif acct.deposit_type == DepositType.PERCENT:
            pct = _q(acct.deposit_value) / Decimal("100")
            amount = _q(net_pay * pct)
        else:
            amount = Decimal("0")

        if amount > remaining:
            amount = remaining
        if amount > 0:
            allocations.append((acct, amount))
            remaining = _q(remaining - amount)

    if remainder_acct is not None and remaining > 0:
        allocations.append((remainder_acct, remaining))

    return allocations


# --- record builders --------------------------------------------------------


def _file_header(originating: dict, created: date) -> str:
    rec = (
        "1"  # record type
        + "01"
        + " "
        + _num(originating.get("immediate_destination"), 9)
        + " "
        + _num(originating.get("immediate_origin"), 9)
        + created.strftime("%y%m%d")
        + "0000"  # file creation time
        + "A"  # file id modifier
        + _num(RECORD_LENGTH, 3)  # record size
        + _num(BLOCKING_FACTOR, 2)  # blocking factor
        + "1"  # format code
        + _alpha(originating.get("destination_name"), 23)
        + _alpha(originating.get("origin_name"), 23)
        + _alpha("", 8)  # reference code
    )
    return rec[:RECORD_LENGTH].ljust(RECORD_LENGTH)


def _batch_header(
    originating: dict, effective_date: date, batch_number: int, entry_desc: str
) -> str:
    rec = (
        "5"
        + SCC_MIXED
        + _alpha(originating.get("company_name"), 16)
        + _alpha("", 20)  # company discretionary data
        + _alpha(originating.get("company_id"), 10)
        + SEC_CODE
        + _alpha(entry_desc, 10)
        + _alpha("", 6)  # company descriptive date
        + effective_date.strftime("%y%m%d")
        + _alpha("", 3)  # settlement date (bank fills)
        + "1"  # originator status code
        + _num(originating.get("originating_dfi_id"), 8)
        + _num(batch_number, 7)
    )
    return rec[:RECORD_LENGTH].ljust(RECORD_LENGTH)


def _entry_detail(
    txn_code: str,
    routing: str,
    account_number: str,
    amount_cents: int,
    individual_id: str,
    individual_name: str,
    originating_dfi: str,
    trace_seq: int,
) -> str:
    rec = (
        "6"
        + txn_code
        + _routing_prefix(routing)
        + _check_digit(routing)
        + _alpha(account_number, 17)
        + _num(amount_cents, 10)
        + _alpha(individual_id, 15)
        + _alpha(individual_name, 22)
        + _alpha("", 2)  # discretionary data
        + "0"  # addenda record indicator
        + _num(originating_dfi, 8)
        + _num(trace_seq, 7)  # trace number
    )
    return rec[:RECORD_LENGTH].ljust(RECORD_LENGTH)


def _batch_control(
    entry_count: int,
    entry_hash: int,
    total_debit: int,
    total_credit: int,
    originating: dict,
    batch_number: int,
) -> str:
    rec = (
        "8"
        + SCC_MIXED
        + _num(entry_count, 6)
        + _num(entry_hash, 10)
        + _num(total_debit, 12)
        + _num(total_credit, 12)
        + _alpha(originating.get("company_id"), 10)
        + _alpha("", 19)  # message authentication code
        + _alpha("", 6)  # reserved
        + _num(originating.get("originating_dfi_id"), 8)
        + _num(batch_number, 7)
    )
    return rec[:RECORD_LENGTH].ljust(RECORD_LENGTH)


def _file_control(
    batch_count: int,
    block_count: int,
    entry_count: int,
    entry_hash: int,
    total_debit: int,
    total_credit: int,
) -> str:
    rec = (
        "9"
        + _num(batch_count, 6)
        + _num(block_count, 6)
        + _num(entry_count, 8)
        + _num(entry_hash, 10)
        + _num(total_debit, 12)
        + _num(total_credit, 12)
        + _alpha("", 39)  # reserved
    )
    return rec[:RECORD_LENGTH].ljust(RECORD_LENGTH)


def _padding_record() -> str:
    return "9" * RECORD_LENGTH


def _assemble(
    header: str,
    batch_header: str,
    entries: list[str],
    batch_control: str,
    file_control: str,
) -> str:
    """Join all records and 9-fill pad to a multiple of the blocking factor."""
    records = [header, batch_header] + entries + [batch_control, file_control]
    while len(records) % BLOCKING_FACTOR != 0:
        records.append(_padding_record())
    return "\n".join(records)


# --- public API -------------------------------------------------------------


def generate_nacha_file(db: Session, pay_run_id: int, originating: dict) -> str:
    """Generate a NACHA ACH credit file for a processed pay run.

    Credits each employee's net pay to their active bank account(s), honoring
    split-deposit configuration. Accounts in a PENDING prenote window or
    flagged inactive are skipped. Returns the file as a single string.
    """
    pay_run = (
        db.query(PayRun)
        .options(joinedload(PayRun.stubs).joinedload(PayStub.employee))
        .filter(PayRun.id == pay_run_id)
        .first()
    )
    if pay_run is None:
        raise ValueError(f"Pay run {pay_run_id} not found")
    if pay_run.status != PayRunStatus.PROCESSED:
        raise ValueError(
            f"Pay run {pay_run_id} is not processed (status={pay_run.status})"
        )

    effective_date = pay_run.pay_date or date.today()
    created = date.today()
    originating_dfi = "".join(
        ch for ch in str(originating.get("originating_dfi_id") or "") if ch.isdigit()
    )

    entries: list[str] = []
    entry_hash = 0
    total_credit = 0
    trace_seq = 0

    for stub in pay_run.stubs:
        employee = stub.employee
        if employee is None:
            continue
        accounts = [
            a
            for a in employee.bank_accounts
            if a.is_active and a.prenote_status != PrenoteStatus.PENDING
        ]
        if not accounts:
            continue

        for acct, amount in _split_net_pay(stub.net_pay, accounts):
            routing = decrypt(acct.routing_number_enc) or ""
            account_number = decrypt(acct.account_number_enc) or ""
            txn_code = (
                TXN_SAVINGS_CREDIT
                if acct.account_kind == BankAccountKind.SAVINGS
                else TXN_CHECKING_CREDIT
            )
            amount_cents = _cents(amount)
            trace_seq += 1
            entries.append(
                _entry_detail(
                    txn_code,
                    routing,
                    account_number,
                    amount_cents,
                    str(employee.id),
                    employee.full_name,
                    originating_dfi,
                    trace_seq,
                )
            )
            entry_hash += int(_routing_prefix(routing))
            total_credit += amount_cents

    # The debit side: a single offsetting debit to the company account funds
    # all the credits. Banks typically expect a balanced file.
    if entries:
        trace_seq += 1
        entries.append(
            _entry_detail(
                "27",  # checking debit (offsetting company account)
                str(originating.get("immediate_destination") or ""),
                str(originating.get("company_account") or ""),
                total_credit,
                str(originating.get("company_id") or ""),
                originating.get("company_name") or "",
                originating_dfi,
                trace_seq,
            )
        )
        entry_hash += int(
            _routing_prefix(str(originating.get("immediate_destination") or ""))
        )

    total_debit = total_credit
    # Entry hash is truncated to its rightmost 10 digits.
    entry_hash = entry_hash % (10**10)
    entry_count = len(entries)

    header = _file_header(originating, created)
    batch_header = _batch_header(originating, effective_date, 1, "PAYROLL")
    batch_control = _batch_control(
        entry_count,
        entry_hash,
        total_debit,
        total_credit,
        originating,
        1,
    )

    # Block count: total records rounded up to a multiple of the blocking factor.
    raw_count = 2 + entry_count + 2  # header + batch header + entries + 2 controls
    block_count = -(-raw_count // BLOCKING_FACTOR)
    file_control = _file_control(
        1,
        block_count,
        entry_count,
        entry_hash,
        total_debit,
        total_credit,
    )

    return _assemble(header, batch_header, entries, batch_control, file_control)


def generate_prenote_file(
    db: Session, employee_bank_account_ids: list[int], originating: dict
) -> str:
    """Generate a NACHA file of zero-dollar prenote entries.

    Prenotes validate routing/account numbers before the first real deposit.
    Uses prenote transaction codes (23 = checking, 33 = savings).
    """
    accounts = (
        db.query(EmployeeBankAccount)
        .options(joinedload(EmployeeBankAccount.employee))
        .filter(EmployeeBankAccount.id.in_(employee_bank_account_ids or []))
        .all()
    )

    created = date.today()
    effective_date = created
    originating_dfi = "".join(
        ch for ch in str(originating.get("originating_dfi_id") or "") if ch.isdigit()
    )

    entries: list[str] = []
    entry_hash = 0
    trace_seq = 0

    for acct in accounts:
        routing = decrypt(acct.routing_number_enc) or ""
        account_number = decrypt(acct.account_number_enc) or ""
        txn_code = (
            TXN_SAVINGS_PRENOTE
            if acct.account_kind == BankAccountKind.SAVINGS
            else TXN_CHECKING_PRENOTE
        )
        employee = acct.employee
        ind_id = str(employee.id) if employee else str(acct.employee_id)
        ind_name = employee.full_name if employee else (acct.nickname or "")
        trace_seq += 1
        entries.append(
            _entry_detail(
                txn_code,
                routing,
                account_number,
                0,
                ind_id,
                ind_name,
                originating_dfi,
                trace_seq,
            )
        )
        entry_hash += int(_routing_prefix(routing))

    entry_hash = entry_hash % (10**10)
    entry_count = len(entries)

    header = _file_header(originating, created)
    batch_header = _batch_header(originating, effective_date, 1, "PRENOTE")
    batch_control = _batch_control(
        entry_count,
        entry_hash,
        0,
        0,
        originating,
        1,
    )

    raw_count = 2 + entry_count + 2
    block_count = -(-raw_count // BLOCKING_FACTOR)
    file_control = _file_control(
        1,
        block_count,
        entry_count,
        entry_hash,
        0,
        0,
    )

    return _assemble(header, batch_header, entries, batch_control, file_control)
