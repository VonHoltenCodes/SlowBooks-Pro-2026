# ============================================================================
# Form W-2 / W-3 — Wage and Tax Statement + Transmittal
# ----------------------------------------------------------------------------
# Builds per-employee annual wage statements (W-2) from PROCESSED pay stubs
# and the W-3 transmittal that totals every W-2 for the year. Box numbers
# follow the standard W-2 layout.
# ============================================================================

from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy.orm import joinedload

from app.models.payroll import Employee, PayRun, PayStub, PayRunStatus
from app.services.payroll_service import SS_WAGE_BASE
from app.services.pdf_service import _jinja_env, _safe_url_fetcher
from weasyprint import HTML

CENT = Decimal("0.01")


def _q(value) -> Decimal:
    """Coerce to Decimal and quantize to cents."""
    if not isinstance(value, Decimal):
        value = Decimal(str(value or 0))
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _year_stubs(db, year: int, employee_id: int | None = None) -> list[PayStub]:
    """PROCESSED pay stubs in the calendar year, optionally for one employee."""
    start = date(year, 1, 1)
    end = date(year, 12, 31)
    query = (
        db.query(PayStub)
        .join(PayRun, PayStub.pay_run_id == PayRun.id)
        .options(joinedload(PayStub.pay_run), joinedload(PayStub.employee))
        .filter(PayRun.status == PayRunStatus.PROCESSED)
        .filter(PayRun.pay_date >= start)
        .filter(PayRun.pay_date <= end)
    )
    if employee_id is not None:
        query = query.filter(PayStub.employee_id == employee_id)
    return query.all()


def _employee_box_totals(stubs: list[PayStub]) -> dict:
    """Sum a single employee's pay stubs into W-2 box amounts."""
    gross = Decimal("0")
    pretax = Decimal("0")
    federal_tax = Decimal("0")
    ss_tax = Decimal("0")
    medicare_tax = Decimal("0")
    state_tax = Decimal("0")
    medicare_wages = Decimal("0")

    for s in stubs:
        gross += Decimal(str(s.gross_pay or 0))
        pretax += Decimal(str(s.pretax_deductions or 0))
        federal_tax += Decimal(str(s.federal_tax or 0))
        ss_tax += Decimal(str(s.ss_tax or 0))
        medicare_tax += Decimal(str(s.medicare_tax or 0))
        state_tax += Decimal(str(s.state_tax or 0))
        medicare_wages += Decimal(str(s.gross_pay or 0))

    # Box 1 — federal wages are gross less pre-tax deductions.
    box1 = gross - pretax
    if box1 < 0:
        box1 = Decimal("0")
    # Box 3 — Social Security wages are capped at the annual wage base.
    box3 = min(gross, SS_WAGE_BASE)

    return {
        "box1_federal_wages": _q(box1),
        "box2_federal_tax_withheld": _q(federal_tax),
        "box3_ss_wages": _q(box3),
        "box4_ss_tax_withheld": _q(ss_tax),
        "box5_medicare_wages": _q(medicare_wages),
        "box6_medicare_tax_withheld": _q(medicare_tax),
        "box16_state_wages": _q(box1),
        "box17_state_income_tax": _q(state_tax),
        "gross_pay": _q(gross),
        "pretax_deductions": _q(pretax),
    }


def compute_w2(db, year: int, employee_id: int) -> dict:
    """Annual W-2 wage statement for one employee."""
    employee = db.query(Employee).filter(Employee.id == employee_id).first()
    stubs = _year_stubs(db, year, employee_id)
    boxes = _employee_box_totals(stubs)

    result: dict = {
        "year": year,
        "employee_id": employee_id,
        "num_stubs": len(stubs),
    }
    if employee is not None:
        result["employee"] = {
            "id": employee.id,
            "name": employee.full_name,
            "first_name": employee.first_name,
            "last_name": employee.last_name,
            "ssn_last_four": employee.ssn_last_four,
            "address1": employee.address1,
            "address2": employee.address2,
            "city": employee.city,
            "state": employee.state,
            "zip": employee.zip,
            "work_state": employee.work_state,
        }
    else:
        result["employee"] = None
    result.update(boxes)
    return result


def compute_all_w2(db, year: int) -> list[dict]:
    """W-2 statements for every employee with wages in the year."""
    stubs = _year_stubs(db, year)
    employee_ids = sorted({s.employee_id for s in stubs if s.employee_id})
    return [compute_w2(db, year, emp_id) for emp_id in employee_ids]


def compute_w3(db, year: int) -> dict:
    """W-3 transmittal — totals across every W-2 for the year."""
    w2s = compute_all_w2(db, year)

    totals = {
        "box1_federal_wages": Decimal("0"),
        "box2_federal_tax_withheld": Decimal("0"),
        "box3_ss_wages": Decimal("0"),
        "box4_ss_tax_withheld": Decimal("0"),
        "box5_medicare_wages": Decimal("0"),
        "box6_medicare_tax_withheld": Decimal("0"),
        "box16_state_wages": Decimal("0"),
        "box17_state_income_tax": Decimal("0"),
    }
    for w2 in w2s:
        for key in totals:
            totals[key] += Decimal(str(w2.get(key, 0)))

    return {
        "year": year,
        "num_w2": len(w2s),
        **{key: _q(value) for key, value in totals.items()},
    }


def generate_w2_pdf(
    db, year: int, employee_id: int, company: dict, audit: dict | None = None
) -> bytes:
    """Render a single employee's W-2 to a PDF. `audit` carries the audit-row
    id/hash/timestamp the footer prints; the caller computes it via
    services.document_audit."""
    data = compute_w2(db, year, employee_id)
    template = _jinja_env.get_template("w2.html")
    html_str = template.render(data=data, company=company or {}, audit=audit or {})
    return HTML(string=html_str, url_fetcher=_safe_url_fetcher).write_pdf()


def generate_w3_pdf(db, year: int, company: dict, audit: dict | None = None) -> bytes:
    """Render the W-3 transmittal (aggregate across all W-2s) to a PDF."""
    data = compute_w3(db, year)
    template = _jinja_env.get_template("w3.html")
    html_str = template.render(data=data, company=company or {}, audit=audit or {})
    return HTML(string=html_str, url_fetcher=_safe_url_fetcher).write_pdf()
