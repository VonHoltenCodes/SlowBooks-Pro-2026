from datetime import date

from decimal import Decimal

from fastapi import Depends, HTTPException
from fastapi.responses import Response, PlainTextResponse
from app.schemas.common import StrictModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.routes.payroll._router import router
from app.routes.payroll.ytd import employee_ytd
from app.services.payroll_documents import employer_block
from app.services.request_utils import content_disposition, file_name
from app.models.payroll import (
    PayRun,
    PayStub,
    PayRunStatus,
    Employee,
)


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

    # Year to date means up to this pay date: the stubs of processed runs
    # this year dated on or before it, and this stub (a later run, or a
    # draft, is not part of what this stub reports).
    from app.models.payroll import PayRunStatus
    from app.routes.payroll.ytd import _ytd_stubs

    ytd_stubs = [
        s
        for s in _ytd_stubs(db, stub.employee_id, run.pay_date.year)
        if s.id != stub.id
        and s.pay_run.pay_date <= run.pay_date
        and s.pay_run.status == PayRunStatus.PROCESSED
    ] + [stub]
    ytd = employee_ytd(db, stub.employee_id, run.pay_date.year)
    for key, attr in (("gross", "gross_pay"), ("net", "net_pay")):
        ytd[key] = sum((getattr(s, attr) or 0 for s in ytd_stubs), Decimal("0"))
    # and what the old system paid before the switch, as employee_ytd
    # counts it, so the stub's YTD gross and net agree with it (#433)
    from app.services import payroll_opening as po

    opening = po.opening_for(db, stub.employee_id, run.pay_date.year)
    if po.counts_before(opening, run.pay_date):
        ytd["gross"] += po.amount(opening, "gross")
        ytd["net"] += po.amount(opening, "net")
    pdf = generate_paystub_pdf(
        stub,
        emp,
        run,
        employer_block(db),
        {k: str(v) for k, v in ytd.items()},
        ytd_stubs=ytd_stubs,
    )
    # Named for the person and the pay date, not internal ids
    # ("paystub_1_1.pdf"; 2.18.0 gate, NEW-6).
    name = file_name("Pay-Stub", run.pay_date, emp.full_name if emp else None)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": content_disposition(name + ".pdf")},
    )


class NachaOriginating(StrictModel):
    """What an ACH file's header says about the originator. Every field is
    optional: what is not sent comes from Settings → Direct Deposit (ACH),
    so a client that saved them there posts an empty body."""

    immediate_destination: str = ""  # the company's bank: its routing number
    immediate_origin: str = ""  # the identifier the bank assigned (10 chars)
    destination_name: str = ""
    origin_name: str = ""
    company_name: str = ""
    company_id: str = ""  # usually "1" + the employer EIN
    originating_dfi_id: str = ""  # 8-digit routing prefix of the company's bank
    company_account: str = ""
    effective_date: date = None


def _direct_deposit_file(run_id: int, overrides: dict, db: Session):
    """The ACH file for a processed pay run, named for its pay date."""
    from app.services.direct_deposit import (
        MissingOrigination,
        direct_deposit_stubs,
        originating_from_settings,
    )
    from app.services.nacha_export import generate_nacha_file

    run = db.query(PayRun).filter(PayRun.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Pay run not found")
    if run.status != PayRunStatus.PROCESSED:
        raise HTTPException(
            status_code=400, detail="Pay run must be processed before ACH export"
        )
    try:
        orig = originating_from_settings(db, overrides)
    except MissingOrigination as e:
        raise HTTPException(status_code=409, detail=str(e))
    if not orig.get("effective_date"):
        orig["effective_date"] = run.pay_date
    paid = direct_deposit_stubs(run)
    if not paid:
        raise HTTPException(
            status_code=409,
            detail=(
                "No one on this pay run is paid by direct deposit: no employee "
                "has an active bank account (one waiting on a prenote is not "
                "paid yet). Pay them by check, or add their accounts under "
                "Employees and run again."
            ),
        )
    try:
        nacha = generate_nacha_file(db, run_id, orig)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    name = file_name("Direct-Deposit", run.pay_date) + ".ach"
    # A file the bank will pay from is a record of its own, like a restore:
    # who made it, for which run, how many it pays and how much. Never the
    # account numbers — the file carries those, the trail does not.
    from app.services.audit import log_event

    log_event(
        db,
        table_name="pay_runs",
        record_id=run.id,
        action="EXPORT",
        new_values={
            "file": name,
            "credits": len(paid),
            "total": str(sum((s.net_pay or 0) for s in paid)),
            "effective_date": orig["effective_date"].isoformat(),
        },
    )
    db.commit()
    return PlainTextResponse(
        content=nacha,
        headers={"Content-Disposition": content_disposition(name, "attachment")},
    )


@router.get("/{run_id}/nacha", response_class=PlainTextResponse)
def download_nacha(run_id: int, db: Session = Depends(get_db)):
    """The direct-deposit file for a processed pay run, from the saved
    originating details (discussion #211). Upload it to the bank."""
    return _direct_deposit_file(run_id, {}, db)


@router.post("/{run_id}/nacha", response_class=PlainTextResponse)
def export_nacha(
    run_id: int,
    originating: NachaOriginating | None = None,
    db: Session = Depends(get_db),
):
    """Generate a NACHA ACH file for direct deposit of a processed pay run.

    A field in the body outranks the saved setting; a missing one comes
    from Settings."""
    overrides = originating.model_dump() if originating else {}
    return _direct_deposit_file(run_id, overrides, db)
