# ============================================================================
# Overtime Service — classify worked hours into regular / overtime / doubletime
# ----------------------------------------------------------------------------
# Splits the hours of a workweek into three pay categories per the federal
# Fair Labor Standards Act (FLSA) and a handful of state daily-overtime rules.
#
# DISCLAIMER: These are SIMPLIFIED rules. Real overtime law has many exceptions
# (exempt employees, alternative workweek schedules, collective bargaining
# agreements, salaried non-exempt computations, regular-rate blending, etc.).
# Verify against current federal and state wage-and-hour law before relying on
# this for actual payroll.
# ============================================================================

from decimal import Decimal, ROUND_HALF_UP

CENT = Decimal("0.01")
WEEKLY_THRESHOLD = Decimal("40")
DAILY_OT_THRESHOLD = Decimal("8")
DAILY_DT_THRESHOLD = Decimal("12")

# States with a daily overtime rule (over 8/day OT, over 12/day DT).
_DAILY_RULE_STATES = {"CA", "AK", "NV", "CO"}


def _q(value) -> Decimal:
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _zero_result() -> dict:
    return {
        "regular": _q(Decimal("0")),
        "overtime": _q(Decimal("0")),
        "doubletime": _q(Decimal("0")),
    }


def _classify_flsa(daily_hours: list) -> dict:
    """FLSA / default rule: only hours over 40 in the week are overtime."""
    total = sum((Decimal(str(h)) for h in daily_hours), Decimal("0"))
    if total <= WEEKLY_THRESHOLD:
        regular, overtime = total, Decimal("0")
    else:
        regular, overtime = WEEKLY_THRESHOLD, total - WEEKLY_THRESHOLD
    return {
        "regular": _q(regular),
        "overtime": _q(overtime),
        "doubletime": _q(Decimal("0")),
    }


def _classify_daily(daily_hours: list, seventh_day_rule: bool) -> dict:
    """Daily-then-weekly reconciliation for CA-style states.

    Step 1: per day, hours over 8 are overtime, hours over 12 are doubletime.
            With seventh_day_rule, the 7th consecutive worked day pays the
            first 8 hours as overtime and anything beyond 8 as doubletime.
    Step 2: of the hours still at the straight-time (regular) rate, any excess
            over 40 in the week converts to overtime so nothing double-counts.
    """
    regular = Decimal("0")
    overtime = Decimal("0")
    doubletime = Decimal("0")

    last_index = len(daily_hours) - 1
    for index, raw in enumerate(daily_hours):
        hours = Decimal(str(raw))
        if hours <= 0:
            continue

        is_seventh = seventh_day_rule and index == 6 and last_index >= 6
        if is_seventh:
            # 7th consecutive day: first 8h overtime, beyond 8h doubletime.
            day_ot = min(hours, DAILY_OT_THRESHOLD)
            day_dt = max(hours - DAILY_OT_THRESHOLD, Decimal("0"))
            day_reg = Decimal("0")
        else:
            day_reg = min(hours, DAILY_OT_THRESHOLD)
            day_ot = max(
                min(hours, DAILY_DT_THRESHOLD) - DAILY_OT_THRESHOLD, Decimal("0")
            )
            day_dt = max(hours - DAILY_DT_THRESHOLD, Decimal("0"))

        regular += day_reg
        overtime += day_ot
        doubletime += day_dt

    # Weekly over-40 reconciliation against straight-time hours only.
    if regular > WEEKLY_THRESHOLD:
        overtime += regular - WEEKLY_THRESHOLD
        regular = WEEKLY_THRESHOLD

    return {
        "regular": _q(regular),
        "overtime": _q(overtime),
        "doubletime": _q(doubletime),
    }


def classify_week(daily_hours: list, state: str = "WA") -> dict:
    """Classify one workweek of daily hours into regular / overtime / doubletime.

    daily_hours: Decimal hours per worked day, chronological (index 0 = first
                 worked day), length up to 7.
    state:       two-letter code; selects the applicable overtime rule.

    Returns {"regular", "overtime", "doubletime"} whose values sum to the
    total hours worked. All values quantized to 2 decimals.
    """
    if not daily_hours:
        return _zero_result()

    code = (state or "WA").upper()
    if code in _DAILY_RULE_STATES:
        return _classify_daily(daily_hours, seventh_day_rule=(code == "CA"))
    return _classify_flsa(daily_hours)


def classify_period(weeks: list, state: str = "WA") -> dict:
    """Classify a full pay period: a list of weeks (each a daily_hours list).

    Each week is classified with classify_week; the per-week results are
    summed into a single {"regular", "overtime", "doubletime"} dict.
    """
    totals = {
        "regular": Decimal("0"),
        "overtime": Decimal("0"),
        "doubletime": Decimal("0"),
    }
    if not weeks:
        return _zero_result()

    for week in weeks:
        result = classify_week(week, state)
        for key in totals:
            totals[key] += result[key]

    return {key: _q(value) for key, value in totals.items()}
