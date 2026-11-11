# ============================================================================
# Purchase postings — where a line on a bill, a PO turning into a bill, or a
# vendor credit posts, and what the sales tax on it does. One place, so the
# three documents can't drift apart.
#
# Accounts. A line posts to the account chosen on it, else its item's expense
# account, else (construction mode, a line with a cost code or cost type) the
# code's account or its cost type's default account, else the vendor's
# default expense account. Nothing further: the old
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
#
# Use tax (construction). On a job whose contractor pays the tax on its
# materials (the job's Sales tax setting), a material line on a bill the
# vendor charged no sales tax on owes use tax: the bill's own journal adds DR
# the line's job-cost account (job, section, cost type) / CR 2220 Use Tax
# Payable, at the company's use-tax rate. A line's Use tax box accrues it on
# anything else; voiding or editing the bill reverses or re-posts it with
# the rest of the journal. The rate is kept on the bill line when the tax
# first accrues (BillLine.use_tax_rate), and every re-post uses it: changing
# the setting later never moves a bill already booked.
#
# Controls (#785). A bill, quick entry, memorized bill, expense or card
# charge line may post to a balance-sheet account (a loan, an escrow, an
# owner's equity, #766), never to Accounts Receivable, Accounts Payable,
# Undeposited Funds or Inventory: the documents that own those post to them.
# A bill line on 1200 turned up in Make Deposits as a payment to deposit, and
# one on 1300 raised the Balance Sheet's Inventory with no item received
# (#785). A bill made from a purchase order (To Bill) is a new bill and is
# refused the same way. A line already saved on one of them keeps it through
# an edit that leaves the line's account alone; a bookkeeper moves it. A
# purchase order itself and vendor credits resolve as they did.

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


# the controls a purchase line may not post to: number -> where such a
# posting belongs, said after the account's name
PURCHASE_LINE_CONTROLS = {
    "1100": "which only invoices and customer payments post to",
    "2000": "which only bills and bill payments post to",
    "1200": "which holds customer payments until they are deposited",
    "1300": "which only an inventory item's purchases and sales post to",
}


def refuse_control_account(account: Account, label: str) -> None:
    """Refuse (422) a purchase line posting to Accounts Receivable, Accounts
    Payable, Undeposited Funds or Inventory, in one sentence that names the
    account, where such a posting belongs, and what to do. `label` starts
    the sentence ("Line 2 (Deposit)", "The card charge")."""
    from app.services.control_accounts import is_control

    number = account.account_number
    where = PURCHASE_LINE_CONTROLS.get(str(number)) if number else None
    if where is None or not is_control(number, account.account_type):
        return
    fix = (
        "put the item on the line, or choose another account"
        if str(number) == "1300"
        else "choose another account"
    )
    raise HTTPException(
        status_code=422,
        detail=f"{label} can't post to {number} {account.name}, {where}; {fix}.",
    )


def expense_account_for(
    db: Session,
    *,
    line_no: int,
    description: Optional[str],
    account_id: Optional[int],
    item,
    vendor,
    fix_hint: str = "Choose an account on the line",
    cost_code_id: Optional[int] = None,
    cost_type: Optional[str] = None,
    refuse_controls: bool = False,
    kept_account_id: Optional[int] = None,
) -> int:
    """The account a non-stock purchase line posts to.

    The account chosen on the line, else the item's expense account, else —
    for a construction company, on a line with a cost code or cost type — the
    code's account or the cost type's default account, else the vendor's
    default expense account. When none of them names one the line is refused
    (400) — never booked to a guessed account. A Business or Nonprofit
    company's lines resolve as they always have.

    `refuse_controls` (a bill's lines): an account chosen on the line that is
    one of the controls (PURCHASE_LINE_CONTROLS) is refused, unless it is
    `kept_account_id`, the account an edited line was already saved on."""
    if account_id:
        acct = db.get(Account, account_id)
        if acct is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{_line_label(line_no, description)} names an account that "
                    "isn't in the chart of accounts. Choose another account. "
                    "Nothing was saved."
                ),
            )
        if refuse_controls and account_id != kept_account_id:
            refuse_control_account(acct, _line_label(line_no, description))
        return account_id
    if item is not None and item.expense_account_id:
        return item.expense_account_id
    if cost_code_id or cost_type:
        from app.services.job_costing import job_cost_account_for
        from app.services.terminology import terms_from_db

        if terms_from_db(db).is_construction:
            job_acct = job_cost_account_for(db, cost_code_id, cost_type)
            if job_acct:
                return job_acct
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


def use_tax_rate(db: Session) -> Decimal:
    """The company's use-tax rate as a fraction: the Use tax rate setting, else
    the default tax rate (both percents, 6.5 = 6.5%)."""
    from app.services.settings_service import get_all_settings

    settings = get_all_settings(db)
    pct = (settings.get("use_tax_rate") or "").strip() or (
        settings.get("default_tax_rate") or "0"
    )
    try:
        return Decimal(pct) / Decimal("100")
    except ArithmeticError:
        return Decimal("0")


def use_tax_applies(
    db: Session,
    *,
    amount,
    job_id: Optional[int],
    cost_type: Optional[str],
    bill_taxed: bool,
    use_tax: Optional[bool] = None,
) -> bool:
    """Whether one purchase line owes use tax at all, whatever the rate is.
    Construction only. A line's own `use_tax` wins (True accrues, False
    never); left unset, a material line on a "contractor pays" job accrues
    when the vendor charged no sales tax on the bill (`bill_taxed` false)."""
    from app.models.jobs import Job
    from app.services.contracts import contractor_pays_tax
    from app.services.terminology import terms_from_db

    amount = _q(Decimal(str(amount or 0)))
    if amount <= 0 or use_tax is False or not terms_from_db(db).is_construction:
        return False
    if use_tax is not True:
        if bill_taxed or cost_type != "material" or not job_id:
            return False
        if not contractor_pays_tax(db.get(Job, job_id)):
            return False
    return True


def use_tax_for(
    db: Session,
    *,
    amount,
    job_id: Optional[int],
    cost_type: Optional[str],
    bill_taxed: bool,
    use_tax: Optional[bool] = None,
    rate: Optional[Decimal] = None,
) -> Decimal:
    """The use tax one purchase line accrues, 0 for none (see use_tax_applies
    for who owes it), at `rate` (the rate the line was booked at) or else
    the company's use-tax rate."""
    if not use_tax_applies(
        db,
        amount=amount,
        job_id=job_id,
        cost_type=cost_type,
        bill_taxed=bill_taxed,
        use_tax=use_tax,
    ):
        return Decimal("0")
    if rate is None:
        rate = use_tax_rate(db)
    return _q(_q(Decimal(str(amount or 0))) * Decimal(str(rate)))


NO_USE_TAX_RATE = (
    "Use tax is not accrued on this bill: no Use tax rate or Default tax rate is "
    "set, so the use tax on its materials is $0.00. Set the Use tax rate in "
    "Settings, then save the bill again."
)


def use_tax_rate_warnings(db: Session, bill) -> list[str]:
    """A sentence when a saved bill has a line that owes use tax and the
    company's use-tax rate is 0, so nothing was accrued and the books would
    never show it. Empty for any bill that accrued, or owes none."""
    if use_tax_rate(db) > 0:
        return []
    from app.models.items import Item

    for ln in bill.lines:
        if ln.use_tax_rate:
            continue  # accrued at the rate it was booked at
        item = db.get(Item, ln.item_id) if ln.item_id else None
        if item is not None and item.track_inventory:
            continue
        if use_tax_applies(
            db,
            amount=ln.amount,
            job_id=ln.job_id or bill.job_id,
            cost_type=ln.cost_type,
            bill_taxed=Decimal(str(bill.tax_amount or 0)) > 0,
            use_tax=ln.use_tax,
        ):
            return [NO_USE_TAX_RATE]
    return []


def use_tax_lines(
    db: Session,
    debit_line: dict,
    accrual: Decimal,
    job_id: Optional[int],
    rate: Optional[Decimal] = None,
) -> list[dict]:
    """The two journal lines that accrue `accrual` of use tax on a purchase
    line: DR the line's own account and dimensions (`debit_line`, the line's
    cost debit), CR 2220 Use Tax Payable, both with the job. The memo ends
    with the rate it was booked at (`rate`, else today's setting). Nothing
    when there is no accrual."""
    if not accrual:
        return []
    from app.services.accounting import ensure_construction_accounts

    payable = ensure_construction_accounts(db)["2220"]
    # The rate rides on the memo, so the Sales Tax report can show the rate
    # each accrual was booked at, not today's setting.
    from app.services.estimating import fmt_pct

    if rate is None:
        rate = use_tax_rate(db)
    tag = f" ({fmt_pct(Decimal(str(rate)) * 100)}%)"
    memo = (
        f"Use tax - {debit_line.get('description') or 'materials'}"[: 200 - len(tag)]
        + tag
    )
    return [
        {
            **debit_line,
            "debit": accrual,
            "credit": Decimal("0"),
            "description": memo,
            "job_id": job_id,
            "is_billable": False,
        },
        {
            "account_id": payable.id,
            "debit": Decimal("0"),
            "credit": accrual,
            "description": memo,
            "job_id": job_id,
        },
    ]
