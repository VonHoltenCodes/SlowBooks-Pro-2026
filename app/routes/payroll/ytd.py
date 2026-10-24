from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session, selectinload

from app.models.payroll import (
    PayRun,
    PayStub,
    PayRunStatus,
    PayRunType,
)


# ---------------------------------------------------------------------------
# Year-to-date helpers — fixes Bug 1 (YTD was never threaded into the calc, so
# the Social Security wage-base cap could never fire).
# ---------------------------------------------------------------------------
def _ytd_stubs(db: Session, employee_id: int, year: int, before: date = None):
    q = (
        db.query(PayStub)
        .join(PayRun, PayStub.pay_run_id == PayRun.id)
        .options(selectinload(PayStub.benefits))
        .filter(
            PayStub.employee_id == employee_id,
            PayRun.status != PayRunStatus.VOID,
            PayRun.pay_date >= date(year, 1, 1),
            PayRun.pay_date <= date(year, 12, 31),
        )
    )
    if before is not None:
        q = q.filter(PayRun.pay_date < before)
    return q.all()


def employee_ytd(db: Session, employee_id: int, year: int, before: date = None) -> dict:
    """Aggregate an employee's pay-stub figures for a calendar year, and
    the opening balance when there is one (#433)."""
    totals = {
        "gross": Decimal("0"),
        "federal": Decimal("0"),
        "state": Decimal("0"),
        "state_other": Decimal("0"),
        "ss": Decimal("0"),
        "medicare": Decimal("0"),
        "pretax_deductions": Decimal("0"),
        "net": Decimal("0"),
    }
    for s in _ytd_stubs(db, employee_id, year, before):
        totals["gross"] += s.gross_pay or 0
        totals["federal"] += s.federal_tax or 0
        totals["state"] += s.state_tax or 0
        totals["state_other"] += s.state_other_employee or 0
        totals["ss"] += s.ss_tax or 0
        totals["medicare"] += s.medicare_tax or 0
        totals["pretax_deductions"] += s.pretax_deductions or 0
        totals["net"] += s.net_pay or 0
    # What the old system paid before the switch (#433), every quarter of
    # it: the stub's YTD and the employee's YTD start from it (the wage
    # bases start from its taxable-wage figures: wage_base_ytd). A run
    # dated on or before the opening's as_of does not count it (it is what
    # was paid before that day).
    from app.services import payroll_opening as po

    opening = po.opening_for(db, employee_id, year)
    if po.counts_before(opening, before):
        totals["gross"] += po.amount(opening, "gross")
        totals["federal"] += po.amount(opening, "federal_tax")
        totals["state"] += po.state_total(opening, "withholding")
        totals["state_other"] += po.state_total(opening, "other_employee")
        totals["ss"] += po.amount(opening, "ss_tax")
        totals["medicare"] += po.amount(opening, "medicare_tax")
        totals["pretax_deductions"] += po.amount(opening, "pretax_deductions")
        totals["net"] += po.amount(opening, "net")
    return totals


def wage_base_ytd(
    db: Session, employee_id: int, year: int, work_state: str, before: date = None
) -> dict:
    """What already counts toward each of the employee's wage bases this
    year, before `before`: the paycheck's caps start from these, and the
    941, 940 and SUI report measure the same way, so a run and the forms
    agree.

    Each SlowBooks stub counts the wages the paycheck taxed: its gross
    less the pre-tax amounts that reduce FICA (Section 125, HSA; a 401(k)
    does not), toward Social Security, the Additional Medicare threshold,
    FUTA and SUTA (payroll_service.stub_taxable_wages); its gross toward
    the work state's own capped taxes, which the state engines measure on
    gross. The opening balance (#433) counts the old system's own taxable
    wages: its Social
    Security wages toward the Social Security base, its Medicare wages
    toward the Additional Medicare threshold, its FUTA wages toward the
    $7,000, and its SUTA and state wages for `work_state` only — the SUTA
    base is a state's own, so stubs paid in another state do not fill it
    either."""
    from app.models.payroll import Employee
    from app.services import payroll_opening as po
    from app.services.payroll_service import stub_taxable_wages

    state = (work_state or "").strip().upper()
    home = None
    bases = {k: Decimal("0") for k in ("ss", "medicare", "futa", "suta", "state")}
    for s in _ytd_stubs(db, employee_id, year, before):
        wages = stub_taxable_wages(s)
        gross, fica = wages["gross"], wages["fica"]
        bases["ss"] += fica
        bases["medicare"] += fica
        bases["futa"] += fica
        stub_state = (s.work_state or "").strip().upper()
        if not stub_state:
            if home is None:
                emp = db.query(Employee).filter(Employee.id == employee_id).first()
                home = ((emp.work_state if emp else None) or "WA").strip().upper()
            stub_state = home
        if stub_state == state:
            bases["suta"] += fica
            bases["state"] += gross
    opening = po.opening_for(db, employee_id, year)
    if po.counts_before(opening, before):
        for k, v in po.wage_bases(opening, state).items():
            bases[k] += v
    return bases


def _ytd_supplemental(
    db: Session, employee_id: int, year: int, before: date = None
) -> Decimal:
    """YTD supplemental wages — gross pay from BONUS-type runs this year.

    Drives the $1M/37% supplemental withholding tier. Approximation: bonuses
    paid as supplemental stubs inside a REGULAR run aren't distinguishable
    after the fact (PayStub has no supplemental flag), so only BONUS runs
    count — the path bonuses are actually processed through.
    """
    total = Decimal("0")
    for s in _ytd_stubs(db, employee_id, year, before):
        if s.pay_run and s.pay_run.run_type == PayRunType.BONUS:
            total += s.gross_pay or 0
    return total
