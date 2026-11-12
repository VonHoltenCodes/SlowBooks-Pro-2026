# ============================================================================
# Payroll tax forms — 941, 940, W-2 / W-3, state SUI, 1099-NEC / 1096
# ============================================================================

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import AfterValidator
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.contacts import Vendor
from app.models.payroll import Employee
from app.schemas.common import check_quarter
from app.services.tax_forms import form_941, form_940, w2_w3, state_sui, tax_liability
from app.services import form_1099
from app.services.payroll_documents import employer_block
from app.services.request_utils import content_disposition, file_name

router = APIRouter(prefix="/api/tax-forms", tags=["tax-forms"])

# A tax year the forms can be dated in: 99999 reached date() and answered a
# 500 (#755 review; a payroll token can reach these routes now).
TaxYear = Annotated[int, Query(ge=1900, le=9999)]
# A quarter, 1 to 4: refused as the year is, a 422 with a sentence naming
# the four (#810; it was a 400, "quarter must be 1-4").
TaxQuarter = Annotated[int, Query(), AfterValidator(check_quarter)]


def _employee_or_404(db: Session, employee_id: int) -> None:
    """A W-2 for an employee who isn't on file is a 404, not a $0 form."""
    if db.get(Employee, employee_id) is None:
        raise HTTPException(status_code=404, detail="Employee not found")


def _company(db: Session) -> dict:
    """The company in Settings (not the "My Company" config default)."""
    return employer_block(db)


def _pdf(content: bytes, *name) -> Response:
    """A form's PDF, named for the form and the person it is for
    ("W-2_2026_Lena-Ortiz.pdf", "941_2026_Q3.pdf"). The names used to
    carry internal ids ("w2_2026_emp1.pdf", "1099nec_2026_vendor3.pdf"),
    which told the owner nothing in the Reports folder (2.18.0 gate,
    NEW-6)."""
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": content_disposition(file_name(*name) + ".pdf")},
    )


# --- Form 941 — quarterly federal -----------------------------------------
@router.get("/941")
def get_941(year: TaxYear, quarter: TaxQuarter, db: Session = Depends(get_db)):
    return form_941.compute_941(db, year, quarter)


@router.get("/941/pdf")
def get_941_pdf(year: TaxYear, quarter: TaxQuarter, db: Session = Depends(get_db)):
    pdf = form_941.generate_941_pdf(db, year, quarter, _company(db))
    return _pdf(pdf, "941", year, f"Q{quarter}")


# --- Form 940 — annual FUTA ------------------------------------------------
@router.get("/940")
def get_940(year: TaxYear, db: Session = Depends(get_db)):
    return form_940.compute_940(db, year)


@router.get("/940/pdf")
def get_940_pdf(year: TaxYear, db: Session = Depends(get_db)):
    pdf = form_940.generate_940_pdf(db, year, _company(db))
    return _pdf(pdf, "940", year)


# --- W-2 / W-3 -------------------------------------------------------------
@router.get("/w2")
def get_all_w2(year: TaxYear, db: Session = Depends(get_db)):
    return {
        "year": year,
        "w3": w2_w3.compute_w3(db, year),
        "w2": w2_w3.compute_all_w2(db, year),
    }


@router.get("/w2/{employee_id}")
def get_w2(employee_id: int, year: TaxYear, db: Session = Depends(get_db)):
    _employee_or_404(db, employee_id)
    return w2_w3.compute_w2(db, year, employee_id)


@router.get("/w2/{employee_id}/pdf")
def get_w2_pdf(employee_id: int, year: TaxYear, db: Session = Depends(get_db)):
    _employee_or_404(db, employee_id)
    pdf = w2_w3.generate_w2_pdf(db, year, employee_id, _company(db))
    emp = db.query(Employee).filter(Employee.id == employee_id).first()
    return _pdf(pdf, "W-2", year, emp.full_name if emp else None)


# --- Paychecks with no work state (#818) -----------------------------------
@router.get("/work-state-missing")
def get_work_state_missing(year: TaxYear, db: Session = Depends(get_db)):
    """The employees paid in the year with no work state: their wages are
    on no state's SUI report and the W-2 leaves the state boxes empty. The
    Tax Forms page shows these sentences."""
    return {"year": year, "warnings": w2_w3.missing_work_state(db, year)}


# --- State unemployment ----------------------------------------------------
@router.get("/sui")
def get_sui(
    year: TaxYear,
    quarter: TaxQuarter,
    state: str = Query(default=None),
    db: Session = Depends(get_db),
):
    return state_sui.compute_sui(db, year, quarter, state)


# --- Quarterly tax liability schedule --------------------------------------
@router.get("/liability")
def get_tax_liability(
    year: TaxYear, quarter: TaxQuarter, db: Session = Depends(get_db)
):
    """What payroll tax is owed for the quarter, and when it is due. The
    year is 1900 to 9999 and the quarter 1 to 4; any other is a 422 naming
    the bounds (#810)."""
    return tax_liability.compute_tax_liability(db, year, quarter)


# --- 1099-NEC / 1096 -------------------------------------------------------
@router.get("/1099")
def get_1099(year: TaxYear, db: Session = Depends(get_db)):
    """The year's 1099-NEC vendors and the 1096. A 1099 vendor paid nothing
    in the year has no form to file and is left off the list and the Tax
    Forms picker; `unpaid` counts them, so the picker can say why it is
    empty (2.31.3 gate, #813)."""
    every = form_1099.compute_1099_data(db, year)
    paid = [v for v in every if v["total_paid"] > 0]
    return {
        "year": year,
        "threshold": float(form_1099.nec_threshold(year)),
        "transmittal": form_1099.compute_1096(db, year),
        "vendors": paid,
        "unpaid": len(every) - len(paid),
    }


@router.get("/1099/{vendor_id}/pdf")
def get_1099_pdf(vendor_id: int, year: TaxYear, db: Session = Depends(get_db)):
    try:
        pdf = form_1099.generate_1099_nec_pdf(db, year, vendor_id, _company(db))
    except ValueError as e:
        # form_1099 raises ValueError for missing or non-1099 vendors; surface
        # as 404 instead of leaking a 500 stack trace to the operator running
        # year-end forms.
        raise HTTPException(status_code=404, detail=str(e))
    vendor = db.query(Vendor).filter(Vendor.id == vendor_id).first()
    return _pdf(pdf, "1099-NEC", year, vendor.name if vendor else None)


@router.get("/1096/pdf")
def get_1096_pdf(year: TaxYear, db: Session = Depends(get_db)):
    pdf = form_1099.generate_1096_pdf(db, year, _company(db))
    return _pdf(pdf, "1096", year)
