# ============================================================================
# Form W-2 / W-3 — Wage and Tax Statement + Transmittal
# ----------------------------------------------------------------------------
# Builds per-employee annual wage statements (W-2) from PROCESSED pay stubs
# and the W-3 transmittal that totals every W-2 for the year. Box numbers
# follow the standard W-2 layout.
# ============================================================================

from datetime import date
from decimal import Decimal

from sqlalchemy.orm import joinedload, selectinload

from app.models.payroll import Employee, PayRun, PayStub, PayRunStatus
from app.services.accounting import _q
from app.services.payroll_service import (
    SS_WAGE_BASE,
    ss_wage_base,
    stub_taxable_wages,
)
from app.services.pdf_service import _jinja_env, render_pdf


def _year_stubs(db, year: int, employee_id: int | None = None) -> list[PayStub]:
    """PROCESSED pay stubs in the calendar year, optionally for one employee."""
    start = date(year, 1, 1)
    end = date(year, 12, 31)
    query = (
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
    )
    if employee_id is not None:
        query = query.filter(PayStub.employee_id == employee_id)
    return query.all()


def _stub_state(stub: PayStub, employee: Employee | None) -> str:
    """The state a stub's wages are reported in: where it was worked (the
    stub's own work state, else the employee's), as the paycheck taxed it."""
    emp = employee or stub.employee
    return (stub.work_state or (emp.work_state if emp else None) or "WA").upper()


def _state_lines(stubs: list, opening, employee: Employee | None, legacy: bool):
    """Boxes 15-17, one line per state the employee had wages or state
    income tax withheld in: each stub in the state it was worked in, each
    opening state row in its own state (#433), an opening with no state
    rows in the employee's work state. Sorted by state."""
    lines: dict[str, dict] = {}

    def add(state: str, wages: Decimal, tax: Decimal) -> None:
        line = lines.setdefault(
            state, {"state": state, "wages": Decimal("0"), "tax": Decimal("0")}
        )
        line["wages"] += wages
        line["tax"] += tax

    for s in stubs:
        if legacy:
            wages = max(
                Decimal("0"),
                Decimal(str(s.gross_pay or 0)) - Decimal(str(s.pretax_deductions or 0)),
            )
        else:
            wages = stub_taxable_wages(s)["state"]
        add(_stub_state(s, employee), wages, Decimal(str(s.state_tax or 0)))
    if opening is not None:
        from app.services.payroll_opening import amount

        if opening.states:
            for row in opening.states:
                add(
                    row.state,
                    Decimal(str(row.wages or 0)),
                    Decimal(str(row.withholding or 0)),
                )
        else:
            # its federal wages, as a stub's would be
            emp = employee or opening.employee
            home = ((emp.work_state if emp else None) or "WA").upper()
            add(
                home,
                max(
                    Decimal("0"),
                    amount(opening, "gross") - amount(opening, "pretax_deductions"),
                ),
                Decimal("0"),
            )
    return [
        {
            "state": st,
            "box16_state_wages": _q(v["wages"]),
            "box17_state_income_tax": _q(v["tax"]),
        }
        for st, v in sorted(lines.items())
        if v["wages"] or v["tax"]
    ]


def _employee_box_totals(
    stubs: list[PayStub], opening=None, year: int | None = None, legacy: bool = False
) -> tuple[dict, list]:
    """Sum a single employee's pay stubs into W-2 box amounts, with the
    opening balance (#433) when there is one: the old system's wages and
    withholding for the part of the year before the switch. Returns the
    boxes and the state lines (boxes 15-17, one per state).

    Each stub's wages are as each tax measured them
    (payroll_service.stub_taxable_wages): box 1 drops the pre-tax amounts
    that reduce income tax, boxes 3 and 5 only those that reduce FICA
    (Section 125, HSA; never a 401(k)), box 16 those that reduce state
    income tax. `legacy` figures it as SlowBooks did before 2.29.0 (box 1
    and 16 gross less every pre-tax amount, boxes 3 and 5 gross, the 2026
    Social Security base): Form history's Check uses it (document_check)."""
    gross = Decimal("0")
    pretax = Decimal("0")
    federal_wages = Decimal("0")
    federal_tax = Decimal("0")
    ss_tax = Decimal("0")
    medicare_tax = Decimal("0")
    ss_wages = Decimal("0")
    medicare_wages = Decimal("0")

    for s in stubs:
        stub_gross = Decimal(str(s.gross_pay or 0))
        stub_pretax = Decimal(str(s.pretax_deductions or 0))
        gross += stub_gross
        pretax += stub_pretax
        federal_tax += Decimal(str(s.federal_tax or 0))
        ss_tax += Decimal(str(s.ss_tax or 0))
        medicare_tax += Decimal(str(s.medicare_tax or 0))
        if legacy:
            federal_wages += stub_gross - stub_pretax
            ss_wages += stub_gross
            medicare_wages += stub_gross
        else:
            wages = stub_taxable_wages(s)
            federal_wages += wages["federal"]
            ss_wages += wages["fica"]
            medicare_wages += wages["fica"]

    if opening is not None:
        from app.services.payroll_opening import amount

        o_gross = amount(opening, "gross")
        o_pretax = amount(opening, "pretax_deductions")
        gross += o_gross
        pretax += o_pretax
        federal_wages += o_gross - o_pretax
        federal_tax += amount(opening, "federal_tax")
        ss_tax += amount(opening, "ss_tax")
        medicare_tax += amount(opening, "medicare_tax")
        # the old system's taxable wages, as it figured them
        ss_wages += amount(opening, "ss_wages")
        medicare_wages += amount(opening, "medicare_wages")

    employee = stubs[0].employee if stubs else (opening.employee if opening else None)
    lines = _state_lines(stubs, opening, employee, legacy)

    # Box 1 — federal wages are gross less pre-tax deductions.
    box1 = max(Decimal("0"), federal_wages)
    # Box 3 — Social Security wages are capped at the year's wage base.
    base = SS_WAGE_BASE if legacy else ss_wage_base(year)
    box3 = min(ss_wages, base)

    boxes = {
        "box1_federal_wages": _q(box1),
        "box2_federal_tax_withheld": _q(federal_tax),
        "box3_ss_wages": _q(box3),
        "box4_ss_tax_withheld": _q(ss_tax),
        "box5_medicare_wages": _q(medicare_wages),
        "box6_medicare_tax_withheld": _q(medicare_tax),
        # the year's totals over every state line (the W-3 adds these)
        "box16_state_wages": _q(
            sum((ln["box16_state_wages"] for ln in lines), Decimal("0"))
        ),
        "box17_state_income_tax": _q(
            sum((ln["box17_state_income_tax"] for ln in lines), Decimal("0"))
        ),
        "gross_pay": _q(gross),
        "pretax_deductions": _q(pretax),
    }
    return boxes, lines


def _employee_dict(employee: Employee | None) -> dict | None:
    """Serialize the employee block embedded in a W-2 result."""
    if employee is None:
        return None
    return {
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


def _build_w2(
    year: int,
    employee_id: int,
    employee: Employee | None,
    stubs: list,
    opening=None,
    legacy: bool = False,
) -> dict:
    """Assemble one W-2 result dict from already-fetched stubs (and the
    employee's opening balance for the year, #433).

    Boxes 15-17 go one line per state. With wages in one state (or none)
    the result keeps the shape it always had — box16/box17 and no
    `states` — so the hash of a W-2 generated before per-state lines is
    the same (Form history's Check). With two or more it carries
    `states`, one line each, and box16/box17 stay the totals the W-3 adds.
    `legacy` (Check only) figures it as before 2.29.0, never with
    `states`."""
    result: dict = {
        "year": year,
        "employee_id": employee_id,
        "num_stubs": len(stubs),
        "employee": _employee_dict(employee),
    }
    boxes, lines = _employee_box_totals(stubs, opening, year, legacy)
    result.update(boxes)
    if len(lines) > 1 and not legacy:
        result["states"] = lines
    return result


def compute_w2(db, year: int, employee_id: int, legacy: bool = False) -> dict:
    """Annual W-2 wage statement for one employee."""
    from app.services.payroll_opening import opening_for

    employee = db.query(Employee).filter(Employee.id == employee_id).first()
    stubs = _year_stubs(db, year, employee_id)
    opening = opening_for(db, employee_id, year)
    return _build_w2(year, employee_id, employee, stubs, opening, legacy)


def w2_state_lines(db, year: int, employee_id: int) -> list[dict]:
    """The employee's boxes 15-17, one line per state, also when there is
    only one (compute_w2 carries them only for two or more)."""
    from app.services.payroll_opening import opening_for

    employee = db.query(Employee).filter(Employee.id == employee_id).first()
    stubs = _year_stubs(db, year, employee_id)
    _boxes, lines = _employee_box_totals(
        stubs, opening_for(db, employee_id, year), year
    )
    if not lines and employee is not None and employee.work_state:
        # no state wages: box 15 still names the work state, as it did
        lines = [
            {
                "state": employee.work_state.upper(),
                "box16_state_wages": Decimal("0.00"),
                "box17_state_income_tax": Decimal("0.00"),
            }
        ]
    return lines


STATES_PER_FORM = 2


def w2_forms(data: dict, lines: list[dict]) -> list[dict]:
    """The W-2 forms one employee's statement prints on. A W-2 has room
    for two states (boxes 15-17, two lines); a third goes on an additional
    W-2, as the IRS instructions say, with only the identifying boxes and
    the state lines — the federal boxes are reported once, on the first."""
    chunks = [
        lines[i : i + STATES_PER_FORM] for i in range(0, len(lines), STATES_PER_FORM)
    ] or [[]]
    return [
        {"number": n + 1, "of": len(chunks), "federal": n == 0, "states": chunk}
        for n, chunk in enumerate(chunks)
    ]


def compute_all_w2(db, year: int, legacy: bool = False) -> list[dict]:
    """W-2 statements for every employee with wages in the year.

    Single-pass: fetch every PROCESSED stub for the year ONCE (with the
    employee joined-loaded), bucket by employee_id in Python, and build each
    W-2 from the in-memory group — no per-employee re-query (fixes the N+1
    that compute_w2-in-a-loop would otherwise produce).
    """
    stubs = _year_stubs(db, year)

    by_employee: dict[int, list] = {}
    employees: dict[int, Employee] = {}
    for s in stubs:
        if not s.employee_id:
            continue
        by_employee.setdefault(s.employee_id, []).append(s)
        if s.employee_id not in employees and s.employee is not None:
            employees[s.employee_id] = s.employee

    # An employee paid only before the switch (left in March) has an
    # opening balance and no stub, and still gets a W-2 (#433).
    from app.services.payroll_opening import openings_for_year

    openings = openings_for_year(db, year)
    for emp_id, o in openings.items():
        by_employee.setdefault(emp_id, [])
        if emp_id not in employees and o.employee is not None:
            employees[emp_id] = o.employee

    w2s = [
        _build_w2(
            year,
            emp_id,
            employees.get(emp_id),
            by_employee[emp_id],
            openings.get(emp_id),
            legacy,
        )
        for emp_id in sorted(by_employee)
    ]
    # No wages, no W-2: a $0.00 stub is not a wage statement, and the W-3
    # counts the W-2s it transmits (2.17.3: "2" for one paid employee).
    return [w2 for w2 in w2s if _has_wages(w2)]


_AMOUNT_KEYS = (
    "gross_pay",
    "box1_federal_wages",
    "box2_federal_tax_withheld",
    "box3_ss_wages",
    "box4_ss_tax_withheld",
    "box5_medicare_wages",
    "box6_medicare_tax_withheld",
    "box16_state_wages",
    "box17_state_income_tax",
)


def _has_wages(w2: dict) -> bool:
    return any(w2.get(k) for k in _AMOUNT_KEYS)


def compute_w3(db, year: int, legacy: bool = False) -> dict:
    """W-3 transmittal — totals across every W-2 for the year (box 16
    and 17 over every state line of every W-2)."""
    w2s = compute_all_w2(db, year, legacy)

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
    return render_pdf(render_w2_html(db, year, employee_id, company, audit))


def render_w2_html(
    db, year: int, employee_id: int, company: dict, audit: dict | None = None
) -> str:
    """The W-2's HTML: one form per two states (w2_forms)."""
    data = compute_w2(db, year, employee_id)
    forms = w2_forms(data, w2_state_lines(db, year, employee_id))
    template = _jinja_env.get_template("w2.html")
    return template.render(
        data=data, forms=forms, company=company or {}, audit=audit or {}
    )


def generate_w3_pdf(db, year: int, company: dict, audit: dict | None = None) -> bytes:
    """Render the W-3 transmittal (aggregate across all W-2s) to a PDF."""
    data = compute_w3(db, year)
    template = _jinja_env.get_template("w3.html")
    html_str = template.render(data=data, company=company or {}, audit=audit or {})
    return render_pdf(html_str)
