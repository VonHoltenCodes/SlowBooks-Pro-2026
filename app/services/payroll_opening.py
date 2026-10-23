# ============================================================================
# Opening YTD balances (#433) — the one place the readers that count an
# opening balance get it from, and the one place one is written.
#
# Readers opt in: employee_ytd (and through it the wage bases and the stub
# YTD), the 941, 940, W-2/W-3 and the benefit accumulators call the helpers
# below. Deposits, remittances, the pay-run list, NACHA and the payroll
# journal never do, so they cannot count the old system's payroll twice.
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
    PayrollOpeningBalance,
    PayrollOpeningBenefit,
    PayrollOpeningStateBalance,
)
from app.services.accounting import _q

ZERO = Decimal("0")
STATE_FIELDS = ("wages", "withholding", "other_employee", "other_employer")
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


def amount(opening, field: str) -> Decimal:
    return Decimal(str(getattr(opening, field) or 0))


def state_total(opening, field: str) -> Decimal:
    """One state column summed over the opening's state rows."""
    return sum((Decimal(str(getattr(s, field) or 0)) for s in opening.states), ZERO)


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
    out["states"] = [
        {
            "state": s.state,
            **{f: str(_q(Decimal(str(getattr(s, f) or 0)))) for f in STATE_FIELDS},
        }
        for s in opening.states
    ]
    out["benefits"] = [
        {
            "benefit_code_id": b.benefit_code_id,
            **{f: str(_q(Decimal(str(getattr(b, f) or 0)))) for f in BENEFIT_FIELDS},
        }
        for b in opening.benefits
    ]
    return out


def serialize(opening) -> dict:
    """The opening balance for the API: money as numbers, like the YTD."""
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
        {"state": s.state, **{f: float(getattr(s, f) or 0) for f in STATE_FIELDS}}
        for s in opening.states
    ]
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


def clean_payload(db: Session, data: dict) -> tuple[dict, list[str]]:
    """Check an opening balance and put it in one shape: Decimals, an
    upper-case state code, a benefit code that exists. Returns the clean
    payload and the reasons it is refused (empty when it is not)."""
    errors: list[str] = []
    year = data.get("year")
    as_of = data.get("as_of")
    if not isinstance(year, int):
        errors.append("year is required.")
    if not isinstance(as_of, date):
        errors.append("as_of (the day before the first SlowBooks pay run) is required.")
    elif isinstance(year, int) and as_of.year != year:
        errors.append(f"as_of {as_of.isoformat()} is not in {year}.")
    clean = {"year": year, "as_of": as_of, "note": (data.get("note") or None)}
    if clean["note"] and len(clean["note"]) > 300:
        clean["note"] = clean["note"][:300]
    for f in OPENING_AMOUNT_FIELDS:
        clean[f] = _money(data.get(f), f, errors)
    if clean["net"] > clean["gross"]:
        errors.append(
            f"net ({clean['net']}) cannot be more than gross ({clean['gross']})."
        )

    clean["states"] = []
    seen_states: set[str] = set()
    for s in data.get("states") or []:
        code = str(s.get("state") or "").strip().upper()
        if not _STATE_RE.match(code):
            errors.append(f"State {s.get('state')!r} is not a two-letter state code.")
            continue
        if code in seen_states:
            errors.append(f"State {code} is listed twice.")
            continue
        seen_states.add(code)
        clean["states"].append(
            {
                "state": code,
                **{f: _money(s.get(f), f"{code} {f}", errors) for f in STATE_FIELDS},
            }
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
    written to the audit trail beside the before and after (CORRECT)."""
    reason = check_reason(reason)
    clean, errors = clean_payload(db, data)
    if errors:
        raise OpeningError(422, " ".join(errors))
    year = clean["year"]
    existing = opening_for(db, employee_id, year)
    first = first_processed_pay_date(db, employee_id, year)
    if first is not None:
        if existing is None and clean["as_of"] >= first:
            raise OpeningError(
                422,
                f"as_of {clean['as_of'].isoformat()} must be before the first "
                f"processed pay run of {year} ({first.isoformat()}): an opening "
                "balance is what was paid before SlowBooks.",
            )
        if not reason:
            raise OpeningError(409, lock_message(first))
    before = snapshot(existing)

    row = existing or PayrollOpeningBalance(employee_id=employee_id, year=year)
    row.as_of = clean["as_of"]
    row.note = clean["note"]
    for f in OPENING_AMOUNT_FIELDS:
        setattr(row, f, clean[f])
    if existing is None:
        db.add(row)
    else:
        # cleared and flushed before the new rows go in, so a state or a
        # code kept from before does not meet its own unique constraint
        row.states.clear()
        row.benefits.clear()
    db.flush()
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
    db.delete(opening)
    db.flush()


def refresh_benefit_ytd(db: Session, year: int) -> None:
    """Rebuild the year's benefit accumulators so the annual caps count
    the opening's benefit amounts; commits."""
    from app.services import benefits_engine

    benefits_engine.rebuild_ytd(db, year)
    db.commit()
