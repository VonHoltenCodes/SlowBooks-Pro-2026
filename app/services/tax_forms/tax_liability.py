# ============================================================================
# Quarterly payroll tax liability report — what is owed, and when.
# ----------------------------------------------------------------------------
# Aggregates a quarter's processed pay stubs into a schedule of tax deposits /
# returns with their statutory due dates (Form 941, Form 940 FUTA, state
# unemployment, state income-tax withholding).
# ============================================================================

import calendar
from datetime import date
from decimal import Decimal

from app.models.payroll import PayRun, PayStub, PayRunStatus
from app.services.accounting import _q


def _quarter_bounds(year: int, quarter: int) -> tuple:
    start_month = (quarter - 1) * 3 + 1
    end_month = quarter * 3
    start = date(year, start_month, 1)
    end = date(year, end_month, calendar.monthrange(year, end_month)[1])
    return start, end


def _filing_due_date(year: int, quarter: int) -> date:
    """Quarterly returns are due the last day of the month following quarter end."""
    if quarter == 4:
        due_year, due_month = year + 1, 1
    else:
        due_year, due_month = year, quarter * 3 + 1
    return date(due_year, due_month, calendar.monthrange(due_year, due_month)[1])


def compute_tax_liability(db, year: int, quarter: int) -> dict:
    """Return the payroll tax liability schedule for one calendar quarter."""
    if quarter not in (1, 2, 3, 4):
        raise ValueError("quarter must be 1-4")

    start, end = _quarter_bounds(year, quarter)
    due = _filing_due_date(year, quarter)

    stubs = (
        db.query(PayStub)
        .join(PayRun, PayStub.pay_run_id == PayRun.id)
        .filter(
            PayRun.status == PayRunStatus.PROCESSED,
            PayRun.pay_date >= start,
            PayRun.pay_date <= end,
        )
        .all()
    )

    federal_income = sum((_q(s.federal_tax) for s in stubs), Decimal("0"))
    ss = sum((_q(s.ss_tax) + _q(s.employer_ss_tax) for s in stubs), Decimal("0"))
    medicare = sum(
        (_q(s.medicare_tax) + _q(s.employer_medicare_tax) for s in stubs), Decimal("0")
    )
    futa = sum((_q(s.futa_tax) for s in stubs), Decimal("0"))
    suta = sum((_q(s.suta_tax) for s in stubs), Decimal("0"))
    state_income = sum((_q(s.state_tax) for s in stubs), Decimal("0"))
    state_other = sum(
        (_q(s.state_other_employee) + _q(s.state_other_employer) for s in stubs),
        Decimal("0"),
    )

    # Opening balances (#433): the old system's share of this quarter. The
    # 941 for the quarter reports it (line 12), so the schedule carries it
    # too, and names it: it was deposited through the old system, or the
    # opening balance sheet carries it, so it is not due through SlowBooks.
    from app.services.payroll_opening import amount, openings_for_year, state_total

    q = {quarter}
    prior = {k: Decimal("0") for k in ("941", "940", "sui", "wh", "other")}
    for o in openings_for_year(db, year).values():
        prior["941"] += sum(
            (
                amount(o, f, q)
                for f in (
                    "federal_tax",
                    "ss_tax",
                    "employer_ss_tax",
                    "medicare_tax",
                    "employer_medicare_tax",
                )
            ),
            Decimal("0"),
        )
        prior["940"] += amount(o, "futa_tax", q)
        prior["sui"] += state_total(o, "suta_tax", q)
        prior["wh"] += state_total(o, "withholding", q)
        prior["other"] += state_total(o, "other_employee", q) + state_total(
            o, "other_employer", q
        )
    prior = {k: _q(v) for k, v in prior.items()}

    federal_941 = _q(federal_income + ss + medicare)

    def line(form, agency, description, ours, key):
        total = _q(ours) + prior[key]
        return {
            "form": form,
            "agency": agency,
            "description": description,
            "amount": float(total),
            # the old payroll's part (#433), and what is left to deposit
            "prior_system": float(prior[key]),
            "due": float(_q(ours)),
            "due_date": due.isoformat(),
        }

    liabilities = [
        line(
            "941",
            "IRS",
            "Federal employment tax (income tax withheld + Social Security + Medicare)",
            federal_941,
            "941",
        ),
        line("940", "IRS", "Federal unemployment tax (FUTA)", futa, "940"),
        line("State SUI", "State", "State unemployment insurance", suta, "sui"),
        line("State WH", "State", "State income tax withheld", state_income, "wh"),
        line(
            "State Other",
            "State",
            "State disability / paid-leave / L&I premiums",
            state_other,
            "other",
        ),
    ]
    total_due = _q(federal_941 + futa + suta + state_income + state_other)
    total_prior = _q(sum(prior.values(), Decimal("0")))

    return {
        "year": year,
        "quarter": quarter,
        "quarter_start": start.isoformat(),
        "quarter_end": end.isoformat(),
        "num_stubs": len(stubs),
        "liabilities": liabilities,
        # what is left to deposit through SlowBooks
        "total_due": float(total_due),
        # the old payroll's share of the quarter (#433), and both together
        "total_prior_system": float(total_prior),
        "total_liability": float(_q(total_due + total_prior)),
    }
