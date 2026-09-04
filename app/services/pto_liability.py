# ============================================================================
# PTO dollar liability — accrual books a liability as hours are earned and
# relieves it when they're taken, so a bank carries hours AND dollars.
# ----------------------------------------------------------------------------
# The two diverge when wage rates change. The policy's `valuation` decides
# how relief and revaluation are priced:
#   current_rate  hours × the employee's rate today. Relief at today's rate;
#                 `revalue()` restates the whole bank at today's rate (the
#                 liability moves on raises — the ideal from the design doc).
#   average_rate  the bank's own dollars ÷ hours (historical cost). Relief
#                 never over- or under-relieves what was accrued.
# Companies handle this in widely varied ways, so it is a policy choice,
# not an assumption. Postings only happen when accrue_liability is on.
# ============================================================================

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from app.models.accounts import Account
from app.models.payroll import Employee, PayType
from app.models.pto import PTOAccrual, PTOPolicy
from app.services.accounting import _q, create_journal_entry

ZERO = Decimal("0")
ANNUAL_HOURS = Decimal("2080")


def _d(v) -> Decimal:
    return v if isinstance(v, Decimal) else Decimal(str(v or 0))


def employee_hourly_rate(emp: Employee) -> Decimal:
    """The wage rate PTO is valued at: the hourly rate, or salary ÷ 2080."""
    rate = _d(emp.pay_rate)
    if emp.pay_type == PayType.SALARY:
        return (rate / ANNUAL_HOURS).quantize(Decimal("0.0001"))
    return rate


def _accounts(db: Session, policy: PTOPolicy) -> tuple[Optional[int], Optional[int]]:
    def by_number(n):
        a = db.query(Account).filter(Account.account_number == n).first()
        return a.id if a else None

    expense = policy.expense_account_id or by_number("6160") or by_number("6110")
    liability = policy.liability_account_id or by_number("2390") or by_number("2300")
    return expense, liability


def _post(
    db: Session,
    policy: PTOPolicy,
    when: date,
    memo: str,
    amount: Decimal,
    accrue: bool,
    accrual: PTOAccrual,
):
    """DR expense / CR liability when accruing (amount > 0), the reverse
    when relieving. Skips silently when the policy doesn't book dollars."""
    amount = _q(amount)
    if not policy.accrue_liability or amount == 0:
        return None
    expense, liability = _accounts(db, policy)
    if not expense or not liability:
        raise ValueError(
            f"PTO policy '{policy.name}' books a liability but has no expense / "
            "liability account and 6160 / 2390 are missing — run "
            "Benefits → Create default accounts or set them on the policy"
        )
    if amount < 0:
        amount = -amount
        accrue = not accrue
    dr, cr = (expense, liability) if accrue else (liability, expense)
    return create_journal_entry(
        db,
        when,
        memo,
        [
            {"account_id": dr, "debit": amount, "credit": ZERO, "description": memo},
            {"account_id": cr, "debit": ZERO, "credit": amount, "description": memo},
        ],
        source_type="pto",
        source_id=accrual.id,
    )


def unit_value(policy: PTOPolicy, accrual: PTOAccrual, emp: Employee) -> Decimal:
    """Dollars per hour for relief / valuation under the policy."""
    hours = _d(accrual.balance)
    if (policy.valuation or "current_rate") == "average_rate" and hours > 0:
        return (_d(accrual.dollar_balance) / hours).quantize(Decimal("0.0001"))
    return employee_hourly_rate(emp)


def accrue_dollars(
    db: Session,
    policy: PTOPolicy,
    accrual: PTOAccrual,
    emp: Employee,
    hours_added: Decimal,
    when: date,
) -> Decimal:
    """Value newly earned hours at the current rate, add to the bank,
    post DR PTO expense / CR accrued PTO liability."""
    hours_added = _d(hours_added)
    if hours_added <= 0:
        return ZERO
    dollars = _q(hours_added * employee_hourly_rate(emp))
    accrual.dollar_balance = _q(_d(accrual.dollar_balance) + dollars)
    _post(
        db,
        policy,
        when,
        f"PTO accrual — {emp.full_name} — {policy.name}",
        dollars,
        True,
        accrual,
    )
    return dollars


def relieve_dollars(
    db: Session,
    policy: PTOPolicy,
    accrual: PTOAccrual,
    emp: Employee,
    hours_used: Decimal,
    when: date,
    memo: str = None,
) -> Decimal:
    """Take hours out of the bank at the policy's unit value, never more
    than the dollars in it; post DR liability / CR PTO expense (the pay run
    expenses the wages, so the earlier accrual is unwound here)."""
    hours_used = _d(hours_used)
    if hours_used <= 0:
        return ZERO
    dollars = min(
        _q(hours_used * unit_value(policy, accrual, emp)), _d(accrual.dollar_balance)
    )
    if dollars <= 0:
        return ZERO
    accrual.dollar_balance = _q(_d(accrual.dollar_balance) - dollars)
    _post(
        db,
        policy,
        when,
        memo or f"PTO taken — {emp.full_name} — {policy.name}",
        dollars,
        False,
        accrual,
    )
    return dollars


def revalue(
    db: Session, policy: PTOPolicy, accrual: PTOAccrual, emp: Employee, when: date
) -> Decimal:
    """Restate the bank at hours × the employee's current rate (raises move
    the liability). Posts the difference. Returns the adjustment."""
    target = _q(_d(accrual.balance) * employee_hourly_rate(emp))
    diff = _q(target - _d(accrual.dollar_balance))
    if diff == 0:
        return ZERO
    accrual.dollar_balance = target
    _post(
        db,
        policy,
        when,
        f"PTO revaluation — {emp.full_name} — {policy.name}",
        diff,
        True,
        accrual,
    )
    return diff


def forfeit_dollars(
    db: Session,
    policy: PTOPolicy,
    accrual: PTOAccrual,
    emp: Employee,
    hours_forfeited: Decimal,
    when: date,
) -> Decimal:
    """Year-end carryover cap: hours above the cap are forfeited, and their
    share of the dollars comes off the liability (DR liability / CR expense)."""
    hours_forfeited = _d(hours_forfeited)
    if hours_forfeited <= 0:
        return ZERO
    return relieve_dollars(
        db,
        policy,
        accrual,
        emp,
        hours_forfeited,
        when,
        memo=f"PTO carryover forfeiture — {emp.full_name} — {policy.name}",
    )
