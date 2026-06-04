# ============================================================================
# State SUI — Quarterly State Unemployment Insurance return
# ----------------------------------------------------------------------------
# Aggregates PROCESSED pay stubs for a calendar quarter into the per-employee
# SUTA-taxable wages and tax that states require on their quarterly
# unemployment-insurance wage reports. Optionally filtered to a single state.
# ============================================================================

from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy.orm import joinedload

from app.models.payroll import Employee, PayRun, PayStub, PayRunStatus

CENT = Decimal("0.01")


def _q(value) -> Decimal:
    """Coerce to Decimal and quantize to cents."""
    if not isinstance(value, Decimal):
        value = Decimal(str(value or 0))
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _quarter_bounds(year: int, quarter: int) -> tuple[date, date]:
    """Return (first_day, last_day) for a calendar quarter."""
    if quarter not in (1, 2, 3, 4):
        raise ValueError(f"quarter must be 1-4, got {quarter!r}")
    start_month = (quarter - 1) * 3 + 1
    start = date(year, start_month, 1)
    if quarter == 4:
        end = date(year, 12, 31)
    else:
        end = date(year, start_month + 3, 1) - timedelta(days=1)
    return start, end


def compute_sui(db, year: int, quarter: int, state: str | None = None) -> dict:
    """Aggregate quarterly state unemployment (SUI/SUTA) totals.

    SUTA is an employer-side tax; the report lists SUTA-taxable wages and tax
    per employee. When `state` is given, only employees whose `work_state`
    matches are included.
    """
    start, end = _quarter_bounds(year, quarter)
    stubs = (
        db.query(PayStub)
        .join(PayRun, PayStub.pay_run_id == PayRun.id)
        .join(Employee, PayStub.employee_id == Employee.id)
        .options(joinedload(PayStub.pay_run), joinedload(PayStub.employee))
        .filter(PayRun.status == PayRunStatus.PROCESSED)
        .filter(PayRun.pay_date >= start)
        .filter(PayRun.pay_date <= end)
    )
    if state:
        stubs = stubs.filter(Employee.work_state == state)
    stubs = stubs.all()

    # Accumulate per employee. SUTA-taxable wages are taken as the wages that
    # actually produced SUTA tax — we report gross alongside the SUTA tax that
    # the payroll calculator recorded on each stub.
    by_employee: dict[int, dict] = {}
    total_wages = Decimal("0")
    total_suta_tax = Decimal("0")

    for s in stubs:
        emp = s.employee
        emp_id = s.employee_id
        gross = Decimal(str(s.gross_pay or 0))
        suta = Decimal(str(s.suta_tax or 0))
        total_wages += gross
        total_suta_tax += suta

        entry = by_employee.get(emp_id)
        if entry is None:
            entry = {
                "employee_id": emp_id,
                "name": emp.full_name if emp else f"#{emp_id}",
                "ssn_last_four": emp.ssn_last_four if emp else None,
                "work_state": emp.work_state if emp else None,
                "total_wages": Decimal("0"),
                "suta_taxable_wages": Decimal("0"),
                "suta_tax": Decimal("0"),
            }
            by_employee[emp_id] = entry
        entry["total_wages"] += gross
        # A stub contributes to SUTA-taxable wages only when it produced
        # SUTA tax (i.e. the employee was still under the state wage base).
        if suta > 0:
            entry["suta_taxable_wages"] += gross
        entry["suta_tax"] += suta

    breakdown = []
    total_taxable = Decimal("0")
    for entry in sorted(by_employee.values(), key=lambda e: e["name"]):
        total_taxable += entry["suta_taxable_wages"]
        breakdown.append(
            {
                "employee_id": entry["employee_id"],
                "name": entry["name"],
                "ssn_last_four": entry["ssn_last_four"],
                "work_state": entry["work_state"],
                "total_wages": _q(entry["total_wages"]),
                "suta_taxable_wages": _q(entry["suta_taxable_wages"]),
                "suta_tax": _q(entry["suta_tax"]),
            }
        )

    return {
        "year": year,
        "quarter": quarter,
        "state": state,
        "num_employees": len(by_employee),
        "num_stubs": len(stubs),
        "total_wages": _q(total_wages),
        "total_suta_taxable_wages": _q(total_taxable),
        "total_suta_tax": _q(total_suta_tax),
        "employees": breakdown,
    }
