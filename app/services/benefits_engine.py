# ============================================================================
# Benefits engine — evaluate whatever codes are attached to an employee.
# ----------------------------------------------------------------------------
# The payroll run asks one question: "for this employee, this period, this
# gross, what comes out and what does the company pay?" The answer is a list
# of resolved lines in SEQUENCE order, each with the rule that produced it
# frozen alongside the amounts, so the stub can snapshot it.
#
# Rules honoured here (docs/design/benefits-engine.md):
#   1. Ordering: pre-tax codes apply in `sequence`; each changes the taxable
#      base for the next (percent_of_taxable sees the reduced base).
#   2. Limits are plural: per-period cap, annual cap, wage-base ceiling.
#   3. Effective dating: rates resolve against the pay-period END date.
#   4. Posted runs snapshot the resolved rule set (PayStubBenefit rows).
#   5. Arbitrary balances: a tracks_balance code stops at zero.
# ============================================================================

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session, joinedload

from app.models.accounts import Account, AccountType
from app.models.benefits import (
    BenefitCode,
    BenefitRate,
    BenefitYTD,
    EmployeeBenefit,
    EmployeeGroupBenefit,
    PayStubBenefit,
)
from app.models.payroll import Employee, PayRun, PayRunStatus, PayStub
from app.services.accounting import _q

ZERO = Decimal("0")
HUNDRED = Decimal("100")


def _d(v) -> Decimal:
    if v is None:
        return ZERO
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def _pick(*values):
    """First non-None value (assignment → group → dated rate)."""
    for v in values:
        if v is not None:
            return v
    return None


# ---------------------------------------------------------------------------
# Rate resolution
# ---------------------------------------------------------------------------
def resolve_rate(code: BenefitCode, as_of: date) -> Optional[BenefitRate]:
    """The dated row in force on `as_of`: the latest effective_from on or
    before the date whose effective_to (if any) hasn't passed."""
    best = None
    for r in code.rates:
        if r.effective_from and r.effective_from > as_of:
            continue
        if r.effective_to and r.effective_to < as_of:
            continue
        if best is None or (r.effective_from or date.min) >= (
            best.effective_from or date.min
        ):
            best = r
    return best


def add_rate(db: Session, code: BenefitCode, data: dict) -> BenefitRate:
    """Append a dated rate row. The previous open-ended row is closed the
    day before the new one starts so history keeps resolving unchanged."""
    start = data.get("effective_from") or date(2000, 1, 1)
    for r in code.rates:
        if r.effective_to is None and r.effective_from < start:
            r.effective_to = start - timedelta(days=1)
    row = BenefitRate(
        benefit_code_id=code.id,
        effective_from=start,
        effective_to=data.get("effective_to"),
        employee_rate=_d(data.get("employee_rate")),
        employer_rate=_d(data.get("employer_rate")),
        per_period_cap=data.get("per_period_cap"),
        annual_cap=data.get("annual_cap"),
        wage_base_ceiling=data.get("wage_base_ceiling"),
        employer_annual_cap=data.get("employer_annual_cap"),
        employer_match_limit_pct=data.get("employer_match_limit_pct"),
        tiers_json=data.get("tiers_json"),
    )
    db.add(row)
    code.rates.append(row)
    db.flush()
    return row


def code_in_force(code: BenefitCode, period_start: date, period_end: date) -> bool:
    if not code.is_active:
        return False
    if code.effective_from and code.effective_from > period_end:
        return False
    if code.effective_to and code.effective_to < period_start:
        return False
    return True


# ---------------------------------------------------------------------------
# Resolution: which codes apply to an employee, at what rates
# ---------------------------------------------------------------------------
@dataclass
class Resolved:
    code: BenefitCode
    rate: BenefitRate
    source: str  # "assignment" | "group"
    assignment: Optional[EmployeeBenefit] = None
    employee_rate: Decimal = ZERO
    employer_rate: Decimal = ZERO
    per_period_cap: Optional[Decimal] = None
    annual_cap: Optional[Decimal] = None
    wage_base_ceiling: Optional[Decimal] = None
    employer_annual_cap: Optional[Decimal] = None
    employer_match_limit_pct: Optional[Decimal] = None
    tiers: list = field(default_factory=list)

    @property
    def sort_key(self):
        return (self.code.sequence or 100, self.code.code)


def _tiers(rate: BenefitRate) -> list:
    if not rate.tiers_json:
        return []
    try:
        data = json.loads(rate.tiers_json)
    except (ValueError, TypeError):
        return []
    return data if isinstance(data, list) else []


def resolve_for_employee(
    db: Session, emp: Employee, period_start: date, period_end: date
) -> list[Resolved]:
    """Assignments win; the employee's group fills in every code the
    employee has no assignment for. A field left blank on an assignment
    falls back to the group's value for that code, then to the dated rate.
    Codes with no dated rate covering the period end are skipped — no
    rate, no deduction."""
    group_rows: dict[int, EmployeeGroupBenefit] = {}
    if emp.employee_group_id:
        for g in (
            db.query(EmployeeGroupBenefit)
            .options(
                joinedload(EmployeeGroupBenefit.benefit_code).joinedload(
                    BenefitCode.rates
                )
            )
            .filter(EmployeeGroupBenefit.group_id == emp.employee_group_id)
            .all()
        ):
            if g.benefit_code:
                group_rows[g.benefit_code_id] = g

    def build(code, rate, source, assignment, override, group):
        return Resolved(
            code=code,
            rate=rate,
            source=source,
            assignment=assignment,
            employee_rate=_d(
                _pick(
                    override.employee_rate if override else None,
                    group.employee_rate if group else None,
                    rate.employee_rate,
                )
            ),
            employer_rate=_d(
                _pick(
                    override.employer_rate if override else None,
                    group.employer_rate if group else None,
                    rate.employer_rate,
                )
            ),
            per_period_cap=_pick(
                override.per_period_cap if override else None,
                group.per_period_cap if group else None,
                rate.per_period_cap,
            ),
            annual_cap=_pick(
                override.annual_cap if override else None,
                group.annual_cap if group else None,
                rate.annual_cap,
            ),
            wage_base_ceiling=rate.wage_base_ceiling,
            employer_annual_cap=rate.employer_annual_cap,
            employer_match_limit_pct=rate.employer_match_limit_pct,
            tiers=_tiers(rate),
        )

    out: dict[int, Resolved] = {}
    rows = (
        db.query(EmployeeBenefit)
        .options(joinedload(EmployeeBenefit.benefit_code).joinedload(BenefitCode.rates))
        .filter(EmployeeBenefit.employee_id == emp.id, EmployeeBenefit.is_active)
        .all()
    )
    for a in rows:
        if a.start_date and a.start_date > period_end:
            continue
        if a.end_date and a.end_date < period_start:
            continue
        code = a.benefit_code
        if not code or not code_in_force(code, period_start, period_end):
            continue
        rate = resolve_rate(code, period_end)
        if rate is None:
            continue
        out[code.id] = build(code, rate, "assignment", a, a, group_rows.get(code.id))
    for code_id, g in group_rows.items():
        if code_id in out:
            continue
        code = g.benefit_code
        if not code_in_force(code, period_start, period_end):
            continue
        rate = resolve_rate(code, period_end)
        if rate is None:
            continue
        out[code.id] = build(code, rate, "group", None, None, g)
    return sorted(out.values(), key=lambda r: r.sort_key)


# ---------------------------------------------------------------------------
# YTD accumulators
# ---------------------------------------------------------------------------
def get_ytd(db: Session, employee_id: int, benefit_code_id: int, year: int):
    return (
        db.query(BenefitYTD)
        .filter(
            BenefitYTD.employee_id == employee_id,
            BenefitYTD.benefit_code_id == benefit_code_id,
            BenefitYTD.year == year,
        )
        .first()
    )


def bump_ytd(
    db: Session,
    employee_id: int,
    benefit_code_id: int,
    year: int,
    employee_amount: Decimal,
    employer_amount: Decimal,
) -> BenefitYTD:
    row = get_ytd(db, employee_id, benefit_code_id, year)
    if row is None:
        row = BenefitYTD(
            employee_id=employee_id,
            benefit_code_id=benefit_code_id,
            year=year,
            employee_amount=ZERO,
            employer_amount=ZERO,
        )
        db.add(row)
    row.employee_amount = _q(_d(row.employee_amount) + employee_amount)
    row.employer_amount = _q(_d(row.employer_amount) + employer_amount)
    db.flush()
    return row


def rebuild_ytd(db: Session, year: int) -> int:
    """Repair path: recompute the accumulators for a year from the stub
    snapshots (non-void runs). Returns the number of rows written."""
    db.query(BenefitYTD).filter(BenefitYTD.year == year).delete()
    rows = (
        db.query(
            PayStub.employee_id,
            PayStubBenefit.benefit_code_id,
            sqlfunc.sum(PayStubBenefit.employee_amount),
            sqlfunc.sum(PayStubBenefit.employer_amount),
        )
        .join(PayStub, PayStubBenefit.pay_stub_id == PayStub.id)
        .join(PayRun, PayStub.pay_run_id == PayRun.id)
        .filter(
            PayRun.status != PayRunStatus.VOID,
            PayRun.pay_date >= date(year, 1, 1),
            PayRun.pay_date <= date(year, 12, 31),
            PayStubBenefit.benefit_code_id.isnot(None),
        )
        .group_by(PayStub.employee_id, PayStubBenefit.benefit_code_id)
        .all()
    )
    n = 0
    for emp_id, code_id, emp_amt, er_amt in rows:
        db.add(
            BenefitYTD(
                employee_id=emp_id,
                benefit_code_id=code_id,
                year=year,
                employee_amount=_q(_d(emp_amt)),
                employer_amount=_q(_d(er_amt)),
            )
        )
        n += 1
    db.flush()
    return n


# ---------------------------------------------------------------------------
# Calculation
# ---------------------------------------------------------------------------
@dataclass
class Line:
    resolved: Resolved
    employee_amount: Decimal = ZERO
    employer_amount: Decimal = ZERO
    base_before: Decimal = ZERO  # income-tax base before this line
    note: str = ""

    @property
    def code(self) -> BenefitCode:
        return self.resolved.code


@dataclass
class CalcResult:
    lines: list[Line]
    gross: Decimal
    federal_base: Decimal
    state_base: Decimal
    fica_base: Decimal

    @property
    def pretax_total(self) -> Decimal:
        return _q(
            sum(
                (
                    ln.employee_amount
                    for ln in self.lines
                    if ln.code.category == "pretax"
                ),
                ZERO,
            )
        )

    @property
    def posttax_total(self) -> Decimal:
        return _q(
            sum(
                (
                    ln.employee_amount
                    for ln in self.lines
                    if ln.code.category == "posttax"
                ),
                ZERO,
            )
        )

    @property
    def employee_total(self) -> Decimal:
        return _q(sum((ln.employee_amount for ln in self.lines), ZERO))

    @property
    def employer_total(self) -> Decimal:
        return _q(sum((ln.employer_amount for ln in self.lines), ZERO))

    # What the tax calculator needs: how much each wage base dropped
    @property
    def pretax_federal(self) -> Decimal:
        return _q(self.gross - self.federal_base)

    @property
    def pretax_state(self) -> Decimal:
        return _q(self.gross - self.state_base)

    @property
    def pretax_fica(self) -> Decimal:
        return _q(self.gross - self.fica_base)


def _tiered(base: Decimal, tiers: list, key_up="up_to", key_rate="rate") -> Decimal:
    """Marginal bands: each tier's rate (percent) applies to the slice of
    `base` between the previous tier's ceiling and this one's. A tier with
    no ceiling is the top band."""
    total = ZERO
    lower = ZERO
    for t in sorted(
        tiers,
        key=lambda x: (
            _d(x.get(key_up)) if x.get(key_up) is not None else Decimal("1e12")
        ),
    ):
        upper = t.get(key_up)
        rate = _d(t.get(key_rate))
        top = base if upper is None else min(base, _d(upper))
        if top > lower:
            total += (top - lower) * rate / HUNDRED
        lower = max(lower, top)
        if upper is not None and base <= _d(upper):
            break
    return total


def _amount(
    method: str,
    rate: Decimal,
    gross: Decimal,
    eligible: Decimal,
    taxable_base: Decimal,
    hours: Decimal,
    tiers: list,
) -> Decimal:
    if method == "fixed_amount":
        return rate
    if method == "percent_of_gross":
        return eligible * rate / HUNDRED
    if method == "percent_of_taxable":
        # sequence-sensitive: the base after earlier pre-tax codes
        return min(eligible, taxable_base) * rate / HUNDRED
    if method == "amount_per_hour":
        return hours * rate
    if method == "tiered":
        return _tiered(eligible, tiers)
    return ZERO


def _match(
    employee_amount: Decimal,
    gross: Decimal,
    employer_rate: Decimal,
    limit_pct: Optional[Decimal],
    tiers: list,
) -> Decimal:
    """Employer match on the employee's own contribution.

    Flat: employer_rate % of the contribution, on at most limit_pct of gross.
    Tiered: bands on the contribution as a percent of gross —
    [{"up_to_pct": 3, "match_pct": 100}, {"up_to_pct": 5, "match_pct": 50}]
    is the classic "100% of the first 3%, 50% of the next 2%".
    """
    if gross <= 0 or employee_amount <= 0:
        return ZERO
    if tiers:
        contrib_pct = employee_amount / gross * HUNDRED
        matched_pct = _tiered(contrib_pct, tiers, "up_to_pct", "match_pct")
        return gross * matched_pct / HUNDRED
    matched = employee_amount
    if limit_pct is not None:
        matched = min(matched, gross * _d(limit_pct) / HUNDRED)
    return matched * employer_rate / HUNDRED


def compute(
    db: Session,
    emp: Employee,
    gross: Decimal,
    hours: Decimal,
    period_start: date,
    period_end: date,
    year: int,
    ytd_gross_before: Decimal = ZERO,
    resolved: Optional[list[Resolved]] = None,
) -> CalcResult:
    gross = _q(_d(gross))
    hours = _d(hours)
    if resolved is None:
        resolved = resolve_for_employee(db, emp, period_start, period_end)
    fed_base = state_base = fica_base = gross
    remaining_pay = gross
    lines: list[Line] = []
    for r in resolved:
        code = r.code
        ytd = get_ytd(db, emp.id, code.id, year)
        ytd_emp = _d(ytd.employee_amount) if ytd else ZERO
        ytd_er = _d(ytd.employer_amount) if ytd else ZERO
        eligible = gross
        if r.wage_base_ceiling is not None:
            ceiling = _d(r.wage_base_ceiling)
            eligible = max(ZERO, min(gross, ceiling - _d(ytd_gross_before)))
        line = Line(resolved=r, base_before=fed_base)

        # --- employee side ---
        emp_amt = ZERO
        if code.kind in ("deduction", "both") and gross > 0:
            emp_amt = _amount(
                code.calc_method,
                r.employee_rate,
                gross,
                eligible,
                fed_base,
                hours,
                r.tiers,
            )
            if r.per_period_cap is not None:
                emp_amt = min(emp_amt, _d(r.per_period_cap))
            if r.annual_cap is not None:
                emp_amt = min(emp_amt, max(ZERO, _d(r.annual_cap) - ytd_emp))
            if code.tracks_balance and r.assignment is not None:
                bal = r.assignment.balance_remaining
                if bal is not None:
                    emp_amt = min(emp_amt, max(ZERO, _d(bal)))
            emp_amt = _q(max(ZERO, min(emp_amt, remaining_pay)))
            remaining_pay -= emp_amt
            if code.category == "pretax":
                if code.reduces_federal:
                    fed_base -= emp_amt
                if code.reduces_state:
                    state_base -= emp_amt
                if code.reduces_fica:
                    fica_base -= emp_amt

        # --- employer side ---
        er_amt = ZERO
        if code.kind in ("benefit", "both") and gross > 0:
            method = code.employer_calc_method or code.calc_method
            if method == "match_percent":
                er_amt = _match(
                    emp_amt, gross, r.employer_rate, r.employer_match_limit_pct, r.tiers
                )
            else:
                er_amt = _amount(
                    method, r.employer_rate, gross, eligible, fed_base, hours, r.tiers
                )
            if r.employer_annual_cap is not None:
                er_amt = min(er_amt, max(ZERO, _d(r.employer_annual_cap) - ytd_er))
            er_amt = _q(max(ZERO, er_amt))

        line.employee_amount = emp_amt
        line.employer_amount = er_amt
        if emp_amt or er_amt or code.kind == "benefit":
            lines.append(line)
    return CalcResult(
        lines=lines,
        gross=gross,
        federal_base=_q(max(ZERO, fed_base)),
        state_base=_q(max(ZERO, state_base)),
        fica_base=_q(max(ZERO, fica_base)),
    )


# ---------------------------------------------------------------------------
# Snapshot onto the stub + accumulate
# ---------------------------------------------------------------------------
def record_on_stub(db: Session, stub: PayStub, result: CalcResult, year: int) -> None:
    for ln in result.lines:
        code = ln.code
        r = ln.resolved
        rule = {
            "source": r.source,
            "employer_calc_method": code.employer_calc_method,
            "per_period_cap": (
                str(r.per_period_cap) if r.per_period_cap is not None else None
            ),
            "annual_cap": str(r.annual_cap) if r.annual_cap is not None else None,
            "wage_base_ceiling": (
                str(r.wage_base_ceiling) if r.wage_base_ceiling is not None else None
            ),
            "employer_annual_cap": (
                str(r.employer_annual_cap)
                if r.employer_annual_cap is not None
                else None
            ),
            "employer_match_limit_pct": (
                str(r.employer_match_limit_pct)
                if r.employer_match_limit_pct is not None
                else None
            ),
            "tiers": r.tiers or None,
            "rate_effective_from": (
                r.rate.effective_from.isoformat() if r.rate.effective_from else None
            ),
            "taxable_base_before": str(ln.base_before),
            "employer_taxable": bool(code.employer_taxable),
        }
        db.add(
            PayStubBenefit(
                pay_stub_id=stub.id,
                benefit_code_id=code.id,
                code=code.code,
                name=code.name,
                kind=code.kind,
                category=code.category,
                sequence=code.sequence or 100,
                calc_method=code.calc_method,
                employee_rate=r.employee_rate,
                employer_rate=r.employer_rate,
                reduces_federal=bool(code.reduces_federal),
                reduces_state=bool(code.reduces_state),
                reduces_fica=bool(code.reduces_fica),
                expense_account_id=code.expense_account_id,
                liability_account_id=code.liability_account_id,
                remittance_vendor_id=code.remittance_vendor_id,
                burden_routing=code.burden_routing or "fringe_pool",
                rule_json=json.dumps(rule),
                employee_amount=ln.employee_amount,
                employer_amount=ln.employer_amount,
            )
        )
        if ln.employee_amount or ln.employer_amount:
            bump_ytd(
                db,
                stub.employee_id,
                code.id,
                year,
                ln.employee_amount,
                ln.employer_amount,
            )
        if (
            code.tracks_balance
            and r.assignment is not None
            and r.assignment.balance_remaining is not None
            and ln.employee_amount
        ):
            r.assignment.balance_remaining = _q(
                max(ZERO, _d(r.assignment.balance_remaining) - ln.employee_amount)
            )
    db.flush()


# ---------------------------------------------------------------------------
# GL grouping for the payroll journal entry
# ---------------------------------------------------------------------------
@dataclass
class GLGroups:
    liabilities: dict  # account_id -> amount (employee withheld + employer cost)
    expenses: dict  # account_id -> employer cost
    employee_total: Decimal
    employer_total: Decimal
    unmapped_liability: Decimal  # rows with no liability account
    unmapped_expense: Decimal


def gl_groups(run: PayRun) -> GLGroups:
    liabilities: dict = {}
    expenses: dict = {}
    emp_total = er_total = ZERO
    unmapped_l = unmapped_e = ZERO
    for stub in run.stubs:
        for b in stub.benefits:
            ea = _d(b.employee_amount)
            ra = _d(b.employer_amount)
            emp_total += ea
            er_total += ra
            if b.liability_account_id:
                liabilities[b.liability_account_id] = (
                    liabilities.get(b.liability_account_id, ZERO) + ea + ra
                )
            else:
                unmapped_l += ea + ra
            if ra:
                if b.expense_account_id:
                    expenses[b.expense_account_id] = (
                        expenses.get(b.expense_account_id, ZERO) + ra
                    )
                else:
                    unmapped_e += ra
    return GLGroups(
        liabilities={k: _q(v) for k, v in liabilities.items()},
        expenses={k: _q(v) for k, v in expenses.items()},
        employee_total=_q(emp_total),
        employer_total=_q(er_total),
        unmapped_liability=_q(unmapped_l),
        unmapped_expense=_q(unmapped_e),
    )


# ---------------------------------------------------------------------------
# Remittance
# ---------------------------------------------------------------------------
def remittance_rows(db: Session, start: date, end: date) -> list[dict]:
    """Per vendor, per code: what was withheld and what the company owes,
    from the posted-run snapshots (processed runs, by pay date)."""
    rows = (
        db.query(
            PayStubBenefit.remittance_vendor_id,
            PayStubBenefit.benefit_code_id,
            PayStubBenefit.code,
            PayStubBenefit.name,
            PayStubBenefit.liability_account_id,
            sqlfunc.sum(PayStubBenefit.employee_amount),
            sqlfunc.sum(PayStubBenefit.employer_amount),
            sqlfunc.count(PayStubBenefit.id),
        )
        .join(PayStub, PayStubBenefit.pay_stub_id == PayStub.id)
        .join(PayRun, PayStub.pay_run_id == PayRun.id)
        .filter(
            PayRun.status == PayRunStatus.PROCESSED,
            PayRun.pay_date >= start,
            PayRun.pay_date <= end,
        )
        .group_by(
            PayStubBenefit.remittance_vendor_id,
            PayStubBenefit.benefit_code_id,
            PayStubBenefit.code,
            PayStubBenefit.name,
            PayStubBenefit.liability_account_id,
        )
        .all()
    )
    from app.models.contacts import Vendor

    vendors = {v.id: v.name for v in db.query(Vendor).all()}
    out = []
    for vid, cid, code, name, liab, ea, ra, n in rows:
        ea = _q(_d(ea))
        ra = _q(_d(ra))
        if ea == 0 and ra == 0:
            continue
        out.append(
            {
                "vendor_id": vid,
                "vendor_name": vendors.get(vid) if vid else None,
                "benefit_code_id": cid,
                "code": code,
                "name": name,
                "liability_account_id": liab,
                "employee_amount": float(ea),
                "employer_amount": float(ra),
                "total": float(ea + ra),
                "stub_count": int(n),
            }
        )
    out.sort(key=lambda r: ((r["vendor_name"] or "~"), r["code"]))
    return out


def create_remittance_bill(
    db: Session,
    vendor_id: int,
    start: date,
    end: date,
    bill_date: Optional[date] = None,
    bill_number: Optional[str] = None,
):
    """A vendor bill for the period's withholdings + employer cost: one line
    per code, debiting the code's liability account (relieving the payroll
    liability the run credited) and crediting AP."""
    from app.routes.bills import create_bill
    from app.schemas.bills import BillCreate, BillLineCreate

    rows = [r for r in remittance_rows(db, start, end) if r["vendor_id"] == vendor_id]
    if not rows:
        raise ValueError("No processed payroll for that vendor in the period")
    fallback = account_by_number(db, "2380") or account_by_number(db, "2370")
    lines = []
    for r in rows:
        acct = r["liability_account_id"] or (fallback.id if fallback else None)
        if not acct:
            raise ValueError(
                f"Code {r['code']} has no liability account and no 2380/2370 exists"
            )
        lines.append(
            BillLineCreate(
                account_id=acct,
                description=f"{r['name']} ({r['code']}) {start} – {end}",
                quantity=1,
                rate=r["total"],
            )
        )
    data = BillCreate(
        vendor_id=vendor_id,
        bill_number=bill_number
        or f"REMIT-{start.strftime('%Y%m%d')}-{end.strftime('%Y%m%d')}",
        date=bill_date or end,
        terms="Net 15",
        notes=f"Benefit remittance for pay dates {start} to {end}",
        lines=lines,
    )
    return create_bill(data, db)


# ---------------------------------------------------------------------------
# Accounts + standard codes
# ---------------------------------------------------------------------------
def account_by_number(db: Session, number: str) -> Optional[Account]:
    return db.query(Account).filter(Account.account_number == number).first()


DEFAULT_ACCOUNTS = (
    ("2380", "Employee Benefits Payable", AccountType.LIABILITY),
    ("2390", "Accrued PTO Liability", AccountType.LIABILITY),
    ("6150", "Employee Benefits Expense", AccountType.EXPENSE),
    ("6160", "Paid Time Off Expense", AccountType.EXPENSE),
)


def ensure_accounts(db: Session) -> list[str]:
    """Create the benefit / PTO accounts a pre-engine company file lacks.
    Returns the numbers created."""
    created = []
    for number, name, atype in DEFAULT_ACCOUNTS:
        if account_by_number(db, number) is None:
            db.add(
                Account(
                    account_number=number,
                    name=name,
                    account_type=atype,
                    is_system=True,
                    balance=ZERO,
                )
            )
            created.append(number)
    db.flush()
    return created


# code, name, kind, category, calc_method, employer_calc_method,
# reduces (fed, state, fica), sequence, employee_rate, employer_rate, extras
STANDARD_CODES = [
    (
        "SEC125",
        "Section 125 Health Premium",
        "both",
        "pretax",
        "fixed_amount",
        None,
        (True, True, True),
        10,
        {},
    ),
    (
        "SEC125DV",
        "Dental / Vision Premium",
        "both",
        "pretax",
        "fixed_amount",
        None,
        (True, True, True),
        20,
        {},
    ),
    (
        "HSA",
        "HSA Contribution",
        "both",
        "pretax",
        "fixed_amount",
        None,
        (True, True, True),
        30,
        {},
    ),
    (
        "401K",
        "401(k) Traditional",
        "both",
        "pretax",
        "percent_of_gross",
        "match_percent",
        (True, True, False),
        40,
        {
            "employer_rate": 100,
            "employer_match_limit_pct": 3,
            "tiers_json": json.dumps(
                [{"up_to_pct": 3, "match_pct": 100}, {"up_to_pct": 5, "match_pct": 50}]
            ),
        },
    ),
    (
        "ROTH401K",
        "Roth 401(k)",
        "deduction",
        "posttax",
        "percent_of_gross",
        None,
        (False, False, False),
        50,
        {},
    ),
    (
        "UNION",
        "Union Dues",
        "deduction",
        "posttax",
        "fixed_amount",
        None,
        (False, False, False),
        60,
        {},
    ),
    (
        "LOAN",
        "Employee Loan Repayment",
        "deduction",
        "posttax",
        "fixed_amount",
        None,
        (False, False, False),
        70,
        {"tracks_balance": True},
    ),
    (
        "HEALTH_ER",
        "Employer Health Contribution",
        "benefit",
        "pretax",
        "fixed_amount",
        None,
        (False, False, False),
        80,
        {},
    ),
    (
        "GTL",
        "Group Term Life (employer)",
        "benefit",
        "pretax",
        "fixed_amount",
        None,
        (False, False, False),
        90,
        {"employer_taxable": True},
    ),
]


def seed_standard_codes(db: Session) -> list[BenefitCode]:
    existing = {c.code for c in db.query(BenefitCode).all()}
    expense = account_by_number(db, "6150")
    liability = account_by_number(db, "2380") or account_by_number(db, "2370")
    for code, name, kind, cat, method, emethod, red, seq, extra in STANDARD_CODES:
        if code in existing:
            continue
        bc = BenefitCode(
            code=code,
            name=name,
            kind=kind,
            category=cat,
            calc_method=method,
            employer_calc_method=emethod,
            reduces_federal=red[0],
            reduces_state=red[1],
            reduces_fica=red[2],
            sequence=seq,
            tracks_balance=bool(extra.get("tracks_balance")),
            employer_taxable=bool(extra.get("employer_taxable")),
            expense_account_id=expense.id if expense else None,
            liability_account_id=liability.id if liability else None,
        )
        db.add(bc)
        db.flush()
        add_rate(
            db,
            bc,
            {
                "effective_from": date(2000, 1, 1),
                "employee_rate": 0,
                "employer_rate": extra.get("employer_rate", 0),
                "employer_match_limit_pct": extra.get("employer_match_limit_pct"),
                "tiers_json": extra.get("tiers_json"),
            },
        )
    db.flush()
    return db.query(BenefitCode).order_by(BenefitCode.sequence, BenefitCode.code).all()
