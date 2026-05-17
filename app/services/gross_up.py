# ============================================================================
# Gross-Up Service — net-to-gross ("gross-up") solver
# ----------------------------------------------------------------------------
# Given a desired take-home (net) amount, finds the gross pay that produces it.
# The relationship net = f(gross) is monotonically non-decreasing but has no
# closed form (progressive tax brackets, capped contributions, etc.), so the
# gross is found iteratively by bisection.
#
# The solver is deliberately generic: it does NOT import the payroll
# calculator. Callers pass the net-of-gross function as a callable, which keeps
# this module pure and trivial to unit-test.
# ============================================================================

from decimal import Decimal, ROUND_HALF_UP
from typing import Callable

CENT = Decimal("0.01")


def _q(value) -> Decimal:
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _as_decimal(value) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def gross_up(target_net: Decimal,
             net_of_gross: Callable[[Decimal], Decimal],
             tolerance: Decimal = Decimal("0.01"),
             max_iterations: int = 80) -> Decimal:
    """Return the gross pay whose resulting net equals target_net.

    net_of_gross(gross) -> net must be monotonically non-decreasing.

    A lower bound starts at target_net (net can never exceed gross), and an
    upper bound is found by doubling until its net reaches target_net. The
    interval is then bisected until the net is within tolerance of the target.
    """
    target_net = _as_decimal(target_net)
    tolerance = _as_decimal(tolerance)
    if target_net <= Decimal("0"):
        return _q(Decimal("0"))

    # Lower bound: net can never exceed gross, so gross >= target_net.
    low = target_net
    if _as_decimal(net_of_gross(low)) >= target_net:
        return _q(low)

    # Upper bound: double until its net reaches (or exceeds) the target.
    high = low if low > Decimal("0") else Decimal("1")
    for _ in range(max_iterations):
        high *= Decimal("2")
        if _as_decimal(net_of_gross(high)) >= target_net:
            break

    # Bisection between low and high.
    guess = high
    for _ in range(max_iterations):
        guess = (low + high) / Decimal("2")
        net = _as_decimal(net_of_gross(guess))
        diff = net - target_net
        if abs(diff) <= tolerance:
            break
        if diff < Decimal("0"):
            low = guess
        else:
            high = guess

    return _q(guess)


def gross_up_detail(target_net,
                    net_of_gross: Callable[[Decimal], Decimal],
                    taxes_of_gross: Callable[[Decimal], Decimal] = None,
                    **kw) -> dict:
    """Solve for gross and report the gross, resulting net, and withholding.

    Extra keyword arguments (tolerance, max_iterations) pass through to
    gross_up. Withholding is taxes_of_gross(gross) when that callable is
    supplied, otherwise the implied difference gross - net.
    """
    gross = gross_up(target_net, net_of_gross, **kw)
    net = _q(_as_decimal(net_of_gross(gross)))
    if taxes_of_gross is not None:
        withholding = _q(_as_decimal(taxes_of_gross(gross)))
    else:
        withholding = _q(gross - net)
    return {"gross": gross, "net": net, "withholding": withholding}
