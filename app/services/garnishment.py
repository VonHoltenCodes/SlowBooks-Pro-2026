# ============================================================================
# Garnishment Service — CCPA wage-garnishment limits and multi-order priority
# ----------------------------------------------------------------------------
# Implements the federal Consumer Credit Protection Act (Title III) caps on how
# much of an employee's pay may be withheld for garnishment orders, plus the
# priority ordering used when several orders compete for the same paycheck.
#
# Plain dataclasses keep the engine ORM-independent and easy to unit-test:
# callers translate their own models into GarnishmentSpec objects and read back
# GarnishmentResult objects.
#
# DISCLAIMER: These are SIMPLIFIED federal rules. Many states impose stricter
# limits (lower percentage caps, higher protected-earnings floors, different
# priority among order types). Verify against current federal and state law
# before relying on this for actual payroll.
# ============================================================================

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP

CENT = Decimal("0.01")

# Order in which garnishment types are satisfied from disposable earnings.
# Lower number = higher priority (processed first).
_TYPE_PRIORITY: dict[str, int] = {
    "child_support": 0,
    "federal_levy": 1,
    "bankruptcy": 1,
    "state_tax_levy": 2,
    "student_loan": 3,
    "creditor": 4,
}

# CCPA ordinary-creditor cap and the protected-earnings multiplier (30x the
# federal minimum wage per workweek is exempt from ordinary garnishment).
_CREDITOR_CAP_PERCENT = Decimal("0.25")
_PROTECTED_HOURS_PER_WEEK = Decimal("30")

# Administrative wage garnishment for a single student-loan order.
_STUDENT_LOAN_CAP_PERCENT = Decimal("0.15")

# CCPA 25% aggregate cap for everything other than child support.
_NON_SUPPORT_AGGREGATE_PERCENT = Decimal("0.25")


def _q(value) -> Decimal:
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _non_negative(value: Decimal) -> Decimal:
    """Clamp negatives up to zero."""
    return value if value > Decimal("0") else Decimal("0")


@dataclass
class GarnishmentSpec:
    order_id: int
    garnishment_type: str   # one of: child_support, federal_levy, student_loan,
                            #         bankruptcy, creditor, state_tax_levy
    calc_method: str        # "fixed" (a dollar amount) or
                            # "percent_disposable" (a percent of disposable)
    amount: Decimal         # dollars when fixed; percent 0-100 when percent
    priority: int = 0       # lower number = applied first (ties: order_id)
    supports_secondary_family: bool = False  # True -> 50% base cap, else 60%
    in_arrears_12_weeks: bool = False        # adds 5% to the child-support cap


@dataclass
class GarnishmentResult:
    order_id: int
    garnishment_type: str
    requested: Decimal      # amount the order asked for
    amount: Decimal         # amount actually garnished after CCPA caps
    capped: bool            # True if a CCPA limit reduced the requested amount
    note: str = ""          # short human explanation


def compute_disposable_earnings(gross: Decimal,
                                mandatory_withholding: Decimal) -> Decimal:
    """Disposable earnings = gross pay minus legally-required deductions.

    Legally-required deductions are federal/state/local income tax, Social
    Security, and Medicare; their sum is passed as mandatory_withholding.
    Voluntary deductions (401k, insurance) are NOT subtracted. Floored at 0.
    """
    if not isinstance(gross, Decimal):
        gross = Decimal(str(gross))
    if not isinstance(mandatory_withholding, Decimal):
        mandatory_withholding = Decimal(str(mandatory_withholding))
    return _q(_non_negative(gross - mandatory_withholding))


def _requested_amount(spec: GarnishmentSpec, disposable: Decimal) -> Decimal:
    """Resolve an order's requested amount before any CCPA cap."""
    amount = spec.amount
    if not isinstance(amount, Decimal):
        amount = Decimal(str(amount))
    method = (spec.calc_method or "").lower()
    if method == "percent_disposable":
        requested = amount / Decimal("100") * disposable
    else:  # "fixed"
        requested = amount
    return _q(_non_negative(requested))


def _sort_key(spec: GarnishmentSpec) -> tuple:
    """Sort key: type priority, then the spec's priority field, then order_id."""
    type_rank = _TYPE_PRIORITY.get((spec.garnishment_type or "").lower(), 99)
    return (type_rank, spec.priority, spec.order_id)


def _child_support_cap_percent(spec: GarnishmentSpec) -> Decimal:
    """CCPA child-support cap as a fraction of disposable earnings."""
    base = Decimal("0.50") if spec.supports_secondary_family else Decimal("0.60")
    if spec.in_arrears_12_weeks:
        base += Decimal("0.05")
    return base


def apply_garnishments(disposable: Decimal, specs: list[GarnishmentSpec],
                       weeks_in_period: int = 2,
                       federal_min_wage: Decimal = Decimal("7.25")
                       ) -> list[GarnishmentResult]:
    """Apply garnishment orders against disposable earnings under CCPA limits.

    Orders are processed in priority order (child support, then federal levy /
    bankruptcy, then state tax levy, then student loan, then ordinary
    creditor). Each result reports the requested amount, the amount actually
    garnished after caps, and whether a limit reduced it.
    """
    if not isinstance(disposable, Decimal):
        disposable = Decimal(str(disposable))
    disposable = _q(_non_negative(disposable))
    if not isinstance(federal_min_wage, Decimal):
        federal_min_wage = Decimal(str(federal_min_wage))
    if weeks_in_period < 1:
        weeks_in_period = 1

    ordered = sorted(specs, key=_sort_key)

    # Pre-compute the shared child-support cap. Multiple child-support orders
    # share one cap; if their requests exceed it they are pro-rated.
    support_specs = [s for s in ordered
                     if (s.garnishment_type or "").lower() == "child_support"]
    support_cap = Decimal("0")
    for spec in support_specs:
        support_cap = max(support_cap,
                          _child_support_cap_percent(spec) * disposable)
    support_cap = _q(support_cap)

    # Ordinary-creditor cap: lesser of 25% of disposable, or disposable minus
    # the protected 30x-minimum-wage-per-week floor. Never below 0.
    creditor_cap_pct = _q(_CREDITOR_CAP_PERCENT * disposable)
    protected = (_PROTECTED_HOURS_PER_WEEK * federal_min_wage
                 * Decimal(str(weeks_in_period)))
    creditor_cap_floor = _q(_non_negative(disposable - protected))
    creditor_cap = min(creditor_cap_pct, creditor_cap_floor)

    # Aggregate 25% cap covers everything except child support.
    non_support_aggregate_cap = _q(_NON_SUPPORT_AGGREGATE_PERCENT * disposable)

    remaining = disposable           # disposable still available
    support_used = Decimal("0")      # child-support total so far
    non_support_used = Decimal("0")  # non-child-support total so far

    # Total child support requested, for pro-rating against the shared cap.
    support_requested_total = Decimal("0")
    for spec in support_specs:
        support_requested_total += _requested_amount(spec, disposable)

    results: list[GarnishmentResult] = []

    for spec in ordered:
        gtype = (spec.garnishment_type or "").lower()
        requested = _requested_amount(spec, disposable)
        allowed = requested
        notes: list[str] = []

        if gtype == "child_support":
            # Share the support cap; pro-rate if all support orders exceed it.
            support_room = _non_negative(support_cap - support_used)
            if support_requested_total > support_cap and support_cap > 0:
                share = _q(requested / support_requested_total * support_cap)
                if share < allowed:
                    allowed = share
                    notes.append("pro-rated against shared child-support cap")
            if allowed > support_room:
                allowed = support_room
                notes.append("child-support cap reached")

        elif gtype in ("federal_levy", "state_tax_levy"):
            # Tax levies are not bound by the 25% rule; only by remaining pay.
            if allowed > remaining:
                allowed = remaining
                notes.append("capped at remaining disposable earnings")

        elif gtype == "bankruptcy":
            # Court-ordered Chapter 13; honor request, capped at remaining pay.
            if allowed > remaining:
                allowed = remaining
                notes.append("capped at remaining disposable earnings")

        elif gtype == "student_loan":
            # Administrative wage garnishment: 15% of disposable per order.
            loan_cap = _q(_STUDENT_LOAN_CAP_PERCENT * disposable)
            if allowed > loan_cap:
                allowed = loan_cap
                notes.append("capped at 15% of disposable earnings")
            aggregate_room = _non_negative(
                non_support_aggregate_cap - non_support_used)
            if allowed > aggregate_room:
                allowed = aggregate_room
                notes.append("25% aggregate non-support cap reached")

        else:  # ordinary creditor
            creditor_room = _non_negative(creditor_cap - non_support_used)
            if allowed > creditor_room:
                allowed = creditor_room
                notes.append("CCPA 25% creditor cap reached")
            aggregate_room = _non_negative(
                non_support_aggregate_cap - non_support_used)
            if allowed > aggregate_room:
                allowed = aggregate_room
                notes.append("25% aggregate non-support cap reached")

        # Final backstop: never garnish more than the disposable still left.
        if allowed > remaining:
            allowed = remaining
            notes.append("capped at remaining disposable earnings")

        allowed = _q(_non_negative(allowed))
        remaining = _q(_non_negative(remaining - allowed))
        if gtype == "child_support":
            support_used = _q(support_used + allowed)
        else:
            non_support_used = _q(non_support_used + allowed)

        capped = allowed < requested
        results.append(GarnishmentResult(
            order_id=spec.order_id,
            garnishment_type=spec.garnishment_type,
            requested=requested,
            amount=allowed,
            capped=capped,
            note="; ".join(notes),
        ))

    return results


def total_garnished(results: list[GarnishmentResult]) -> Decimal:
    """Sum of all amounts actually garnished across the given results."""
    total = Decimal("0")
    for result in results:
        total += result.amount
    return _q(total)
