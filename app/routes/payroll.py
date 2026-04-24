# ============================================================================
# Payroll — pay runs, withholding calculations, pay stubs
# Feature 17: DR Wage Expense, CR Withholding accounts, CR Bank
# ============================================================================

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models.payroll import PayRun, PayStub, PayRunStatus, Employee
from app.models.accounts import Account
from app.schemas.payroll import PayRunCreate, PayRunResponse, PayStubResponse
from app.services.payroll_service import calculate_withholdings
from app.services.accounting import create_journal_entry

router = APIRouter(prefix="/api/payroll", tags=["payroll"])


@router.get("", response_model=list[PayRunResponse])
def list_pay_runs(db: Session = Depends(get_db)):
    runs = (
        db.query(PayRun)
        .options(joinedload(PayRun.stubs).joinedload(PayStub.employee))
        .order_by(PayRun.pay_date.desc())
        .all()
    )
    results = []
    for run in runs:
        resp = PayRunResponse.model_validate(run)
        for stub_resp, stub in zip(resp.stubs, run.stubs):
            if stub.employee:
                stub_resp.employee_name = f"{stub.employee.first_name} {stub.employee.last_name}"
        results.append(resp)
    return results


@router.get("/{run_id}", response_model=PayRunResponse)
def get_pay_run(run_id: int, db: Session = Depends(get_db)):
    run = (
        db.query(PayRun)
        .options(joinedload(PayRun.stubs).joinedload(PayStub.employee))
        .filter(PayRun.id == run_id)
        .first()
    )
    if not run:
        raise HTTPException(status_code=404, detail="Pay run not found")
    resp = PayRunResponse.model_validate(run)
    for stub_resp, stub in zip(resp.stubs, run.stubs):
        if stub.employee:
            stub_resp.employee_name = f"{stub.employee.first_name} {stub.employee.last_name}"
    return resp


@router.post("", response_model=PayRunResponse, status_code=201)
def create_pay_run(data: PayRunCreate, db: Session = Depends(get_db)):
    run = PayRun(
        period_start=data.period_start, period_end=data.period_end,
        pay_date=data.pay_date,
    )
    db.add(run)
    db.flush()

    total_gross = Decimal("0")
    total_taxes = Decimal("0")
    total_net = Decimal("0")

    for stub_input in data.stubs:
        emp = db.query(Employee).filter(Employee.id == stub_input.employee_id).first()
        if not emp:
            raise HTTPException(status_code=404, detail=f"Employee {stub_input.employee_id} not found")

        # Calculate gross pay
        if emp.pay_type.value == "hourly":
            gross = Decimal(str(stub_input.hours)) * emp.pay_rate
        else:
            # Salary: divide annual by 26 (biweekly)
            gross = emp.pay_rate / Decimal("26")

        gross = gross.quantize(Decimal("0.01"))

        # Calculate withholdings
        taxes = calculate_withholdings(
            gross, pay_periods=26,
            filing_status=emp.filing_status.value,
            allowances=emp.allowances,
        )

        net = gross - taxes["total"]

        stub = PayStub(
            pay_run_id=run.id, employee_id=emp.id,
            hours=stub_input.hours, gross_pay=gross,
            federal_tax=taxes["federal"], state_tax=taxes["state"],
            ss_tax=taxes["ss"], medicare_tax=taxes["medicare"],
            net_pay=net,
        )
        db.add(stub)

        total_gross += gross
        total_taxes += taxes["total"]
        total_net += net

    run.total_gross = total_gross
    run.total_taxes = total_taxes
    run.total_net = total_net

    db.commit()
    db.refresh(run)
    resp = PayRunResponse.model_validate(run)
    return resp


@router.post("/{run_id}/process")
def process_pay_run(run_id: int, db: Session = Depends(get_db)):
    """Process pay run — creates journal entries."""
    run = db.query(PayRun).filter(PayRun.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Pay run not found")
    if run.status == PayRunStatus.PROCESSED:
        raise HTTPException(status_code=400, detail="Pay run already processed")

    # Get payroll accounts
    def _get_acct(num):
        a = db.query(Account).filter(Account.account_number == num).first()
        return a.id if a else None

    wage_expense_id = _get_acct("6100") or _get_acct("6000")  # Wage expense or general expense
    fed_wh_id = _get_acct("2310")
    state_wh_id = _get_acct("2320")
    ss_id = _get_acct("2330")
    medicare_id = _get_acct("2340")
    bank_id = _get_acct("1000")

    if not wage_expense_id or not bank_id:
        raise HTTPException(status_code=400, detail="Required payroll accounts not found. Set up accounts 6100, 2310-2340, 1000.")

    journal_lines = []

    # DR Wage Expense
    if run.total_gross > 0:
        journal_lines.append({
            "account_id": wage_expense_id,
            "debit": run.total_gross, "credit": Decimal("0"),
            "description": f"Payroll {run.period_start} - {run.period_end}",
        })

    # CR Withholding accounts
    total_fed = sum(s.federal_tax for s in run.stubs)
    total_state = sum(s.state_tax for s in run.stubs)
    total_ss = sum(s.ss_tax for s in run.stubs)
    total_medicare = sum(s.medicare_tax for s in run.stubs)

    if total_fed > 0 and fed_wh_id:
        journal_lines.append({"account_id": fed_wh_id, "debit": Decimal("0"),
                              "credit": total_fed, "description": "Federal withholding"})
    if total_state > 0 and state_wh_id:
        journal_lines.append({"account_id": state_wh_id, "debit": Decimal("0"),
                              "credit": total_state, "description": "State withholding"})
    if total_ss > 0 and ss_id:
        journal_lines.append({"account_id": ss_id, "debit": Decimal("0"),
                              "credit": total_ss, "description": "Social Security payable"})
    if total_medicare > 0 and medicare_id:
        journal_lines.append({"account_id": medicare_id, "debit": Decimal("0"),
                              "credit": total_medicare, "description": "Medicare payable"})

    # CR Bank for net pay
    if run.total_net > 0 and bank_id:
        journal_lines.append({"account_id": bank_id, "debit": Decimal("0"),
                              "credit": run.total_net, "description": "Net payroll"})

    if journal_lines:
        txn = create_journal_entry(
            db, run.pay_date, f"Payroll {run.period_start} - {run.period_end}",
            journal_lines, source_type="payroll", source_id=run.id,
        )
        run.transaction_id = txn.id

    run.status = PayRunStatus.PROCESSED
    db.commit()
    return {"status": "processed", "pay_run_id": run.id}
