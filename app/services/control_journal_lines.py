# ============================================================================
# Journal lines on Accounts Receivable and Accounts Payable that name the
# customer or vendor (#897).
#
# The A/R and A/P agings, the customer and vendor balances, and Books Check
# read documents: invoices, payments and credit memos; bills, bill payments
# and vendor credits. QuickBooks also lets a journal entry put a line on
# A/R or A/P when the line names the customer or vendor, and an IIF or
# QuickBooks Online import carries those lines across. Here they are read as
# what QuickBooks reads them as, a receivable or payable item of that
# customer or vendor dated the entry's date, so an imported book ties: the
# aging, the balance and the control account agree.
#
# How a line names its party, read from what the ledger already keeps:
#
#   - A/R: the line's own customer (TransactionLine.customer_id), the name
#     on a register line (#606) and on an imported journal line;
#   - A/P: the entry's vendor (Transaction.vendor_id), the payee a register
#     entry names (#491) and the vendor an imported entry names. A ledger
#     line carries no vendor of its own, so an entry's A/P lines are one
#     vendor's.
#
# A line that names nobody stays out of both: it is the Books Check
# difference it always was, named with where it belongs. The postings of the
# subledger's own documents (and their voids) are never read here, and an
# entry voided by its reversal is left out together with the reversal.
# ============================================================================

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable, Optional

from sqlalchemy.orm import Session

from app.models.transactions import Transaction, TransactionLine
from app.services import control_accounts
from app.services.accounting import _q

ZERO = Decimal("0")

# The source types (prefixes: "invoice" covers invoice, invoice_edit and
# invoice_void) whose postings ARE the subledger's documents.
AR_DOCUMENTS = ("invoice", "payment", "credit_memo", "late_fee")
AP_DOCUMENTS = ("bill", "vendor_credit")  # "bill" covers bill_payment


@dataclass(frozen=True)
class ControlLine:
    """One entry's net on a control account for one party (None: nameless),
    in the account's natural direction (A/R a debit, A/P a credit, positive)."""

    transaction: Transaction
    party_id: Optional[int]
    amount: Decimal

    @property
    def date(self) -> date:
        return self.transaction.date


def _is_document(txn: Transaction, documents: tuple[str, ...]) -> bool:
    source = txn.source_type or ""
    return source.startswith(documents) or source.endswith("_void")


def _account(db: Session, number: str) -> Optional[int]:
    acct_id = control_accounts.find(db, number)
    if acct_id is None:
        return None
    from app.models.accounts import Account

    acct = db.get(Account, acct_id)
    if not control_accounts.is_control(acct.account_number, acct.account_type):
        return None
    return acct_id


def _lines(
    db: Session,
    account_id: int,
    documents: tuple[str, ...],
    debit_normal: bool,
    party_of,
    as_of: Optional[date],
    named_only: bool,
) -> list[ControlLine]:
    from app.services.voids import voided_transaction_ids

    q = (
        db.query(TransactionLine, Transaction)
        .join(Transaction, TransactionLine.transaction_id == Transaction.id)
        .filter(TransactionLine.account_id == account_id)
    )
    if as_of is not None:
        q = q.filter(Transaction.date <= as_of)
    if named_only:
        q = q.filter(_NAMED[party_of])
    nets: dict[tuple[int, Optional[int]], Decimal] = defaultdict(lambda: ZERO)
    txns: dict[int, Transaction] = {}
    for line, txn in q.order_by(Transaction.date, Transaction.id, TransactionLine.id):
        if _is_document(txn, documents):
            continue
        txns[txn.id] = txn
        dr, cr = Decimal(str(line.debit or 0)), Decimal(str(line.credit or 0))
        nets[(txn.id, party_of(line, txn))] += (dr - cr) if debit_normal else (cr - dr)
    if not nets:
        return []
    voided = voided_transaction_ids(db, list(txns))
    out = []
    for (tid, party), amount in nets.items():
        amount = _q(amount)
        if amount == ZERO or tid in voided:
            continue
        out.append(ControlLine(txns[tid], party, amount))
    out.sort(key=lambda c: (c.date, c.transaction.id, c.party_id or 0))
    return out


def _customer_of(line, txn):
    return line.customer_id


def _vendor_of(line, txn):
    return txn.vendor_id


_NAMED = {
    _customer_of: TransactionLine.customer_id.isnot(None),
    _vendor_of: Transaction.vendor_id.isnot(None),
}


def receivable_lines(
    db: Session, as_of: Optional[date] = None, named_only: bool = False
) -> list[ControlLine]:
    """Every entry on Accounts Receivable that is not a receivable document's
    posting, netted per customer it names (None for the nameless part)."""
    ar_id = _account(db, control_accounts.RECEIVABLE)
    if ar_id is None:
        return []
    return _lines(db, ar_id, AR_DOCUMENTS, True, _customer_of, as_of, named_only)


def payable_lines(
    db: Session, as_of: Optional[date] = None, named_only: bool = False
) -> list[ControlLine]:
    """Every entry on Accounts Payable that is not a payable document's
    posting, netted per vendor it names (None when it names none)."""
    ap_id = _account(db, control_accounts.PAYABLE)
    if ap_id is None:
        return []
    return _lines(db, ap_id, AP_DOCUMENTS, False, _vendor_of, as_of, named_only)


def by_party(
    lines: Iterable[ControlLine], party_ids: Optional[Iterable[int]] = None
) -> dict[int, Decimal]:
    """The named lines summed per party (only `party_ids`, when given)."""
    wanted = None if party_ids is None else {int(i) for i in party_ids}
    out: dict[int, Decimal] = defaultdict(lambda: ZERO)
    for c in lines:
        if c.party_id is None or (wanted is not None and c.party_id not in wanted):
            continue
        out[c.party_id] += c.amount
    return dict(out)


def aging_bucket(as_of: date, on: date) -> str:
    """The aging column a named line lands in: by its entry date, as
    QuickBooks ages a journal line that has no due date."""
    days = (as_of - on).days
    if days <= 0:
        return "current"
    if days <= 30:
        return "over_30"
    if days <= 60:
        return "over_60"
    return "over_90"


def total(lines: Iterable[ControlLine]) -> Decimal:
    return _q(sum((c.amount for c in lines), ZERO))
