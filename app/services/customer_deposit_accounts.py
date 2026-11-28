"""Customer deposits and retainers (#1237).

A firm that takes a retainer up front holds it in a liability ("Retainer
Payable") and later applies it to an invoice as a payment deposited to that
liability: DR the liability / CR A/R, no money moves. Settings → Deposits
names the liability accounts used this way (`customer_deposit_account_ids`,
"[]" by default), and Record Payment then offers them in Deposit To.

A customer payment to one of them, new or edited, must be applied to
invoices in full (left over, it would sit as a customer credit taken out of
a liability), and must not take more than the account holds unless the
request says so (`allow_overdraw`, which the form sends after asking).
`POST /api/payments` and `POST /api/payments/{id}/replace` refuse both
themselves, as the form does. An edit keeping its own account may leave no
more unapplied, and take no more from the account, than the payment it
replaces, so a payment saved before the account was ticked can still be
corrected.

Only those two customer payment routes check. A sales receipt is posted as
it always was (see `not_checked`), and batch payments, importers and the
bank feed write their payments without these checks. A payment to any
other account is checked exactly as before, and posting is unchanged.
"""

import json
from contextlib import contextmanager
from contextvars import ContextVar
from decimal import Decimal
from typing import NamedTuple, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.services.accounting import _q
from app.services.terminology import said


def configured_ids(db: Session) -> set[int]:
    """The ids on the setting; an empty set for none or an unreadable value."""
    from app.services.settings_service import get_setting_raw

    try:
        ids = json.loads(get_setting_raw(db, "customer_deposit_account_ids") or "[]")
    except (TypeError, ValueError):
        return set()
    if not isinstance(ids, list):
        return set()
    out = set()
    for item in ids:
        if isinstance(item, bool):
            continue
        try:
            out.add(int(item))
        except (TypeError, ValueError):
            continue
    return out


# The seeded payroll liabilities a pay run, a benefits posting or a PTO
# accrual finds by number (see payroll_accounts and accounts.py's
# _FOUND_BY_NUMBER): money the company owes for its people, never a
# customer's deposit.
PAYROLL_NUMBERS = frozenset(
    ("2300", "2310", "2320", "2330", "2340", "2350", "2360", "2370", "2380", "2390")
)


def payroll_ids(db: Session) -> set[int]:
    """The accounts payroll posts to: the seeded 23xx payroll liabilities
    and every account a `payroll_*_account_id` setting names."""
    from app.models.accounts import Account
    from app.services import payroll_accounts
    from app.services.settings_service import get_setting_raw

    out = {
        acct_id
        for (acct_id,) in db.query(Account.id).filter(
            Account.account_number.in_(sorted(PAYROLL_NUMBERS))
        )
    }
    for piece in payroll_accounts.PIECES:
        acct_id = payroll_accounts.parse_id(get_setting_raw(db, piece.key) or "")
        if acct_id is not None:
            out.add(acct_id)
    return out


def not_usable_because(db: Session, acct) -> Optional[str]:
    """Why `acct` cannot hold customer deposits and retainers ("is not a
    liability account" and the rest), or None when it can. Being inactive
    is a separate matter the caller decides."""
    from app.services import control_accounts

    kind = getattr(acct.account_type, "value", acct.account_type)
    if kind != "liability":
        return "is not a liability account"
    if acct.bank_kind:
        return "is a bank or credit card account"
    if control_accounts.is_control(acct.account_number, acct.account_type, acct.name):
        return "is a control account, which the app posts to on its own"
    if acct.id in payroll_ids(db):
        return "is a payroll account"
    return None


class Kept(NamedTuple):
    """The payment an edit replaces: its Deposit To, the amount it left
    unapplied, and its home-currency amount."""

    account_id: Optional[int]
    unapplied: Decimal
    home_amount: Decimal


# The payment being edited (`keeping_payment`); None for a new payment.
_kept_payment: ContextVar[Optional[Kept]] = ContextVar("_kept_payment", default=None)
# False while a sales receipt posts its payment (`not_checked`).
_checking: ContextVar[bool] = ContextVar("_checking", default=True)


@contextmanager
def keeping_payment(payment):
    """While open, `refuse_misuse` weighs a replacement keeping `payment`'s
    own Deposit To against `payment` itself (None: nothing kept)."""
    kept = None
    if payment is not None:
        applied = sum(
            (Decimal(str(a.amount)) for a in payment.allocations), Decimal("0")
        )
        rate = Decimal(str(payment.exchange_rate or 1))
        kept = Kept(
            payment.deposit_to_account_id,
            max(_q(Decimal(str(payment.amount)) - applied), Decimal("0")),
            _q(Decimal(str(payment.amount)) * rate),
        )
    token = _kept_payment.set(kept)
    try:
        yield
    finally:
        _kept_payment.reset(token)


@contextmanager
def not_checked():
    """While open, `refuse_misuse` checks nothing: a sales receipt posts its
    payment as it did before the setting."""
    token = _checking.set(False)
    try:
        yield
    finally:
        _checking.reset(token)


def _money(value: Decimal) -> str:
    """$1,234.56, and -$100.00 for a negative amount."""
    return f"-${-value:,.2f}" if value < 0 else f"${value:,.2f}"


def refuse_misuse(
    db: Session,
    account_id: Optional[int],
    amount,
    applied,
    home_amount,
    allow_overdraw: bool,
) -> None:
    """A 422 when a customer payment deposited to a customer deposit or
    retainer account leaves money unapplied, or (without `allow_overdraw`)
    takes more than the account holds. Nothing for any other account, nor
    for one on the list that no longer qualifies (retyped to a bank,
    renumbered onto a payroll account since it was ticked: it takes ordinary
    deposits as any such account does), nor inside `not_checked`.

    An edit keeping its own account (`keeping_payment`) is refused only for
    leaving more unapplied than the payment it replaces, and asked only when
    it takes more than both the account holds and the replaced payment took:
    a memo fixed on a payment saved before the account was ticked still
    saves. The replaced payment is voided first, so what the account holds
    already includes its money back."""
    if not _checking.get():
        return
    if account_id is None or account_id not in configured_ids(db):
        return
    from app.models.accounts import Account

    acct = db.get(Account, account_id)
    if acct is None or not_usable_because(db, acct) is not None:
        return
    kept = _kept_payment.get()
    if kept is not None and kept.account_id != account_id:
        kept = None
    left = _q(Decimal(str(amount)) - Decimal(str(applied)))
    if left > (kept.unapplied if kept else 0):
        raise HTTPException(
            status_code=422,
            # the account's name and the amount are data (said): a
            # nonprofit's "Customer Deposits" keeps its own name
            detail=said(
                "Deposit To is {}, a customer deposit or retainer account, so "
                "the whole payment must be applied to invoices; {} is not "
                "applied. Apply it, or lower the amount. Nothing was recorded.",
                acct.name,
                _money(left),
            ),
        )
    held = _q(Decimal(str(acct.balance or 0)))
    wanted = _q(Decimal(str(home_amount)))
    if kept is not None and wanted <= kept.home_amount:
        return
    if wanted > held and not allow_overdraw:
        raise HTTPException(
            status_code=422,
            detail=said(
                "{} holds {}, less than the {} applied from it. Lower the "
                "amount, or confirm applying more than it holds. Nothing was "
                "recorded.",
                acct.name,
                _money(held),
                _money(wanted),
            ),
        )
