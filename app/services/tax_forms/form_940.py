# ============================================================================
# Form 940 — Employer's Annual Federal Unemployment (FUTA) Tax Return
# ----------------------------------------------------------------------------
# Aggregates a full calendar year of PROCESSED pay stubs into the FUTA wage
# and tax totals reported on IRS Form 940. FUTA wages are capped at the first
# $7,000 paid to each employee for the year.
# ============================================================================

from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy.orm import joinedload

from app.models.payroll import PayRun, PayStub, PayRunStatus
from app.services.payroll_service import FUTA_WAGE_BASE
from app.services.pdf_service import _jinja_env, _safe_url_fetcher
from weasyprint import HTML

CENT = Decimal("0.01")


def _q(value) -> Decimal:
    """Coerce to Decimal and quantize to cents."""
    if not isinstance(value, Decimal):
        value = Decimal(str(value or 0))
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _year_stubs(db, year: int) -> list[PayStub]:
    """All PROCESSED pay stubs with a pay_date inside the calendar year."""
    start = date(year, 1, 1)
    end = date(year, 12, 31)
    return (
        db.query(PayStub)
        .join(PayRun, PayStub.pay_run_id == PayRun.id)
        .options(joinedload(PayStub.pay_run), joinedload(PayStub.employee))
        .filter(PayRun.status == PayRunStatus.PROCESSED)
        .filter(PayRun.pay_date >= start)
        .filter(PayRun.pay_date <= end)
        .all()
    )


def compute_940(db, year: int) -> dict:
    """Aggregate annual Form 940 (FUTA) totals.

    FUTA taxable wages are computed per employee, capped at the $7,000 wage
    base; payments above the cap are exempt.
    """
    stubs = _year_stubs(db, year)

    total_payments = Decimal("0")
    futa_tax = Decimal("0")
    # Running gross-paid per employee so the $7,000 cap can be applied as
    # later stubs push an employee over the wage base.
    paid_by_employee: dict[int, Decimal] = {}
    taxable_by_employee: dict[int, Decimal] = {}

    # Process in pay_date order so the per-employee wage base fills correctly.
    for s in sorted(stubs, key=lambda x: (x.pay_run.pay_date, x.id)):
        emp_id = s.employee_id
        gross = Decimal(str(s.gross_pay or 0))
        total_payments += gross
        futa_tax += Decimal(str(s.futa_tax or 0))

        prior = paid_by_employee.get(emp_id, Decimal("0"))
        if prior >= FUTA_WAGE_BASE:
            taxable = Decimal("0")
        elif prior + gross > FUTA_WAGE_BASE:
            taxable = FUTA_WAGE_BASE - prior
        else:
            taxable = gross
        paid_by_employee[emp_id] = prior + gross
        taxable_by_employee[emp_id] = (
            taxable_by_employee.get(emp_id, Decimal("0")) + taxable
        )

    total_taxable = sum(taxable_by_employee.values(), Decimal("0"))
    # Payments exempt from FUTA = total payments above the per-employee cap.
    exempt_payments = total_payments - total_taxable

    return {
        "year": year,
        "num_employees": len(paid_by_employee),
        "num_stubs": len(stubs),
        # Line 3 — total payments to all employees
        "total_payments": _q(total_payments),
        # Payments exempt from FUTA (over the $7,000 wage base)
        "exempt_payments": _q(exempt_payments),
        # Line 7 — total FUTA taxable wages
        "futa_taxable_wages": _q(total_taxable),
        "futa_wage_base": FUTA_WAGE_BASE,
        # Line 8 — FUTA tax for the year
        "total_futa_tax": _q(futa_tax),
    }


def generate_940_pdf(db, year: int, company: dict, audit: dict | None = None) -> bytes:
    """Render Form 940 to a PDF for the given year."""
    data = compute_940(db, year)
    template = _jinja_env.get_template("form_940.html")
    html_str = template.render(data=data, company=company or {}, audit=audit or {})
    return HTML(string=html_str, url_fetcher=_safe_url_fetcher).write_pdf()
