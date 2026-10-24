# ============================================================================
# Opening YTD balances (#433) — the one place the readers that count an
# opening balance get it from, and the one place one is written.
#
# Readers opt in: employee_ytd and wage_bases (the paycheck's caps), the
# stub YTD, the 941, 940, W-2/W-3, the state SUI report, the tax liability
# schedule and the benefit accumulators call the helpers below. Benefit
# remittances, the pay-run list, NACHA and the payroll journal never do, so
# they cannot count the old system's payroll twice.
#
# The amounts are by calendar quarter (QuickBooks' YTD adjustments are
# entered the same way): a quarter's 941 carries only that quarter's
# figures, and the year's readers sum every quarter. The wage bases come
# from the old system's own taxable-wage figures (Social Security, Medicare,
# FUTA, and SUTA per state), never from its gross.
# ============================================================================

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session, selectinload

from app.models.benefits import BenefitCode
from app.models.payroll import PayRun, PayRunStatus, PayStub
from app.models.payroll_opening import (
    OPENING_AMOUNT_FIELDS,
    OPENING_STATE_FIELDS,
    PayrollOpeningBalance,
    PayrollOpeningBenefit,
    PayrollOpeningQuarter,
    PayrollOpeningStateBalance,
)
from app.services.accounting import _q
from app.services.payroll_service import (
    FUTA_WAGE_BASE,
    ss_wage_base,
    wage_base_note,
)

ZERO = Decimal("0")
STATE_FIELDS = OPENING_STATE_FIELDS
BENEFIT_FIELDS = ("employee_amount", "employer_amount")
REASON_MAX = 300
_STATE_RE = re.compile(r"^[A-Z]{2}$")


class OpeningError(Exception):
    """A refused write: the status the route answers and the sentence."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


# ---------------------------------------------------------------------------
# Readers
# ---------------------------------------------------------------------------
def opening_for(db: Session, employee_id: int, year: int):
    """The employee's opening balance for the year, or None."""
    return (
        db.query(PayrollOpeningBalance)
        .options(
            selectinload(PayrollOpeningBalance.quarters),
            selectinload(PayrollOpeningBalance.states),
            selectinload(PayrollOpeningBalance.benefits),
        )
        .filter(
            PayrollOpeningBalance.employee_id == employee_id,
            PayrollOpeningBalance.year == year,
        )
        .first()
    )


def openings_for_year(db: Session, year: int) -> dict:
    """Every opening balance for the year, keyed by employee id."""
    rows = (
        db.query(PayrollOpeningBalance)
        .options(
            selectinload(PayrollOpeningBalance.quarters),
            selectinload(PayrollOpeningBalance.states),
            selectinload(PayrollOpeningBalance.benefits),
            selectinload(PayrollOpeningBalance.employee),
        )
        .filter(PayrollOpeningBalance.year == year)
        .all()
    )
    return {r.employee_id: r for r in rows}


def counts_before(opening, before: date | None) -> bool:
    """Whether the opening is part of the YTD measured before `before`
    (a pay date): it is unless it is dated on or after it."""
    if opening is None:
        return False
    return before is None or opening.as_of < before


def quarter_of(d: date) -> int:
    return (d.month - 1) // 3 + 1


def _d(value) -> Decimal:
    return Decimal(str(value or 0))


def _in(row, quarters) -> bool:
    return quarters is None or row.quarter in quarters


def amount(opening, field: str, quarters=None) -> Decimal:
    """One federal column summed over the opening's quarters (all of them,
    or those in `quarters`)."""
    if opening is None:
        return ZERO
    return sum(
        (_d(getattr(q, field)) for q in opening.quarters if _in(q, quarters)), ZERO
    )


def state_total(opening, field: str, quarters=None, state: str | None = None):
    """One state column summed over the opening's state rows (every state,
    or one; every quarter, or those in `quarters`)."""
    if opening is None:
        return ZERO
    return sum(
        (
            _d(getattr(s, field))
            for s in opening.states
            if _in(s, quarters) and (state is None or s.state == state)
        ),
        ZERO,
    )


def state_codes(opening) -> list[str]:
    return sorted({s.state for s in opening.states}) if opening else []


def wage_bases(opening, work_state: str | None) -> dict:
    """What the old system already counted toward each wage base: Social
    Security, Medicare (the Additional Medicare threshold), FUTA, and for
    `work_state` its SUTA wages and its state wages (the state's own
    capped taxes, e.g. disability or paid leave). The SUTA base is a
    state's own: wages paid in another state do not fill it.

    An opening with no state rows says nothing per state; its gross then
    stands in for the employee's work state, as one stub's would."""
    state = (work_state or "").strip().upper()
    if not opening.states:
        home = (
            (opening.employee.work_state if opening.employee else None) or "WA"
        ).upper()
        gross = amount(opening, "gross") if state == home else ZERO
        suta = state_wages = gross
    else:
        suta = state_total(opening, "suta_wages", state=state)
        state_wages = state_total(opening, "wages", state=state)
    return {
        "ss": amount(opening, "ss_wages"),
        "medicare": amount(opening, "medicare_wages"),
        "futa": amount(opening, "futa_wages"),
        "suta": suta,
        "state": state_wages,
    }


def first_processed_pay_date(db: Session, employee_id: int, year: int):
    """The earliest processed pay run in the year that paid the employee:
    from then on the opening balance is locked (a change is a correction)."""
    row = (
        db.query(PayRun.pay_date)
        .join(PayStub, PayStub.pay_run_id == PayRun.id)
        .filter(
            PayStub.employee_id == employee_id,
            PayRun.status == PayRunStatus.PROCESSED,
            PayRun.pay_date >= date(year, 1, 1),
            PayRun.pay_date <= date(year, 12, 31),
        )
        .order_by(PayRun.pay_date)
        .first()
    )
    return row[0] if row else None


def lock_message(first: date) -> str:
    return (
        f"Locked: a pay run dated {first.isoformat()} is processed; changes "
        "are corrections with a reason."
    )


# ---------------------------------------------------------------------------
# Snapshots, for the response and the audit trail
# ---------------------------------------------------------------------------
def _quarter_values(opening, conv) -> list[dict]:
    out = []
    for q in opening.quarters:
        row = {"quarter": q.quarter}
        row.update({f: conv(getattr(q, f)) for f in OPENING_AMOUNT_FIELDS})
        row["states"] = [
            {"state": s.state, **{f: conv(getattr(s, f)) for f in STATE_FIELDS}}
            for s in opening.states
            if s.quarter == q.quarter
        ]
        out.append(row)
    return out


def _exact(value) -> str:
    return str(_q(_d(value)))


def snapshot(opening) -> dict | None:
    """The opening balance as plain values (strings for money, exact)."""
    if opening is None:
        return None
    out = {
        "employee_id": opening.employee_id,
        "year": opening.year,
        "as_of": opening.as_of.isoformat() if opening.as_of else None,
        "note": opening.note,
    }
    for f in OPENING_AMOUNT_FIELDS:
        out[f] = str(_q(amount(opening, f)))
    out["quarters"] = _quarter_values(opening, _exact)
    out["benefits"] = [
        {
            "benefit_code_id": b.benefit_code_id,
            **{f: _exact(getattr(b, f)) for f in BENEFIT_FIELDS},
        }
        for b in opening.benefits
    ]
    return out


def serialize(opening) -> dict:
    """The opening balance for the API: money as numbers, like the YTD.
    The year's totals at the top (each state summed over its quarters),
    the quarters as entered under `quarters`."""
    out = {
        "id": opening.id,
        "employee_id": opening.employee_id,
        "employee_name": opening.employee.full_name if opening.employee else None,
        "year": opening.year,
        "as_of": opening.as_of.isoformat(),
        "note": opening.note,
    }
    for f in OPENING_AMOUNT_FIELDS:
        out[f] = float(amount(opening, f))
    out["states"] = [
        {
            "state": st,
            **{f: float(state_total(opening, f, state=st)) for f in STATE_FIELDS},
        }
        for st in state_codes(opening)
    ]
    out["quarters"] = _quarter_values(opening, lambda v: float(_d(v)))
    out["benefits"] = [
        {
            "benefit_code_id": b.benefit_code_id,
            "code": b.benefit_code.code if b.benefit_code else None,
            "name": b.benefit_code.name if b.benefit_code else None,
            **{f: float(getattr(b, f) or 0) for f in BENEFIT_FIELDS},
        }
        for b in opening.benefits
    ]
    return out


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------
def _money(value, label: str, errors: list) -> Decimal:
    if value is None or value == "":
        return ZERO
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError):
        errors.append(f"{label} is not an amount: {value!r}.")
        return ZERO
    if not d.is_finite():
        errors.append(f"{label} is not an amount: {value!r}.")
        return ZERO
    if d < 0:
        errors.append(f"{label} cannot be negative ({d}).")
    return _q(d)


def _clean_states(states, quarter: int, errors: list) -> list[dict]:
    out = []
    seen: set[str] = set()
    for s in states or []:
        code = str(s.get("state") or "").strip().upper()
        if not _STATE_RE.match(code):
            errors.append(
                f"Q{quarter}: state {s.get('state')!r} is not a two-letter state code."
            )
            continue
        if code in seen:
            errors.append(f"Q{quarter}: state {code} is listed twice.")
            continue
        seen.add(code)
        out.append(
            {
                "quarter": quarter,
                "state": code,
                **{
                    f: _money(s.get(f), f"Q{quarter} {code} {f}", errors)
                    for f in STATE_FIELDS
                },
            }
        )
    return out


def clean_payload(db: Session, data: dict) -> tuple[dict, list[str]]:
    """Check an opening balance and put it in one shape: Decimals, a
    quarter that is not after as_of's, upper-case state codes, benefit
    codes that exist. Returns the clean payload and the reasons it is
    refused (empty when it is not)."""
    errors: list[str] = []
    year = data.get("year")
    as_of = data.get("as_of")
    if not isinstance(year, int):
        errors.append("year is required.")
    if not isinstance(as_of, date):
        errors.append("as_of (the day before the first SlowBooks pay run) is required.")
        as_of = None
    elif isinstance(year, int) and as_of.year != year:
        errors.append(f"as_of {as_of.isoformat()} is not in {year}.")
    clean = {"year": year, "as_of": as_of, "note": (data.get("note") or None)}
    if clean["note"] and len(clean["note"]) > 300:
        clean["note"] = clean["note"][:300]

    last = quarter_of(as_of) if as_of else 4
    clean["quarters"] = []
    clean["states"] = []
    seen_quarters: set[int] = set()
    quarters = data.get("quarters") or []
    if not quarters:
        errors.append(
            "quarters is required: what the old system paid in each quarter "
            "up to as_of (a May switch has Q1 and Q2)."
        )
    for q in quarters:
        n = q.get("quarter")
        if not isinstance(n, int) or not 1 <= n <= 4:
            errors.append(f"Quarter {n!r} is not 1 to 4.")
            continue
        if n in seen_quarters:
            errors.append(f"Q{n} is listed twice.")
            continue
        seen_quarters.add(n)
        if n > last:
            errors.append(
                f"Q{n} is after as_of {as_of.isoformat()} (Q{last}): the old "
                "system paid nothing after the switch."
            )
            continue
        row = {"quarter": n}
        for f in OPENING_AMOUNT_FIELDS:
            row[f] = _money(q.get(f), f"Q{n} {f}", errors)
        if row["net"] > row["gross"]:
            errors.append(
                f"Q{n} net ({row['net']}) cannot be more than gross ({row['gross']})."
            )
        clean["quarters"].append(row)
        clean["states"].extend(_clean_states(q.get("states"), n, errors))
    clean["quarters"].sort(key=lambda r: r["quarter"])

    # The old system's taxable wages are capped wages: more than the base
    # is a typing slip, and would leave the paychecks and the forms apart.
    # The Social Security base is the opening's own year's; a year SlowBooks
    # has no base for is checked against the nearest one, and says so
    # (clean["warnings"]) rather than refusing the switch.
    tax_year = year if isinstance(year, int) else None
    clean["warnings"] = [n for n in (wage_base_note(tax_year),) if n]
    for field, base, label in (
        ("ss_wages", ss_wage_base(tax_year), "Social Security"),
        ("futa_wages", FUTA_WAGE_BASE, "FUTA"),
    ):
        total = sum((r[field] for r in clean["quarters"]), ZERO)
        if total > base:
            when = f"{tax_year} " if tax_year and label != "FUTA" else ""
            errors.append(
                f"{label} wages for the year ({total}) are more than the "
                f"{when}{label} wage base ({base}); enter the taxable wages, "
                "not gross."
            )

    clean["benefits"] = []
    seen_codes: set[int] = set()
    for b in data.get("benefits") or []:
        code_id = b.get("benefit_code_id")
        code = (
            db.query(BenefitCode).filter(BenefitCode.id == code_id).first()
            if isinstance(code_id, int)
            else None
        )
        if code is None:
            errors.append(f"Benefit code {code_id!r} does not exist.")
            continue
        if code.id in seen_codes:
            errors.append(f"Benefit code {code.code} is listed twice.")
            continue
        seen_codes.add(code.id)
        clean["benefits"].append(
            {
                "benefit_code_id": code.id,
                **{
                    f: _money(b.get(f), f"{code.code} {f}", errors)
                    for f in BENEFIT_FIELDS
                },
            }
        )
    return clean, errors


def plain(clean: dict) -> dict:
    """A clean payload as JSON-friendly values (the import plan shows it)."""
    states_by_q: dict[int, list] = {}
    for s in clean.get("states") or []:
        states_by_q.setdefault(s["quarter"], []).append(
            {"state": s["state"], **{f: float(s[f]) for f in STATE_FIELDS}}
        )
    quarters = []
    for q in clean.get("quarters") or []:
        row = {"quarter": q["quarter"]}
        row.update({f: float(q[f]) for f in OPENING_AMOUNT_FIELDS})
        row["states"] = states_by_q.get(q["quarter"], [])
        quarters.append(row)
    totals = {
        f: float(sum((q[f] for q in clean.get("quarters") or []), ZERO))
        for f in OPENING_AMOUNT_FIELDS
    }
    return {
        **totals,
        "quarters": quarters,
        "benefits": [
            {
                "benefit_code_id": b["benefit_code_id"],
                **{f: float(b[f]) for f in BENEFIT_FIELDS},
            }
            for b in clean.get("benefits") or []
        ],
    }


def as_of_refusal(clean_as_of: date, first: date | None, year: int) -> str | None:
    """Why as_of cannot stand: an opening balance is what was paid before
    SlowBooks, so it ends before the first processed pay run. A correction
    that moved it to or past that run would drop the opening out of the
    YTD that run (and every later one) was figured from."""
    if first is None or clean_as_of < first:
        return None
    return (
        f"as_of {clean_as_of.isoformat()} must be before the first processed "
        f"pay run of {year} ({first.isoformat()}): an opening balance is what "
        "was paid before SlowBooks."
    )


def check_reason(reason: str | None) -> str | None:
    reason = (reason or "").strip() or None
    if reason and len(reason) > REASON_MAX:
        raise OpeningError(
            422, f"The reason is {len(reason)} characters; at most {REASON_MAX}."
        )
    return reason


def save_opening(
    db: Session, employee_id: int, data: dict, reason: str | None = None
) -> tuple[PayrollOpeningBalance, str]:
    """Create or replace the employee's opening balance for the year.
    Returns the row and "create" or "replace"; raises OpeningError. Does
    not commit.

    Once a pay run in the year is processed for the employee the figures
    have been counted by it, so a change needs a reason, and the reason is
    written to the audit trail beside the before and after (CORRECT). A
    draft run that counted the old figures is flagged (flag_drafts)."""
    reason = check_reason(reason)
    clean, errors = clean_payload(db, data)
    if errors:
        raise OpeningError(422, " ".join(errors))
    year = clean["year"]
    existing = opening_for(db, employee_id, year)
    first = first_processed_pay_date(db, employee_id, year)
    if first is not None:
        refusal = as_of_refusal(clean["as_of"], first, year)
        if refusal:
            raise OpeningError(422, refusal)
        if not reason:
            raise OpeningError(409, lock_message(first))
    before = snapshot(existing)

    row = existing or PayrollOpeningBalance(employee_id=employee_id, year=year)
    row.as_of = clean["as_of"]
    row.note = clean["note"]
    if existing is None:
        db.add(row)
    else:
        # cleared and flushed before the new rows go in, so a quarter, a
        # state or a code kept from before does not meet its own unique
        # constraint
        row.quarters.clear()
        row.states.clear()
        row.benefits.clear()
    db.flush()
    for q in clean["quarters"]:
        row.quarters.append(PayrollOpeningQuarter(**q))
    for s in clean["states"]:
        row.states.append(PayrollOpeningStateBalance(**s))
    for b in clean["benefits"]:
        row.benefits.append(PayrollOpeningBenefit(**b))
    db.flush()
    db.refresh(row)

    if first is not None and reason:
        from app.services.audit import log_event

        log_event(
            db,
            "payroll_opening_balances",
            row.id,
            "CORRECT",
            old_values=before,
            new_values={**snapshot(row), "reason": reason},
        )
    if before != snapshot(row):
        flag_drafts(db, employee_id, year)
    return row, ("create" if existing is None else "replace")


def delete_opening(
    db: Session, opening: PayrollOpeningBalance, reason: str | None = None
) -> None:
    """Remove an opening balance, under the same lock as a change. Does
    not commit."""
    reason = check_reason(reason)
    first = first_processed_pay_date(db, opening.employee_id, opening.year)
    if first is not None and not reason:
        raise OpeningError(409, lock_message(first))
    if first is not None:
        from app.services.audit import log_event

        log_event(
            db,
            "payroll_opening_balances",
            opening.id,
            "CORRECT",
            old_values=snapshot(opening),
            new_values={"deleted": True, "reason": reason},
        )
    employee_id, year = opening.employee_id, opening.year
    db.delete(opening)
    db.flush()
    flag_drafts(db, employee_id, year)


def flag_drafts(db: Session, employee_id: int, year: int) -> list[int]:
    """Mark every draft run in the year that pays the employee: it was
    figured from the opening balance as it stood, so its Social Security,
    Medicare, FUTA and SUTA wage bases (and benefit caps) may be wrong now.
    Processing it asks first (runs.process_pay_run). Returns the run ids."""
    from app.models.payroll import Employee

    drafts = (
        db.query(PayRun)
        .join(PayStub, PayStub.pay_run_id == PayRun.id)
        .filter(
            PayStub.employee_id == employee_id,
            PayRun.status == PayRunStatus.DRAFT,
            PayRun.pay_date >= date(year, 1, 1),
            PayRun.pay_date <= date(year, 12, 31),
        )
        .distinct()
        .all()
    )
    if not drafts:
        return []
    emp = db.query(Employee).filter(Employee.id == employee_id).first()
    name = emp.full_name if emp else f"Employee {employee_id}"
    for run in drafts:
        names = run_stale_names(run)
        if name not in names:
            names.append(name)
        run.stale_reason = stale_message(names, year)[:500]
    db.flush()
    return [r.id for r in drafts]


_STALE_PREFIX = "Figured before the opening YTD changed for "


def stale_message(names: list[str], year: int) -> str:
    return (
        f"{_STALE_PREFIX}{', '.join(names)} ({year}): its Social Security, "
        "Medicare, FUTA and SUTA wage bases came from the old figures. "
        "Create the run again to use the new ones."
    )


def run_stale_names(run) -> list[str]:
    text = run.stale_reason or ""
    if not text.startswith(_STALE_PREFIX):
        return []
    names = text[len(_STALE_PREFIX) :].split(" (", 1)[0]
    return [n for n in names.split(", ") if n]


def refresh_benefit_ytd(db: Session, year: int) -> None:
    """Rebuild the year's benefit accumulators so the annual caps count
    the opening's benefit amounts; commits."""
    from app.services import benefits_engine

    benefits_engine.rebuild_ytd(db, year)
    db.commit()
