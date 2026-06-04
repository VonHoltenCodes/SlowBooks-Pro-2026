# ============================================================================
# PTO Accrual Service — compute paid-time-off accrual, balances, and carryover
# ----------------------------------------------------------------------------
# Pure functions operate on plain Decimal values so they are trivial to test;
# thin wrappers read PTOPolicy ORM objects (see app/models/pto.py).
#
# Accrual methods (AccrualMethod enum values):
#   per_hour_worked -> accrued = hours_worked * rate (WA sick = 1/40 = 0.025)
#   per_pay_period  -> accrued = rate (fixed hours each pay run)
#   annual_grant    -> accrued = rate (full lump grant; caller times the grant)
#
# DISCLAIMER: Simplified model. Real PTO rules vary by jurisdiction, policy,
# and employment status. WA paid-sick-leave figures follow the state mandate
# (1 hr per 40 hrs worked, carryover capped at 40 hrs) but verify current law.
# ============================================================================

from decimal import Decimal, ROUND_HALF_UP

CENT = Decimal("0.01")

# WA paid-sick-leave mandate: 1 hour accrued per 40 hours worked.
WA_SICK_ACCRUAL_RATE: Decimal = Decimal("0.025")  # 1 / 40
# WA caps the unused paid-sick balance carried into the new year at 40 hours.
WA_SICK_CARRYOVER_CAP: Decimal = Decimal("40")


def _q(value) -> Decimal:
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _non_negative(value) -> Decimal:
    """Coerce to Decimal and clamp negatives up to zero."""
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return value if value > 0 else Decimal("0")


def accrual_for_period(
    accrual_method: str, accrual_rate: Decimal, hours_worked: Decimal = Decimal("0")
) -> Decimal:
    """Hours accrued for a single pay period under the given accrual method.

    accrual_method: one of "per_hour_worked", "per_pay_period", "annual_grant".
    accrual_rate:   interpreted per the method (rate-per-hour, fixed hours, or
                    the full annual grant amount).
    hours_worked:   only used by "per_hour_worked".
    """
    rate = _non_negative(accrual_rate)
    method = (accrual_method or "").lower()

    if method == "per_hour_worked":
        accrued = _non_negative(hours_worked) * rate
    elif method in ("per_pay_period", "annual_grant"):
        accrued = rate
    else:
        accrued = Decimal("0")

    return _q(accrued)


def apply_accrual(
    current_balance: Decimal,
    accrued: Decimal,
    used: Decimal = Decimal("0"),
    max_balance: Decimal | None = None,
) -> Decimal:
    """New balance = current_balance + accrued - used.

    Clamped to >= 0, and to max_balance when a cap is set (None = no cap).
    """
    balance = (
        _non_negative(current_balance) + _non_negative(accrued) - _non_negative(used)
    )
    if balance < 0:
        balance = Decimal("0")
    if max_balance is not None:
        cap = _non_negative(max_balance)
        if balance > cap:
            balance = cap
    return _q(balance)


def apply_carryover(
    year_end_balance: Decimal, max_carryover: Decimal | None
) -> Decimal:
    """Balance carried into the new year, capped at max_carryover when set.

    max_carryover None means unlimited carryover.
    """
    balance = _non_negative(year_end_balance)
    if max_carryover is not None:
        cap = _non_negative(max_carryover)
        if balance > cap:
            balance = cap
    return _q(balance)


def wa_sick_accrual(hours_worked: Decimal) -> Decimal:
    """WA paid-sick-leave mandate accrual: 1 hour per 40 hours worked."""
    return _q(_non_negative(hours_worked) * WA_SICK_ACCRUAL_RATE)


def run_year_end_carryover(db, target_year: int) -> list[dict]:
    """Roll every PTOAccrual into the new year.

    For each accrual:
      - cap `balance` at the policy's `max_carryover` (None = unlimited)
      - reset `accrued_ytd` and `used_ytd` to 0

    Returns a per-row summary so the operator (or an audit log) can see
    exactly what changed. The target_year parameter doesn't filter rows;
    it's required so the caller has to be explicit about which year they're
    closing out.
    """
    from sqlalchemy.orm import joinedload

    from app.models.pto import PTOAccrual

    accruals = db.query(PTOAccrual).options(joinedload(PTOAccrual.policy)).all()
    changes = []
    for acc in accruals:
        old_balance = _non_negative(acc.balance)
        old_accrued = _non_negative(acc.accrued_ytd)
        old_used = _non_negative(acc.used_ytd)
        cap = acc.policy.max_carryover if acc.policy else None
        new_balance = apply_carryover(old_balance, cap)

        acc.balance = new_balance
        acc.accrued_ytd = Decimal("0")
        acc.used_ytd = Decimal("0")

        changes.append(
            {
                "accrual_id": acc.id,
                "employee_id": acc.employee_id,
                "policy_id": acc.policy_id,
                "policy_name": acc.policy.name if acc.policy else None,
                "year_closed": target_year,
                "balance_before": float(old_balance),
                "balance_after": float(new_balance),
                "carryover_cap": (float(cap) if cap is not None else None),
                "capped": old_balance > new_balance,
                "accrued_ytd_reset_from": float(old_accrued),
                "used_ytd_reset_from": float(old_used),
            }
        )

    db.commit()
    return changes


def compute_period_accrual(policy, hours_worked: Decimal = Decimal("0")) -> Decimal:
    """Period accrual for a PTOPolicy ORM object.

    Reads policy.accrual_method (an AccrualMethod enum) and policy.accrual_rate,
    then delegates to accrual_for_period.
    """
    method = policy.accrual_method
    method_value = method.value if hasattr(method, "value") else str(method)
    rate = policy.accrual_rate if policy.accrual_rate is not None else Decimal("0")
    return accrual_for_period(method_value, rate, hours_worked)
