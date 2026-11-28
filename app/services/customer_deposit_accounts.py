"""Customer deposits and retainers (#1237).

A firm that takes a retainer up front holds it in a liability ("Retainer
Payable") and later applies it to an invoice as a payment deposited to that
liability: DR the liability / CR A/R, no money moves. Settings → Deposits
names the liability accounts used this way (`customer_deposit_account_ids`,
"[]" by default), and Record Payment then offers them in Deposit To.

A payment to one of them must be applied to invoices in full (left over,
it would sit as a customer credit taken out of a liability), and must not
take more than the account holds unless the request says so
(`allow_overdraw`, which the form sends after asking). The server refuses
both itself, so the API cannot do what the form refuses. A payment to any
other account is checked exactly as before, and posting is unchanged.
"""

import json
from decimal import Decimal
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.services.accounting import _q


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
        return "is a control account its own documents keep"
    if acct.id in payroll_ids(db):
        return "is a payroll account"
    return None


def refuse_misuse(
    db: Session,
    account_id: Optional[int],
    amount,
    applied,
    home_amount,
    allow_overdraw: bool,
) -> None:
    """A 422 when a payment deposited to a customer deposit or retainer
    account leaves money unapplied, or (without `allow_overdraw`) takes more
    than the account holds. Nothing for any other account, nor for one on
    the list that no longer qualifies (retyped to a bank, renumbered onto a
    payroll account since it was ticked: it takes ordinary deposits as any
    such account does), nor for an edit keeping its own account (a payment
    saved before the account was ticked still saves as it was)."""
    from app.services.undeposited_funds import _kept_deposit_account

    if account_id is None or account_id not in configured_ids(db):
        return
    if account_id == _kept_deposit_account.get():
        return
    from app.models.accounts import Account

    acct = db.get(Account, account_id)
    if acct is None or not_usable_because(db, acct) is not None:
        return
    left = _q(Decimal(str(amount)) - Decimal(str(applied)))
    if left > 0:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Deposit To is {acct.name}, a customer deposit or retainer "
                f"account, so the whole payment must be applied to invoices; "
                f"${left:,.2f} is not applied. Apply it, or lower the amount. "
                "Nothing was recorded."
            ),
        )
    held = _q(Decimal(str(acct.balance or 0)))
    wanted = _q(Decimal(str(home_amount)))
    if wanted > held and not allow_overdraw:
        raise HTTPException(
            status_code=422,
            detail=(
                f"{acct.name} holds ${held:,.2f}, less than the ${wanted:,.2f} "
                "applied from it. Lower the amount, or confirm applying more "
                "than it holds. Nothing was recorded."
            ),
        )
