from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

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
    # What the old system paid before the switch (#433): the wage bases,
    # the stub's YTD and the employee's YTD all start from it. A run dated
    # on or before the opening's as_of does not count it (it is what was
    # paid before that day).
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
