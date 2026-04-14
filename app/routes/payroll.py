# ============================================================================
# Payroll — NZ PAYE draft runs and processing
# Uses versioned NZ payroll rules and posts payroll liabilities to accounting.
# ============================================================================

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.payroll import Employee, PayRun, PayRunStatus, PayStub
from app.schemas.email import DocumentEmailRequest
from app.schemas.payroll import PayRunCreate, PayRunResponse, PayStubResponse
from app.services.pdf_service import generate_payroll_payslip_pdf
from app.services.payday_filing import generate_employment_information_csv
from app.services.email_service import render_document_email, send_document_email
from app.services.accounting import (
    create_journal_entry,
    get_child_support_payable_account_id,
    get_employer_kiwisaver_expense_account_id,
    get_esct_payable_account_id,
    get_kiwisaver_payable_account_id,
    get_payroll_clearing_account_id,
    get_paye_payable_account_id,
    get_wages_expense_account_id,
)
from app.services.closing_date import check_closing_date
from app.services.nz_payroll import calculate_payroll_stub, round_money
from app.routes.settings import _get_all as get_settings

router = APIRouter(prefix="/api/payroll", tags=["payroll"])


def _stub_response(stub: PayStub) -> PayStubResponse:
    return PayStubResponse(
        id=stub.id,
        employee_id=stub.employee_id,
        employee_name=f"{stub.employee.first_name} {stub.employee.last_name}".strip() if stub.employee else None,
        tax_code=stub.tax_code,
        hours=float(stub.hours or 0),
        gross_pay=float(stub.gross_pay or 0),
        paye=float(stub.paye or 0),
        acc_earners_levy=float(stub.acc_earners_levy or 0),
        student_loan_deduction=float(stub.student_loan_deduction or 0),
        kiwisaver_employee_deduction=float(stub.kiwisaver_employee_deduction or 0),
        employer_kiwisaver_contribution=float(stub.employer_kiwisaver_contribution or 0),
        esct=float(stub.esct or 0),
        child_support_deduction=float(stub.child_support_deduction or 0),
        total_deductions=float(
            round_money(
                Decimal(str(stub.paye or 0))
                + Decimal(str(stub.acc_earners_levy or 0))
                + Decimal(str(stub.student_loan_deduction or 0))
                + Decimal(str(stub.kiwisaver_employee_deduction or 0))
                + Decimal(str(stub.child_support_deduction or 0))
            )
        ),
        employer_kiwisaver_net=float(
            round_money(Decimal(str(stub.employer_kiwisaver_contribution or 0)) - Decimal(str(stub.esct or 0)))
        ),
        net_pay=float(stub.net_pay or 0),
    )


def _pay_run_response(pay_run: PayRun) -> PayRunResponse:
    return PayRunResponse(
        id=pay_run.id,
        period_start=pay_run.period_start,
        period_end=pay_run.period_end,
        pay_date=pay_run.pay_date,
        tax_year=pay_run.tax_year,
        status=pay_run.status.value if hasattr(pay_run.status, "value") else str(pay_run.status),
        total_gross=float(pay_run.total_gross or 0),
        total_net=float(pay_run.total_net or 0),
        total_taxes=float(pay_run.total_taxes or 0),
        total_paye=float(pay_run.total_paye or 0),
        total_acc_earners_levy=float(pay_run.total_acc_earners_levy or 0),
        total_student_loan=float(pay_run.total_student_loan or 0),
        total_kiwisaver_employee=float(pay_run.total_kiwisaver_employee or 0),
        total_employer_kiwisaver=float(pay_run.total_employer_kiwisaver or 0),
        total_esct=float(pay_run.total_esct or 0),
        total_child_support=float(pay_run.total_child_support or 0),
        transaction_id=pay_run.transaction_id,
        stubs=[_stub_response(stub) for stub in pay_run.stubs],
    )


def _active_employee_for_payday(employee: Employee, pay_date) -> bool:
    if not employee.is_active:
        return False
    if employee.start_date and employee.start_date > pay_date:
        return False
    if employee.end_date and employee.end_date < pay_date:
        return False
    return True


@router.get("", response_model=list[PayRunResponse])
def list_pay_runs(db: Session = Depends(get_db)):
    pay_runs = db.query(PayRun).order_by(PayRun.pay_date.desc(), PayRun.id.desc()).all()
    return [_pay_run_response(pay_run) for pay_run in pay_runs]


@router.get("/{run_id}", response_model=PayRunResponse)
def get_pay_run(run_id: int, db: Session = Depends(get_db)):
    pay_run = db.query(PayRun).filter(PayRun.id == run_id).first()
    if not pay_run:
        raise HTTPException(status_code=404, detail="Pay run not found")
    return _pay_run_response(pay_run)


@router.post("", response_model=PayRunResponse, status_code=201)
def create_pay_run(data: PayRunCreate, db: Session = Depends(get_db)):
    if not data.stubs:
        raise HTTPException(status_code=400, detail="At least one employee stub is required")

    employee_map = {
        employee.id: employee
        for employee in db.query(Employee).filter(Employee.id.in_([stub.employee_id for stub in data.stubs])).all()
    }
    if len(employee_map) != len({stub.employee_id for stub in data.stubs}):
        raise HTTPException(status_code=404, detail="One or more employees were not found")

    calculations = []
    for stub_input in data.stubs:
        employee = employee_map[stub_input.employee_id]
        if not _active_employee_for_payday(employee, data.pay_date):
            raise HTTPException(status_code=400, detail=f"Employee {employee.first_name} {employee.last_name} is not active for this pay date")
        try:
            calculations.append((employee, calculate_payroll_stub(employee, data.pay_date, Decimal(str(stub_input.hours or 0)))))
        except ValueError as err:
            raise HTTPException(status_code=400, detail=str(err)) from err

    pay_run = PayRun(
        period_start=data.period_start,
        period_end=data.period_end,
        pay_date=data.pay_date,
        tax_year=calculations[0][1].tax_year,
        status=PayRunStatus.DRAFT,
        total_gross=sum((calc.gross_pay for _employee, calc in calculations), Decimal("0.00")),
        total_net=sum((calc.net_pay for _employee, calc in calculations), Decimal("0.00")),
        total_taxes=sum((calc.total_deductions for _employee, calc in calculations), Decimal("0.00")),
        total_paye=sum((calc.paye for _employee, calc in calculations), Decimal("0.00")),
        total_acc_earners_levy=sum((calc.acc_earners_levy for _employee, calc in calculations), Decimal("0.00")),
        total_student_loan=sum((calc.student_loan_deduction for _employee, calc in calculations), Decimal("0.00")),
        total_kiwisaver_employee=sum((calc.kiwisaver_employee_deduction for _employee, calc in calculations), Decimal("0.00")),
        total_employer_kiwisaver=sum((calc.employer_kiwisaver_contribution for _employee, calc in calculations), Decimal("0.00")),
        total_esct=sum((calc.esct for _employee, calc in calculations), Decimal("0.00")),
        total_child_support=sum((calc.child_support_deduction for _employee, calc in calculations), Decimal("0.00")),
    )
    db.add(pay_run)
    db.flush()

    for employee, calculation in calculations:
        db.add(PayStub(
            pay_run_id=pay_run.id,
            employee_id=employee.id,
            tax_code=calculation.tax_code,
            hours=calculation.hours,
            gross_pay=calculation.gross_pay,
            paye=calculation.paye,
            acc_earners_levy=calculation.acc_earners_levy,
            student_loan_deduction=calculation.student_loan_deduction,
            kiwisaver_employee_deduction=calculation.kiwisaver_employee_deduction,
            employer_kiwisaver_contribution=calculation.employer_kiwisaver_contribution,
            esct=calculation.esct,
            child_support_deduction=calculation.child_support_deduction,
            net_pay=calculation.net_pay,
        ))

    db.commit()
    db.refresh(pay_run)
    return _pay_run_response(pay_run)


@router.post("/{run_id}/process", response_model=PayRunResponse)
def process_pay_run(run_id: int, db: Session = Depends(get_db)):
    pay_run = db.query(PayRun).filter(PayRun.id == run_id).first()
    if not pay_run:
        raise HTTPException(status_code=404, detail="Pay run not found")
    if pay_run.status == PayRunStatus.PROCESSED:
        raise HTTPException(status_code=400, detail="Pay run already processed")

    check_closing_date(db, pay_run.pay_date)

    wages_expense_id = get_wages_expense_account_id(db)
    employer_kiwisaver_expense_id = get_employer_kiwisaver_expense_account_id(db)
    paye_payable_id = get_paye_payable_account_id(db)
    kiwisaver_payable_id = get_kiwisaver_payable_account_id(db)
    esct_payable_id = get_esct_payable_account_id(db)
    child_support_payable_id = get_child_support_payable_account_id(db)
    payroll_clearing_id = get_payroll_clearing_account_id(db)

    journal_lines = [
        {
            "account_id": wages_expense_id,
            "debit": Decimal(str(pay_run.total_gross)),
            "credit": Decimal("0.00"),
            "description": f"Payroll gross wages run #{pay_run.id}",
        },
    ]

    if Decimal(str(pay_run.total_employer_kiwisaver or 0)) > 0:
        journal_lines.append({
            "account_id": employer_kiwisaver_expense_id,
            "debit": Decimal(str(pay_run.total_employer_kiwisaver)),
            "credit": Decimal("0.00"),
            "description": f"Employer KiwiSaver run #{pay_run.id}",
        })

    paye_related_total = (
        Decimal(str(pay_run.total_paye or 0))
        + Decimal(str(pay_run.total_acc_earners_levy or 0))
        + Decimal(str(pay_run.total_student_loan or 0))
    )
    if paye_related_total > 0:
        journal_lines.append({
            "account_id": paye_payable_id,
            "debit": Decimal("0.00"),
            "credit": paye_related_total,
            "description": f"PAYE deductions run #{pay_run.id}",
        })

    kiwisaver_payable_total = Decimal(str(pay_run.total_kiwisaver_employee or 0)) + (
        Decimal(str(pay_run.total_employer_kiwisaver or 0)) - Decimal(str(pay_run.total_esct or 0))
    )
    if kiwisaver_payable_total > 0:
        journal_lines.append({
            "account_id": kiwisaver_payable_id,
            "debit": Decimal("0.00"),
            "credit": kiwisaver_payable_total,
            "description": f"KiwiSaver deductions run #{pay_run.id}",
        })

    if Decimal(str(pay_run.total_esct or 0)) > 0:
        journal_lines.append({
            "account_id": esct_payable_id,
            "debit": Decimal("0.00"),
            "credit": Decimal(str(pay_run.total_esct)),
            "description": f"ESCT deductions run #{pay_run.id}",
        })

    if Decimal(str(pay_run.total_child_support or 0)) > 0:
        journal_lines.append({
            "account_id": child_support_payable_id,
            "debit": Decimal("0.00"),
            "credit": Decimal(str(pay_run.total_child_support)),
            "description": f"Child support deductions run #{pay_run.id}",
        })

    journal_lines.append({
        "account_id": payroll_clearing_id,
        "debit": Decimal("0.00"),
        "credit": Decimal(str(pay_run.total_net)),
        "description": f"Net wages payable run #{pay_run.id}",
    })

    txn = create_journal_entry(
        db,
        pay_run.pay_date,
        f"Payroll run #{pay_run.id}",
        journal_lines,
        source_type="payroll",
        source_id=pay_run.id,
        reference=f"PAYRUN-{pay_run.id}",
    )
    pay_run.transaction_id = txn.id
    pay_run.status = PayRunStatus.PROCESSED

    db.commit()
    db.refresh(pay_run)
    return _pay_run_response(pay_run)


@router.get("/{run_id}/payslips/{employee_id}/pdf")
def payroll_payslip_pdf(run_id: int, employee_id: int, db: Session = Depends(get_db)):
    pay_run = db.query(PayRun).filter(PayRun.id == run_id).first()
    if not pay_run:
        raise HTTPException(status_code=404, detail="Pay run not found")
    if pay_run.status != PayRunStatus.PROCESSED:
        raise HTTPException(status_code=400, detail="Payslips are only available for processed pay runs")

    stub = (
        db.query(PayStub)
        .filter(PayStub.pay_run_id == run_id, PayStub.employee_id == employee_id)
        .first()
    )
    if not stub or not stub.employee:
        raise HTTPException(status_code=404, detail="Employee payslip not found for this pay run")

    pdf_bytes = generate_payroll_payslip_pdf(pay_run, stub, stub.employee, get_settings(db))
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"inline; filename=PaySlip_{run_id}_{employee_id}.pdf"},
    )


@router.post("/{run_id}/payslips/{employee_id}/email")
def email_payroll_payslip(run_id: int, employee_id: int, data: DocumentEmailRequest, db: Session = Depends(get_db)):
    pay_run = db.query(PayRun).filter(PayRun.id == run_id).first()
    if not pay_run:
        raise HTTPException(status_code=404, detail="Pay run not found")
    if pay_run.status != PayRunStatus.PROCESSED:
        raise HTTPException(status_code=400, detail="Payslips are only available for processed pay runs")

    stub = (
        db.query(PayStub)
        .filter(PayStub.pay_run_id == run_id, PayStub.employee_id == employee_id)
        .first()
    )
    if not stub or not stub.employee:
        raise HTTPException(status_code=404, detail="Employee payslip not found for this pay run")

    company = get_settings(db)
    try:
        pdf_bytes = generate_payroll_payslip_pdf(pay_run, stub, stub.employee, company)
        html_body = render_document_email(
            document_label="Payslip",
            recipient_name=f"{stub.employee.first_name} {stub.employee.last_name}".strip(),
            document_number=f"Pay Run {pay_run.id}",
            company_settings=company,
            amount=stub.net_pay,
            action_label="Pay date",
            action_value=pay_run.pay_date,
        )
        send_document_email(
            db,
            to_email=data.recipient,
            subject=data.subject or f"Payslip for {pay_run.pay_date.isoformat()}",
            html_body=html_body,
            attachment_bytes=pdf_bytes,
            attachment_name=f"PaySlip_{run_id}_{employee_id}.pdf",
            entity_type="payroll_payslip",
            entity_id=stub.id,
        )
        return {"status": "sent"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Email failed: {str(e)}")


@router.get("/{run_id}/employment-information/export")
def export_employment_information(run_id: int, db: Session = Depends(get_db)):
    pay_run = db.query(PayRun).filter(PayRun.id == run_id).first()
    if not pay_run:
        raise HTTPException(status_code=404, detail="Pay run not found")

    try:
        csv_content = generate_employment_information_csv(pay_run, get_settings(db))
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err

    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="EmploymentInformation_{pay_run.pay_date.isoformat()}_run-{pay_run.id}.csv"'},
    )
