import json
from datetime import date
from decimal import Decimal

from fastapi import Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.routes._helpers import clamp_pagination
from app.routes.payroll._router import router
from app.services.direct_deposit import pays_by_direct_deposit
from app.routes.payroll.ytd import _ytd_supplemental, employee_ytd, wage_base_ytd
from app.models.payroll import (
    PayRun,
    PayStub,
    PayRunStatus,
    PayRunType,
    Employee,
    periods_per_year,
)
from app.models.time_entries import TimeEntry, TimeEntryStatus
from app.models.deductions import GarnishmentOrder
from app.models.accounts import Account
from app.schemas.payroll import PayRunCreate, PayRunResponse
from app.schemas.deductions import GrossUpRequest, GrossUpResponse
from app.services.payroll_service import _q, calculate_withholdings, wage_base_note
from app.services.accounting import create_journal_entry
from app.services.garnishment import (
    GarnishmentSpec,
    apply_garnishments,
    compute_disposable_earnings,
    total_garnished,
)
from app.services.gross_up import gross_up
from app.services.state_tax.reciprocity import withholding_state
from app.services import benefits_engine


def _with_employee_names(run: PayRun) -> PayRunResponse:
    resp = PayRunResponse.model_validate(run)
    for stub_resp, stub in zip(resp.stubs, run.stubs):
        if stub.employee:
            stub_resp.employee_name = stub.employee.full_name
            stub_resp.direct_deposit = pays_by_direct_deposit(stub.employee)
    return resp


@router.get("", response_model=list[PayRunResponse])
def list_pay_runs(skip: int = 0, limit: int = 200, db: Session = Depends(get_db)):
    skip, limit = clamp_pagination(skip, limit, max_limit=500)
    runs = (
        db.query(PayRun)
        .options(
            joinedload(PayRun.stubs)
            .joinedload(PayStub.employee)
            .selectinload(Employee.bank_accounts)
        )
        .order_by(PayRun.pay_date.desc(), PayRun.id.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    return [_with_employee_names(run) for run in runs]


@router.get("/{run_id}", response_model=PayRunResponse)
def get_pay_run(run_id: int, db: Session = Depends(get_db)):
    run = (
        db.query(PayRun)
        .options(
            joinedload(PayRun.stubs)
            .joinedload(PayStub.employee)
            .selectinload(Employee.bank_accounts)
        )
        .filter(PayRun.id == run_id)
        .first()
    )
    if not run:
        raise HTTPException(status_code=404, detail="Pay run not found")
    return _with_employee_names(run)


def _garnishment_specs(db: Session, employee_id: int) -> list:
    specs = []
    rows = (
        db.query(GarnishmentOrder)
        .filter(
            GarnishmentOrder.employee_id == employee_id,
            GarnishmentOrder.is_active,
        )  # noqa: E712
        .all()
    )
    for g in rows:
        specs.append(
            GarnishmentSpec(
                order_id=g.id,
                garnishment_type=g.garnishment_type.value,
                calc_method=g.calc_method.value,
                amount=Decimal(str(g.amount or 0)),
                priority=g.priority or 0,
                supports_secondary_family=bool(g.supports_secondary_family),
                in_arrears_12_weeks=bool(g.in_arrears_12_weeks),
            )
        )
    return specs


def _last_regular_gross(db: Session, employee_id: int, before: date) -> Decimal:
    """Most recent regular-run gross pay — the base for aggregate supplemental."""
    stub = (
        db.query(PayStub)
        .join(PayRun, PayStub.pay_run_id == PayRun.id)
        .filter(
            PayStub.employee_id == employee_id,
            PayRun.run_type == PayRunType.REGULAR,
            PayRun.status != PayRunStatus.VOID,
            PayRun.pay_date < before,
        )
        .order_by(PayRun.pay_date.desc())
        .first()
    )
    return Decimal(str(stub.gross_pay)) if stub else Decimal("0")


def _imported_time(db: Session, employee_id: int, start: date, end: date):
    """(count, hours) of the employee's time in the period that came from
    QuickBooks (source set): approved history a pay run never pays."""
    rows = (
        db.query(TimeEntry)
        .filter(
            TimeEntry.employee_id == employee_id,
            TimeEntry.source.isnot(None),
            TimeEntry.date >= start,
            TimeEntry.date <= end,
        )
        .all()
    )
    hours = sum(
        (
            Decimal(str(te.hours_regular or 0))
            + Decimal(str(te.hours_overtime or 0))
            + Decimal(str(te.hours_doubletime or 0))
            for te in rows
        ),
        Decimal("0"),
    )
    return len(rows), float(hours)


def _unapproved_time(db: Session, employee_id: int, start: date, end: date):
    """(count, hours) of the employee's time entries in the period that are
    still waiting for approval — draft or submitted, not yet paid."""
    waiting = (
        db.query(TimeEntry)
        .filter(
            TimeEntry.employee_id == employee_id,
            TimeEntry.status.in_([TimeEntryStatus.DRAFT, TimeEntryStatus.SUBMITTED]),
            TimeEntry.pay_run_id.is_(None),
            TimeEntry.source.is_(
                None
            ),  # time imported from QuickBooks is history, never paid here (#323)
            TimeEntry.date >= start,
            TimeEntry.date <= end,
        )
        .all()
    )
    hours = sum(
        (
            Decimal(str(te.hours_regular or 0))
            + Decimal(str(te.hours_overtime or 0))
            + Decimal(str(te.hours_doubletime or 0))
            for te in waiting
        ),
        Decimal("0"),
    )
    return len(waiting), _q(hours)


def _entries(count: int) -> str:
    return f"{count} time entry" if count == 1 else f"{count} time entries"


@router.post("", response_model=PayRunResponse, status_code=201)
def create_pay_run(data: PayRunCreate, db: Session = Depends(get_db)):
    try:
        run_type = PayRunType(data.run_type)
    except ValueError:
        raise HTTPException(
            status_code=400, detail=f"Invalid run_type: {data.run_type}"
        )
    if not data.stubs:
        # A run with nobody on it used to be a 201 that paid no one. An
        # agent told to "run payroll for every period" posted 26 of them
        # and saw 26 successes (2.9.0 gate). Refuse, and name the roster
        # so the caller knows who a stub is expected for.
        roster = (
            db.query(Employee)
            .filter(Employee.is_active)
            .order_by(Employee.last_name, Employee.first_name)
            .all()
        )
        names = ", ".join(f"{e.first_name} {e.last_name} (id {e.id})" for e in roster)
        raise HTTPException(
            status_code=422,
            detail=(
                "stubs is empty: a pay run needs one stub per employee to pay. "
                + (
                    f"Active employees: {names}."
                    if roster
                    else "There are no active employees."
                )
            ),
        )

    run = PayRun(
        period_start=data.period_start,
        period_end=data.period_end,
        pay_date=data.pay_date,
        run_type=run_type,
    )
    db.add(run)
    db.flush()

    year = data.pay_date.year
    total_gross = total_taxes = total_net = total_employer = Decimal("0")
    total_employer_benefits = Decimal("0")
    period = f"{data.period_start} to {data.period_end}"
    # A stub that pays nothing is refused, all at once with every name
    # (2.17.3: "Use approved time entries" made a $0.00 stub for an hourly
    # employee whose time was never approved, and said nothing). Time left
    # unapproved beside approved time is paid later; the run says so.
    refused: list[str] = []
    warnings: list[str] = []

    for stub_input in data.stubs:
        emp = db.query(Employee).filter(Employee.id == stub_input.employee_id).first()
        if not emp:
            raise HTTPException(
                status_code=404, detail=f"Employee {stub_input.employee_id} not found"
            )

        reg = ot = dt = Decimal("0")
        rate = Decimal(str(emp.pay_rate or 0))
        time_entry_ids: list[int] = []
        waiting = (0, Decimal("0"))

        if stub_input.gross_override is not None:
            gross = Decimal(str(stub_input.gross_override))
        elif emp.pay_type.value == "salary":
            # Bug 3 fix: divide by the employee's actual pay frequency, not a
            # hardcoded 26.
            gross = rate / periods_per_year(emp.pay_frequency)
        else:
            if stub_input.use_time_entries:
                entries = (
                    db.query(TimeEntry)
                    .filter(
                        TimeEntry.employee_id == emp.id,
                        TimeEntry.status == TimeEntryStatus.APPROVED,
                        TimeEntry.pay_run_id.is_(None),
                        TimeEntry.source.is_(None),  # imported history (#323)
                        TimeEntry.date >= data.period_start,
                        TimeEntry.date <= data.period_end,
                    )
                    .all()
                )
                for te in entries:
                    reg += te.hours_regular or 0
                    ot += te.hours_overtime or 0
                    dt += te.hours_doubletime or 0
                    time_entry_ids.append(te.id)
                waiting = _unapproved_time(
                    db, emp.id, data.period_start, data.period_end
                )
            else:
                ot = Decimal(str(stub_input.overtime_hours or 0))
                dt = Decimal(str(stub_input.doubletime_hours or 0))
                if stub_input.regular_hours is not None:
                    reg = Decimal(str(stub_input.regular_hours))
                else:
                    reg = Decimal(str(stub_input.hours or 0)) - ot - dt
                if reg < 0:
                    reg = Decimal("0")
            gross = reg * rate + ot * rate * Decimal("1.5") + dt * rate * Decimal("2")

        gross = _q(gross)
        if gross < 0:
            gross = Decimal("0")

        total_hours = reg + ot + dt

        reimbursements = _q(Decimal(str(stub_input.reimbursements or 0)))

        name = emp.full_name
        if gross == 0 and reimbursements == 0:
            if stub_input.use_time_entries and emp.pay_type.value != "salary":
                if waiting[0]:
                    refused.append(
                        f"{name} has no approved time from {period}: "
                        f"{_entries(waiting[0])} ({waiting[1]} hours) "
                        "waiting for approval under Time Entries. Approve them, "
                        f"or leave {name} out of this run."
                    )
                else:
                    imported = _imported_time(
                        db, emp.id, data.period_start, data.period_end
                    )
                    if imported[0]:
                        # the only approved time there is QuickBooks' history,
                        # which a pay run never pays (2.23.4 gate, NEW-131)
                        refused.append(
                            f"{name} has no time to pay from {period}: the "
                            f"{_entries(imported[0])} there ({imported[1]} hours) "
                            "came from QuickBooks, which paid them, and a pay "
                            "run never pays imported time. Log and approve "
                            f"time here, or leave {name} out of this run."
                        )
                    else:
                        refused.append(
                            f"{name} has no approved time from {period}. Log and "
                            f"approve it under Time Entries, or leave {name} out "
                            "of this run."
                        )
            else:
                refused.append(
                    f"{name} would be paid $0.00. Enter hours or an amount, "
                    f"or leave {name} out of this run."
                )
            continue
        if waiting[0]:
            warnings.append(
                f"{name}: {_entries(waiting[0])} ({waiting[1]} hours) from "
                f"{period} {'is' if waiting[0] == 1 else 'are'} not approved "
                "and not paid in this run. Approve them under Time Entries to "
                "pay them in a later run."
            )

        # Multi-state: per-stub work location, with reciprocity deciding which
        # state's income tax is actually withheld.
        work_state = (stub_input.work_state or emp.work_state or "WA").upper()
        wh_state = withholding_state(work_state, emp.residence_state)

        regular_wages = Decimal("0")
        if stub_input.supplemental and stub_input.supplemental_method == "aggregate":
            regular_wages = _last_regular_gross(db, emp.id, data.pay_date)

        ytd = employee_ytd(db, emp.id, year, before=data.pay_date)
        # the caps measure the opening's taxable wages, SUTA per state (#433)
        bases = wage_base_ytd(db, emp.id, year, work_state, before=data.pay_date)
        ytd_suppl = (
            _ytd_supplemental(db, emp.id, year, before=data.pay_date)
            if stub_input.supplemental
            else Decimal("0")
        )

        # Benefits engine: every code attached to the employee (assignments,
        # then the group), in sequence, at the rate in force on the period
        # end date. Ad-hoc amounts on the request are added on top and
        # treated as reducing income-tax wages (pre-tax) or nothing (post).
        ben = benefits_engine.compute(
            db,
            emp,
            gross,
            total_hours,
            data.period_start,
            data.period_end,
            year,
            ytd_gross_before=ytd["gross"],
        )
        adhoc_pretax = _q(Decimal(str(stub_input.pretax_deductions or 0)))
        adhoc_posttax = _q(Decimal(str(stub_input.posttax_deductions or 0)))
        pretax = ben.pretax_total + adhoc_pretax
        posttax = ben.posttax_total + adhoc_posttax

        result = calculate_withholdings(
            gross,
            pay_frequency=emp.pay_frequency.value if emp.pay_frequency else "biweekly",
            filing_status=emp.filing_status.value if emp.filing_status else "single",
            multiple_jobs=bool(emp.multiple_jobs),
            dependents_amount=emp.dependents_amount or 0,
            other_income_annual=emp.other_income_annual or 0,
            deductions_annual=emp.deductions_annual or 0,
            extra_withholding=emp.extra_withholding or 0,
            ytd_gross=ytd["gross"],
            work_state=work_state,
            withholding_state=wh_state,
            wc_class_code=emp.wc_class_code,
            state_allowances=emp.state_allowances or 0,
            state_extra_withholding=emp.state_extra_withholding or 0,
            state_rate_override=emp.state_rate_override,
            local_tax_rate=emp.local_tax_rate,
            hours=total_hours,
            pretax_deductions=ben.pretax_federal + adhoc_pretax,
            pretax_state=ben.pretax_state + adhoc_pretax,
            pretax_fica=ben.pretax_fica,
            supplemental=bool(stub_input.supplemental),
            supplemental_method=stub_input.supplemental_method or "flat",
            regular_wages=regular_wages,
            ytd_supplemental=ytd_suppl,
            ytd_bases=bases,
            year=year,
        )

        # Garnishments are applied to disposable earnings (gross less the
        # legally-required tax withholding) under CCPA limits.
        disposable = compute_disposable_earnings(gross, result["total_employee_tax"])
        weeks = max(1, round(52 / periods_per_year(emp.pay_frequency)))
        garn_results = apply_garnishments(
            disposable, _garnishment_specs(db, emp.id), weeks_in_period=weeks
        )
        garnish_total = total_garnished(garn_results)

        # Post-tax deductions come out of what is left after pre-tax codes,
        # taxes and garnishments (court orders outrank voluntary codes).
        # The first pass could not know the taxes; when the post-tax total
        # would overdraw the check, evaluate the codes again with the real
        # room and trim the ad-hoc amount to whatever remains. Pre-tax
        # amounts are identical on both passes, so the withholdings stand.
        available = _q(gross - pretax - result["total_employee_tax"] - garnish_total)
        if posttax > available:
            ben = benefits_engine.compute(
                db,
                emp,
                gross,
                total_hours,
                data.period_start,
                data.period_end,
                year,
                ytd_gross_before=ytd["gross"],
                posttax_available=available,
            )
            adhoc_posttax = _q(
                max(Decimal("0"), min(adhoc_posttax, available - ben.posttax_total))
            )
            posttax = ben.posttax_total + adhoc_posttax

        # Net: gross less every tax, every employee-side benefit amount
        # (pre- and post-tax, engine and ad-hoc), garnishments; plus
        # non-taxable reimbursements. Computed here rather than from the
        # calculator's `net` because a pre-tax code that reduces only FICA
        # still comes out of the check.
        net = _q(
            gross
            - result["total_employee_tax"]
            - pretax
            - posttax
            - garnish_total
            + reimbursements
        )

        detail = {k: str(v) for k, v in result["detail"].items()}
        detail["pretax_deductions"] = str(pretax)
        for gr in garn_results:
            detail[f"garnishment:{gr.garnishment_type}:{gr.order_id}"] = str(gr.amount)
        if posttax:
            detail["posttax_deductions"] = str(posttax)
        if reimbursements:
            detail["reimbursements"] = str(reimbursements)
        for ln in ben.lines:
            if ln.employee_amount:
                detail[f"benefit:{ln.code.code}"] = str(ln.employee_amount)
            if ln.employer_amount:
                detail[f"employer_benefit:{ln.code.code}"] = str(ln.employer_amount)
            if ln.note:
                detail[f"benefit:{ln.code.code}:note"] = ln.note
        if net < 0:
            # Only a garnishment stack beyond disposable earnings can get
            # here now; refuse rather than post an unbalanced payroll entry.
            raise HTTPException(
                status_code=422,
                detail=(
                    f"{emp.full_name}: deductions and garnishments exceed pay "
                    f"(net would be {net}). Reduce them before running payroll."
                ),
            )
        if adhoc_pretax:
            detail["other_pretax_deductions"] = str(adhoc_pretax)
        if adhoc_posttax:
            detail["other_posttax_deductions"] = str(adhoc_posttax)

        stub = PayStub(
            pay_run_id=run.id,
            employee_id=emp.id,
            hours=total_hours,
            regular_hours=reg,
            overtime_hours=ot,
            doubletime_hours=dt,
            gross_pay=gross,
            federal_tax=result["federal"],
            state_tax=result["state_income"],
            state_other_employee=result["state_other_employee"],
            ss_tax=result["ss"],
            medicare_tax=result["medicare"],
            pretax_deductions=pretax,
            posttax_deductions=posttax,
            garnishments=garnish_total,
            reimbursements=reimbursements,
            work_state=work_state,
            net_pay=net,
            employer_ss_tax=result["employer_ss"],
            employer_medicare_tax=result["employer_medicare"],
            futa_tax=result["futa"],
            suta_tax=result["suta"],
            state_other_employer=result["state_other_employer"],
            employer_benefits=ben.employer_total,
            detail_json=json.dumps(detail),
        )
        db.add(stub)
        db.flush()
        # Snapshot the resolved rules + amounts on the stub and bump the
        # YTD accumulators / loan balances.
        benefits_engine.record_on_stub(db, stub, ben, year)
        total_employer_benefits += ben.employer_total

        # Mark the consumed time entries so they cannot be paid twice.
        for te_id in time_entry_ids:
            te = db.query(TimeEntry).filter(TimeEntry.id == te_id).first()
            if te:
                te.pay_run_id = run.id

        total_gross += gross
        total_taxes += result["total_employee_tax"]
        total_employer += result["total_employer_tax"]
        total_net += net

    if refused:
        # Nothing is written: the session is never committed.
        raise HTTPException(status_code=422, detail=" ".join(refused))
    note = wage_base_note(year)
    if note:
        warnings.append(note)

    run.total_gross = total_gross
    run.total_taxes = total_taxes
    run.total_employer_taxes = total_employer
    run.total_employer_benefits = total_employer_benefits
    run.total_net = total_net

    db.commit()
    db.refresh(run)
    resp = _with_employee_names(run)
    resp.warnings = warnings
    return resp


@router.post("/{run_id}/process")
def process_pay_run(
    run_id: int,
    acknowledge_stale: bool = Query(False),
    db: Session = Depends(get_db),
):
    """Process a pay run — posts the payroll journal entry."""
    from app.services.closing_date import check_closing_date

    run = (
        db.query(PayRun)
        .options(joinedload(PayRun.stubs).joinedload(PayStub.benefits))
        .filter(PayRun.id == run_id)
        .first()
    )
    if not run:
        raise HTTPException(status_code=404, detail="Pay run not found")
    if run.status == PayRunStatus.PROCESSED:
        raise HTTPException(status_code=400, detail="Pay run already processed")
    if run.status == PayRunStatus.VOID:
        raise HTTPException(status_code=400, detail="Pay run is void")
    if run.stale_reason and not acknowledge_stale:
        # an opening YTD changed after the draft was figured (#433)
        raise HTTPException(
            status_code=409,
            detail=run.stale_reason
            + " To process it as figured, send acknowledge_stale=1.",
        )
    # Pay-run processing posts a dated JE; subject to closing-date enforcement
    # like every other JE-posting route. Without this, an operator can process
    # a backdated pay run into a closed period.
    check_closing_date(db, run.pay_date)

    def _acct(num, fallback=None):
        a = db.query(Account).filter(Account.account_number == num).first()
        if a:
            return a.id
        if fallback:
            return _acct(fallback)
        return None

    # Expense accounts (fall back to generic expense if payroll accounts are
    # missing on an un-migrated company file).
    wage_expense = _acct("6110", "6000")
    payroll_tax_expense = _acct("6120", "6000")
    reimb_expense = _acct("6140", "6950")
    bank = _acct("1000")
    # Liability payables fall back to the umbrella "Payroll Liabilities" (2300).
    fed = _acct("2310", "2300")
    state_wh = _acct("2320", "2300")
    ss_acct = _acct("2330", "2300")
    medicare_acct = _acct("2340", "2300")
    futa_acct = _acct("2350", "2300")
    suta_acct = _acct("2360", "2300")
    other_acct = _acct("2370", "2300")
    benefits_expense = _acct("6150", "6120") or payroll_tax_expense

    if not wage_expense or not bank:
        raise HTTPException(
            status_code=400,
            detail="Required payroll accounts not found (need 6110/6000 and 1000).",
        )

    def _s(field):
        return sum((getattr(s, field) or Decimal("0")) for s in run.stubs)

    total_gross = _s("gross_pay")
    total_fed = _s("federal_tax")
    total_state = _s("state_tax")
    total_ss = _s("ss_tax") + _s("employer_ss_tax")
    total_medicare = _s("medicare_tax") + _s("employer_medicare_tax")
    total_futa = _s("futa_tax")
    total_suta = _s("suta_tax")
    # Benefit codes post to their own liability (and expense) accounts; the
    # ad-hoc pre/post-tax amounts typed on the stub have no code and stay in
    # the umbrella "other deductions" payable with garnishments.
    groups = benefits_engine.gl_groups(run)
    adhoc_deductions = (
        _s("pretax_deductions") + _s("posttax_deductions") - groups.employee_total
    )
    total_other = (
        _s("state_other_employee")
        + _s("state_other_employer")
        + adhoc_deductions
        + _s("garnishments")
        + groups.unmapped_liability
    )
    total_benefit_expense_unmapped = groups.unmapped_expense
    total_employer = (
        _s("employer_ss_tax")
        + _s("employer_medicare_tax")
        + total_futa
        + total_suta
        + _s("state_other_employer")
    )
    total_reimb = _s("reimbursements")
    total_net = _s("net_pay")

    lines = []
    if total_gross > 0:
        lines.append(
            {
                "account_id": wage_expense,
                "debit": total_gross,
                "credit": Decimal("0"),
                "description": "Gross wages",
            }
        )
    if total_employer > 0:
        lines.append(
            {
                "account_id": payroll_tax_expense,
                "debit": total_employer,
                "credit": Decimal("0"),
                "description": "Employer payroll taxes",
            }
        )
    if total_reimb > 0 and reimb_expense:
        lines.append(
            {
                "account_id": reimb_expense,
                "debit": total_reimb,
                "credit": Decimal("0"),
                "description": "Employee reimbursements",
            }
        )
    # Employer-paid benefits: DR each code's expense account (fringe pool)
    for acct_id, amount in groups.expenses.items():
        if amount > 0:
            lines.append(
                {
                    "account_id": acct_id,
                    "debit": amount,
                    "credit": Decimal("0"),
                    "description": "Employer benefit contributions",
                }
            )
    if total_benefit_expense_unmapped > 0 and benefits_expense:
        lines.append(
            {
                "account_id": benefits_expense,
                "debit": total_benefit_expense_unmapped,
                "credit": Decimal("0"),
                "description": "Employer benefit contributions",
            }
        )
    # Withheld + employer benefit amounts: CR each code's liability account
    for acct_id, amount in groups.liabilities.items():
        if amount > 0:
            lines.append(
                {
                    "account_id": acct_id,
                    "debit": Decimal("0"),
                    "credit": amount,
                    "description": "Benefit deductions & contributions payable",
                }
            )

    payables = [
        (total_fed, fed, "Federal income tax withheld", "2310"),
        (total_state, state_wh, "State income tax withheld", "2320"),
        (total_ss, ss_acct, "Social Security payable", "2330"),
        (total_medicare, medicare_acct, "Medicare payable", "2340"),
        (total_futa, futa_acct, "FUTA payable", "2350"),
        (total_suta, suta_acct, "SUTA payable", "2360"),
        (total_other, other_acct, "Other payroll deductions payable", "2370"),
        (total_net, bank, "Net payroll", "1000"),
    ]
    # A withholding with nowhere to post used to be left out, and the entry
    # then failed to balance as a 500 (2.23.5 round 2, W-16 / NEW-139: the
    # seeded cleanup had taken 2300 and 2310–2390). Say which account is
    # missing instead.
    missing = [
        f"{number} ({desc})"
        for amount, acct, desc, number in payables
        if amount and amount > 0 and not acct
    ]
    if missing:
        raise HTTPException(
            status_code=400,
            detail="This pay run has nowhere to post "
            + ", ".join(missing)
            + ": add that account under Chart of Accounts (or 2300 Payroll "
            "Liabilities, which stands in for any of them), then process again.",
        )
    for amount, acct, desc, _number in payables:
        if amount and amount > 0 and acct:
            lines.append(
                {
                    "account_id": acct,
                    "debit": Decimal("0"),
                    "credit": amount,
                    "description": desc,
                }
            )

    if lines:
        try:
            txn = create_journal_entry(
                db,
                run.pay_date,
                f"Payroll {run.period_start} - {run.period_end}",
                lines,
                source_type="payroll",
                source_id=run.id,
            )
        except ValueError as e:
            # a journal the ledger refuses is a problem with the data, said
            # in a sentence, never a bare 500
            raise HTTPException(
                status_code=400, detail="This pay run could not be posted: " + str(e)
            )
        run.transaction_id = txn.id

    # Job costing seam: when the labor cost type distributes actual burden,
    # spread this run's employer taxes + job-routed benefit costs across the
    # jobs the employees' time entries hit, by hours.
    from app.services.job_costing import distribute_payroll_burden

    try:
        jc = distribute_payroll_burden(db, run)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if jc is not None:
        run.burden_job_cost_id = jc.id

    run.status = PayRunStatus.PROCESSED
    db.commit()
    return {
        "status": "processed",
        "pay_run_id": run.id,
        "transaction_id": run.transaction_id,
        "burden_job_cost_id": run.burden_job_cost_id,
    }


def _discard_flag(run: PayRun, names: list[str]) -> str:
    who = ", ".join(names)
    return (
        f"Figured while draft pay run #{run.id} ({run.pay_date.isoformat()}) "
        f"counted toward the year-to-date for {who}; that draft was discarded, "
        "so this run's Social Security, Medicare, FUTA and SUTA wage bases and "
        "benefit caps counted pay that was never made. Create the run again "
        "to use the right ones."
    )


@router.delete("/{run_id}")
def discard_pay_run(run_id: int, db: Session = Depends(get_db)):
    """Discard a draft pay run: delete it and its stubs, and undo everything
    creating it changed — the benefit accumulators (BenefitYTD) and loan
    balances its stubs moved, and the time entries it took (they are free
    for the next run again). A processed run posted its journal entry and
    counts in the year's forms: it is voided, never discarded.

    A later draft that was figured while this one counted toward the
    year-to-date (its wage bases, or its benefit caps) is flagged, as an
    opening YTD change flags one (stale_reason)."""
    run = (
        db.query(PayRun)
        .options(
            joinedload(PayRun.stubs).joinedload(PayStub.benefits),
            joinedload(PayRun.stubs).joinedload(PayStub.employee),
        )
        .filter(PayRun.id == run_id)
        .first()
    )
    if not run:
        raise HTTPException(status_code=404, detail="Pay run not found")
    if run.status == PayRunStatus.PROCESSED:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Pay run #{run.id} is processed: its journal entry is posted "
                "and its stubs count in the year's tax forms, so it cannot be "
                "discarded. Only a draft is discarded; a processed run is voided."
            ),
        )
    if run.status == PayRunStatus.VOID:
        raise HTTPException(
            status_code=409,
            detail=f"Pay run #{run.id} is void; there is nothing to discard.",
        )

    year = run.pay_date.year
    # who the later drafts' figures counted this one for: its wage bases
    # (a later pay date), or its benefit caps (any draft created after it)
    by_employee: dict[int, tuple[str, bool]] = {}
    for stub in run.stubs:
        name = (
            stub.employee.full_name if stub.employee else f"Employee {stub.employee_id}"
        )
        moved_caps = any(b.employee_amount or b.employer_amount for b in stub.benefits)
        _, before = by_employee.get(stub.employee_id, (name, False))
        by_employee[stub.employee_id] = (name, before or moved_caps)
        benefits_engine.unrecord_stub(db, stub, year)

    # the time it paid is free for the next run (one at a time, so the
    # audit trail records each)
    entries = db.query(TimeEntry).filter(TimeEntry.pay_run_id == run.id).all()
    for te in entries:
        te.pay_run_id = None
    released = len(entries)
    db.flush()

    flagged = []
    if by_employee:
        later = (
            db.query(PayRun)
            .options(joinedload(PayRun.stubs))
            .filter(
                PayRun.id != run.id,
                PayRun.status == PayRunStatus.DRAFT,
                PayRun.pay_date >= date(year, 1, 1),
                PayRun.pay_date <= date(year, 12, 31),
            )
            .all()
        )
        for other in later:
            names = []
            for st in other.stubs:
                hit = by_employee.get(st.employee_id)
                if hit is None or hit[0] in names:
                    continue
                counted_bases = other.pay_date > run.pay_date
                counted_caps = hit[1] and other.id > run.id
                if counted_bases or counted_caps:
                    names.append(hit[0])
            if names:
                msg = _discard_flag(run, names)
                other.stale_reason = (
                    f"{other.stale_reason} {msg}" if other.stale_reason else msg
                )[:500]
                flagged.append(other.id)

    db.delete(run)
    db.commit()
    return {
        "status": "discarded",
        "pay_run_id": run_id,
        "released_time_entries": released,
        "flagged_runs": flagged,
    }


@router.post("/gross-up", response_model=GrossUpResponse)
def gross_up_paycheck(data: GrossUpRequest, db: Session = Depends(get_db)):
    """Net-to-gross: reverse-solve the gross pay that yields a target take-home."""
    emp = db.query(Employee).filter(Employee.id == data.employee_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    if data.target_net <= 0:
        raise HTTPException(status_code=400, detail="target_net must be positive")

    year = date.today().year
    ytd = employee_ytd(db, emp.id, year)
    ytd_suppl = (
        _ytd_supplemental(db, emp.id, year) if data.supplemental else Decimal("0")
    )
    work_state = (emp.work_state or "WA").upper()
    wh_state = withholding_state(work_state, emp.residence_state)
    bases = wage_base_ytd(db, emp.id, year, work_state)

    def net_of(g: Decimal) -> Decimal:
        return calculate_withholdings(
            g,
            pay_frequency=emp.pay_frequency.value if emp.pay_frequency else "biweekly",
            filing_status=emp.filing_status.value if emp.filing_status else "single",
            multiple_jobs=bool(emp.multiple_jobs),
            dependents_amount=emp.dependents_amount or 0,
            other_income_annual=emp.other_income_annual or 0,
            deductions_annual=emp.deductions_annual or 0,
            extra_withholding=emp.extra_withholding or 0,
            ytd_gross=ytd["gross"],
            work_state=work_state,
            withholding_state=wh_state,
            wc_class_code=emp.wc_class_code,
            state_allowances=emp.state_allowances or 0,
            state_extra_withholding=emp.state_extra_withholding or 0,
            state_rate_override=emp.state_rate_override,
            local_tax_rate=emp.local_tax_rate,
            supplemental=bool(data.supplemental),
            ytd_supplemental=ytd_suppl,
            ytd_bases=bases,
            year=year,
        )["net"]

    target = Decimal(str(data.target_net))
    gross = gross_up(target, net_of)
    net = net_of(gross)
    return GrossUpResponse(
        employee_id=emp.id,
        target_net=data.target_net,
        gross=float(gross),
        net=float(net),
        withholding=float(gross - net),
    )
