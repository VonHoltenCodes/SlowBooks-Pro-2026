# ============================================================================
# Form 940 — Employer's Annual Federal Unemployment (FUTA) Tax Return
# ----------------------------------------------------------------------------
# Aggregates a full calendar year of PROCESSED pay stubs into the FUTA wage
# and tax totals reported on IRS Form 940. FUTA wages are capped at the first
# $7,000 paid to each employee for the year.
#
# Credit reduction (Form 940 line 11 / Schedule A): an employer who paid
# state unemployment tax to a state that has not repaid its federal UI loans
# loses part of the 5.4% credit, so owes extra FUTA on the FUTA taxable
# wages paid in that state. The states and rates are published each year on
# Schedule A (Form 940); the company's state (Settings → company_state, else
# the EMPLOYER_STATE setting) decides whether it applies.
# ============================================================================

from datetime import date
from decimal import Decimal

from sqlalchemy.orm import joinedload, selectinload

from app.models.payroll import PayRun, PayStub, PayRunStatus
from app.services.accounting import _q
from app.services.payroll_service import FUTA_WAGE_BASE, stub_taxable_wages
from app.services.pdf_service import _jinja_env, render_pdf

# Credit reduction rates by return year and state, from Schedule A (Form 940).
# 2025: "Credit reduction states for 2025. The credit reduction rate for
# California is 0.012 (1.2%). The credit reduction rate for the U.S. Virgin
# Islands is 0.045 (4.5%)." — Schedule A (Form 940) for 2025, Cat. No.
# 16997C, created 11/12/25, https://www.irs.gov/pub/irs-pdf/f940sa.pdf
# (checked 2026-10-31). A year with no row has no credit-reduction states
# that SlowBooks knows of; the 2026 Schedule A is published in November 2026.
FUTA_CREDIT_REDUCTION: dict[int, dict[str, Decimal]] = {
    2025: {"CA": Decimal("0.012"), "VI": Decimal("0.045")},
}


def credit_reduction_rate(year: int, state: str | None) -> Decimal:
    """The Schedule A credit reduction rate for `state` in `year` (0 if none)."""
    if not state:
        return Decimal("0")
    return FUTA_CREDIT_REDUCTION.get(year, {}).get(state.strip().upper(), Decimal("0"))


def _year_stubs(db, year: int) -> list[PayStub]:
    """All PROCESSED pay stubs with a pay_date inside the calendar year."""
    start = date(year, 1, 1)
    end = date(year, 12, 31)
    return (
        db.query(PayStub)
        .join(PayRun, PayStub.pay_run_id == PayRun.id)
        .options(
            joinedload(PayStub.pay_run),
            joinedload(PayStub.employee),
            selectinload(PayStub.benefits),
        )
        .filter(PayRun.status == PayRunStatus.PROCESSED)
        .filter(PayRun.pay_date >= start)
        .filter(PayRun.pay_date <= end)
        .all()
    )


def compute_940(db, year: int, legacy: bool = False) -> dict:
    """Aggregate annual Form 940 (FUTA) totals.

    FUTA taxable wages are computed per employee, capped at the $7,000 wage
    base; payments above the cap are exempt. A stub's FUTA wages are what
    the paycheck taxed: gross less Section 125 and the like (which are
    exempt payments, line 4), never a 401(k).

    `legacy` measures each stub's gross instead, as SlowBooks did before
    2.29.0: Form history's Check uses it (document_check).
    """
    stubs = _year_stubs(db, year)

    total_payments = Decimal("0")
    futa_tax = Decimal("0")
    # Running FUTA wages per employee so the $7,000 cap can be applied as
    # later stubs push an employee over the wage base.
    paid_by_employee: dict[int, Decimal] = {}
    taxable_by_employee: dict[int, Decimal] = {}
    gross_by_employee: dict[int, Decimal] = {}

    # Opening balances (#433): the old system's FUTA wages are what it
    # already counted toward each employee's $7,000, so the year's stubs
    # fill only what is left of it; its payments and FUTA tax are the
    # year's too.
    from app.services.payroll_opening import amount, openings_for_year

    for emp_id, o in openings_for_year(db, year).items():
        gross = amount(o, "gross")
        futa_wages = min(amount(o, "futa_wages"), FUTA_WAGE_BASE)
        total_payments += gross
        futa_tax += amount(o, "futa_tax")
        paid_by_employee[emp_id] = futa_wages
        taxable_by_employee[emp_id] = futa_wages
        gross_by_employee[emp_id] = gross

    # Process in pay_date order so the per-employee wage base fills correctly.
    for s in sorted(stubs, key=lambda x: (x.pay_run.pay_date, x.id)):
        emp_id = s.employee_id
        gross = Decimal(str(s.gross_pay or 0))
        total_payments += gross
        futa_tax += Decimal(str(s.futa_tax or 0))

        futa_wages = gross if legacy else stub_taxable_wages(s)["fica"]
        prior = paid_by_employee.get(emp_id, Decimal("0"))
        if prior >= FUTA_WAGE_BASE:
            taxable = Decimal("0")
        elif prior + futa_wages > FUTA_WAGE_BASE:
            taxable = FUTA_WAGE_BASE - prior
        else:
            taxable = futa_wages
        paid_by_employee[emp_id] = prior + futa_wages
        gross_by_employee[emp_id] = gross_by_employee.get(emp_id, Decimal("0")) + gross
        taxable_by_employee[emp_id] = (
            taxable_by_employee.get(emp_id, Decimal("0")) + taxable
        )

    total_taxable = sum(taxable_by_employee.values(), Decimal("0"))
    # Payments exempt from FUTA = total payments above the per-employee cap.
    exempt_payments = total_payments - total_taxable

    out = {
        "year": year,
        # Employees who received wages: a $0.00 stub pays no one.
        "num_employees": sum(1 for paid in gross_by_employee.values() if paid > 0),
        "num_stubs": len(stubs),
        # Line 3 — total payments to all employees
        "total_payments": _q(total_payments),
        # Payments exempt from FUTA (over the $7,000 wage base)
        "exempt_payments": _q(exempt_payments),
        # Line 7 — total FUTA taxable wages
        "futa_taxable_wages": _q(total_taxable),
        "futa_wage_base": FUTA_WAGE_BASE,
        # Line 8 — FUTA tax before adjustments
        "total_futa_tax": _q(futa_tax),
    }

    # Line 11 — credit reduction (Schedule A), when the company's state is a
    # credit-reduction state for the return year. Every FUTA taxable wage is
    # taken as paid in the company's state (Schedule A wants the wages paid
    # in THAT state; SlowBooks has one employer state per company). The
    # lines are added only when a reduction applies, so a form for any other
    # employer keeps the figures — and the Form history hash — it had.
    from app.services.payroll_documents import employer_block

    state = (employer_block(db).get("state") or "").strip().upper()
    rate = credit_reduction_rate(year, state)
    if rate > 0:
        reduction = _q(total_taxable * rate)
        out.update(
            {
                "credit_reduction_state": state,
                "credit_reduction_rate": rate,
                # Line 11 — FUTA taxable wages paid in the state × its rate
                "credit_reduction": reduction,
                # Line 12 — total FUTA tax after adjustments
                "total_futa_tax_after_adjustments": _q(futa_tax) + reduction,
            }
        )
    return out


def generate_940_pdf(db, year: int, company: dict, audit: dict | None = None) -> bytes:
    """Render Form 940 to a PDF for the given year."""
    data = compute_940(db, year)
    template = _jinja_env.get_template("form_940.html")
    html_str = template.render(data=data, company=company or {}, audit=audit or {})
    return render_pdf(html_str)
