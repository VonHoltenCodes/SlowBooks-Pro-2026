from datetime import date

from fastapi import Depends, HTTPException
from fastapi.responses import Response, PlainTextResponse
from app.schemas.common import StrictModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.routes.payroll._router import router
from app.routes.payroll.ytd import employee_ytd
from app.services.payroll_documents import employer_block
from app.models.payroll import (
    PayRun,
    PayStub,
    PayRunStatus,
    Employee,
)
from app import config


@router.get("/{run_id}/paystub/{stub_id}")
def download_paystub(run_id: int, stub_id: int, db: Session = Depends(get_db)):
    """Generate the PDF pay stub for one employee on a pay run."""
    from app.services.paystub_pdf import generate_paystub_pdf

    stub = (
        db.query(PayStub)
        .filter(PayStub.id == stub_id, PayStub.pay_run_id == run_id)
        .first()
    )
    if not stub:
        raise HTTPException(status_code=404, detail="Pay stub not found")
    run = db.query(PayRun).filter(PayRun.id == run_id).first()
    emp = db.query(Employee).filter(Employee.id == stub.employee_id).first()

    ytd = employee_ytd(db, stub.employee_id, run.pay_date.year)
    pdf = generate_paystub_pdf(
        stub, emp, run, employer_block(db), {k: str(v) for k, v in ytd.items()}
    )
    filename = f"paystub_{run_id}_{stub_id}.pdf"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f"inline; filename={filename}"},
    )


class NachaOriginating(StrictModel):
    immediate_destination: str  # receiving bank routing number
    immediate_origin: str  # company identifier (10 chars)
    destination_name: str = "BANK"
    origin_name: str = ""
    company_name: str = ""
    company_id: str = ""  # usually the employer EIN
    originating_dfi_id: str  # 8-digit routing prefix of the company's bank
    company_account: str = ""
    effective_date: date = None


@router.post("/{run_id}/nacha", response_class=PlainTextResponse)
def export_nacha(
    run_id: int, originating: NachaOriginating, db: Session = Depends(get_db)
):
    """Generate a NACHA ACH file for direct deposit of a processed pay run."""
    from app.services.nacha_export import generate_nacha_file

    run = db.query(PayRun).filter(PayRun.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Pay run not found")
    if run.status != PayRunStatus.PROCESSED:
        raise HTTPException(
            status_code=400, detail="Pay run must be processed before ACH export"
        )

    orig = originating.model_dump()
    if not orig.get("effective_date"):
        orig["effective_date"] = run.pay_date
    if not orig.get("company_name"):
        orig["company_name"] = config.COMPANY_NAME
    if not orig.get("company_id"):
        orig["company_id"] = config.EMPLOYER_EIN

    try:
        nacha = generate_nacha_file(db, run_id, orig)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return PlainTextResponse(
        content=nacha,
        headers={"Content-Disposition": f"attachment; filename=payroll_{run_id}.ach"},
    )
