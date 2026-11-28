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
    than the account holds. Nothing for any other account."""
    if account_id is None or account_id not in configured_ids(db):
        return
    from app.models.accounts import Account

    acct = db.get(Account, account_id)
    if acct is None:
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
                "applied from it. Lower the amount, or send allow_overdraw to "
                "apply it anyway. Nothing was recorded."
            ),
        )
