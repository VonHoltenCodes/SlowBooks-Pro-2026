# ============================================================================
# Purchase postings — where a line on a bill, a PO turning into a bill, or a
# vendor credit posts, and what the sales tax on it does. One place, so the
# three documents can't drift apart.
#
# Accounts. A line posts to the account chosen on it, else its item's expense
# account, else the vendor's default expense account. Nothing further: the old
# last resort was account number "6000", which the seeded chart names
# Advertising & Marketing, so a sign shop's panels and a bakery's flour were
# booked as advertising and the P&L showed no cost of goods (2.17.3
# exploratory: skytech W-H5, macbase1 F8). A line nothing names is refused
# with a sentence that says what to choose.
#
# Tax. Sales tax a supplier charges is part of what the purchase cost — a US
# business can't reclaim it — so it is spread over the lines it was charged
# on, in proportion to their amounts, and posts with them (to the expense,
# cost-of-goods or inventory account each line already uses). It never
# touches 2200 Sales Tax Payable, which holds the tax the business collected
# from its own customers and owes the state: debiting it with tax paid to a
# supplier made Pay Sales Tax offer $0.33 where $59.57 was owed (macbase1 F9).
# ============================================================================

from decimal import Decimal
from typing import Optional, Sequence

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.accounts import Account
from app.services.accounting import _q, quantize_to

COST_Q = Decimal("0.0001")  # unit costs carry four places (InventoryMovement)


def spread(total, amounts: Sequence) -> list[Decimal]:
    """Split `total` over `amounts` in proportion, exact to the cent.

    Each share is first cut down to whole cents, then the cents still left
    go one each to the shares that lost the most in the cut (ties to the
    larger amount, then the earlier line). The shares always add up to
    `total`, and none is negative — which "round each share and give the
    remainder to one line" can't promise: $0.06 over ten $1 lines and a
    1-cent line rounds every $1 share up to a cent and leaves -$0.04 for
    the line that absorbs the difference.

    Amounts of zero or less take no share. Returns one Decimal per amount.
    """
    weights = [Decimal(str(a or 0)) for a in amounts]
    total = _q(total)
    denom = sum((w for w in weights if w > 0), Decimal("0"))
    if total == 0 or denom <= 0:
        return [Decimal("0.00") for _ in weights]
    sign = 1 if total > 0 else -1
    cents = int(abs(total) * 100)
    exact = [(cents * w / denom) if w > 0 else Decimal("0") for w in weights]
    shares = [int(x) for x in exact]  # whole cents, rounded down
    leftover = cents - sum(shares)
    order = sorted(
        (i for i, w in enumerate(weights) if w > 0),
        key=lambda i: (-(exact[i] - shares[i]), -weights[i], i),
    )
    for i in order[:leftover]:
        shares[i] += 1
    return [_q(Decimal(sign * s) / 100) for s in shares]


def unit_cost_with_tax(amount, tax_share, quantity, rate) -> Decimal:
    """What one unit of a stocked line cost once its share of the tax is in.

    The ledger debits Inventory with the line amount plus its tax share, so
    the inventory movement has to carry the same cost or the stock valuation
    (quantity x average cost) stops agreeing with account 1300."""
    qty = Decimal(str(quantity or 0))
    share = Decimal(str(tax_share or 0))
    if share == 0 or qty <= 0:
        return Decimal(str(rate))
    return quantize_to((Decimal(str(amount)) + share) / qty, COST_Q)


def _line_label(line_no: int, description: Optional[str]) -> str:
    desc = (description or "").strip()
    if len(desc) > 40:
        desc = desc[:39].rstrip() + "…"
    return f"Line {line_no} ({desc})" if desc else f"Line {line_no}"


def expense_account_for(
    db: Session,
    *,
    line_no: int,
    description: Optional[str],
    account_id: Optional[int],
    item,
    vendor,
    fix_hint: str = "Choose an account on the line",
) -> int:
    """The account a non-stock purchase line posts to.

    The account chosen on the line, else the item's expense account, else
    the vendor's default expense account. When none of them names one the
    line is refused (400) — never booked to a guessed account."""
    if account_id:
        if db.get(Account, account_id) is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{_line_label(line_no, description)} names an account that "
                    "isn't in the chart of accounts. Choose another account. "
                    "Nothing was saved."
                ),
            )
        return account_id
    if item is not None and item.expense_account_id:
        return item.expense_account_id
    if vendor is not None and vendor.default_expense_account_id:
        return vendor.default_expense_account_id
    who = f"{vendor.name} " if vendor is not None and vendor.name else "the vendor "
    raise HTTPException(
        status_code=400,
        detail=(
            f"{_line_label(line_no, description)} has no account to post to. "
            f"{fix_hint}, or give {who}a default expense account in the "
            "Vendor Center. Nothing was saved."
        ),
    )
