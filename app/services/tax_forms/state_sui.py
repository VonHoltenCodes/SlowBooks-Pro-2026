# ============================================================================
# State SUI — Quarterly State Unemployment Insurance return
# ----------------------------------------------------------------------------
# Aggregates PROCESSED pay stubs for a calendar quarter into the per-employee
# SUTA-taxable wages and tax that states require on their quarterly
# unemployment-insurance wage reports. Optionally filtered to a single state.
# ============================================================================

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.orm import joinedload, selectinload

from app.models.payroll import Employee, PayRun, PayStub, PayRunStatus
from app.services.accounting import _q


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
    per employee. When `state` is given, only stubs worked in it are
    included (the stub's work state, else the employee's), and an opening
    balance's rows by their own state.
    The quarter's opening-balance state rows (#433) count with the stubs.
    """
    from app.services import payroll_opening as po
    from app.services.payroll_service import _capped_wages, stub_taxable_wages
    from app.services.state_tax import get_engine

    start, end = _quarter_bounds(year, quarter)
    state = (state or "").strip().upper() or None
    # January 1 to the quarter's end: the earlier stubs are what each
    # state's SUTA base was already filled with
    year_stubs = (
        db.query(PayStub)
        .join(PayRun, PayStub.pay_run_id == PayRun.id)
        .join(Employee, PayStub.employee_id == Employee.id)
        .options(
            joinedload(PayStub.pay_run),
            joinedload(PayStub.employee),
            selectinload(PayStub.benefits),
        )
        .filter(PayRun.status == PayRunStatus.PROCESSED)
        .filter(PayRun.pay_date >= date(year, 1, 1))
        .filter(PayRun.pay_date <= end)
        .all()
    )
    year_stubs.sort(key=lambda x: (x.pay_run.pay_date, x.id))

    def stub_state(stub) -> str:
        # a stub's own work state (multi-state), else the employee's
        emp = stub.employee
        return (stub.work_state or (emp.work_state if emp else None) or "WA").upper()

    # SUTA-taxable wages are what the paycheck taxed: the stub's SUTA wages
    # (gross less Section 125 and the like) up to what is left of the
    # state's base, after the opening's SUTA wages for that state (#433)
    # and the year's earlier stubs worked there (ytd.wage_base_ytd).
    openings = po.openings_for_year(db, year)
    filled: dict[tuple[int, str], Decimal] = {}
    bases: dict[str, Decimal] = {}
    taxable_of: dict[int, Decimal] = {}
    for s in year_stubs:
        st = stub_state(s)
        key = (s.employee_id, st)
        if key not in filled:
            o = openings.get(s.employee_id)
            filled[key] = (
                po.wage_bases(o, st)["suta"]
                if po.counts_before(o, s.pay_run.pay_date)
                else Decimal("0")
            )
        if st not in bases:
            bases[st] = Decimal(str(get_engine(st).suta_wage_base or 0))
        wages = stub_taxable_wages(s)["fica"]
        taxable_of[s.id] = _capped_wages(wages, filled[key], bases[st])
        filled[key] += wages

    stubs = [
        s
        for s in year_stubs
        if s.pay_run.pay_date >= start and (not state or stub_state(s) == state)
    ]

    # Accumulate per employee: gross, the SUTA-taxable wages, and the SUTA
    # tax the payroll calculator recorded on each stub.
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
        entry["suta_taxable_wages"] += taxable_of[s.id]
        entry["suta_tax"] += suta

    # Opening balances (#433): the old system's wages, SUTA wages and SUTA
    # tax in this quarter, state by state, as it reported them.
    for emp_id, o in openings.items():
        for row in o.states:
            if row.quarter != quarter or (state and row.state != state):
                continue
            emp = o.employee
            wages = Decimal(str(row.wages or 0))
            suta = Decimal(str(row.suta_tax or 0))
            total_wages += wages
            total_suta_tax += suta
            entry = by_employee.get(emp_id)
            if entry is None:
                entry = {
                    "employee_id": emp_id,
                    "name": emp.full_name if emp else f"#{emp_id}",
                    "ssn_last_four": emp.ssn_last_four if emp else None,
                    "work_state": (
                        row.state if state else emp.work_state if emp else None
                    ),
                    "total_wages": Decimal("0"),
                    "suta_taxable_wages": Decimal("0"),
                    "suta_tax": Decimal("0"),
                }
                by_employee[emp_id] = entry
            entry["total_wages"] += wages
            entry["suta_taxable_wages"] += Decimal(str(row.suta_wages or 0))
            entry["suta_tax"] += suta

    breakdown = []
    total_taxable = Decimal("0")
    # Employees who received wages: a $0.00 stub pays no one.
    paid = [e for e in by_employee.values() if e["total_wages"] > 0]
    for entry in sorted(paid, key=lambda e: e["name"]):
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
        "num_employees": len(paid),
        "num_stubs": len(stubs),
        "total_wages": _q(total_wages),
        "total_suta_taxable_wages": _q(total_taxable),
        "total_suta_tax": _q(total_suta_tax),
        "employees": breakdown,
    }
