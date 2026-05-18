# ============================================================================
# Payroll tax forms — 941, 940, W-2 / W-3, state SUI, 1099-NEC / 1096
# ============================================================================

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.tax_forms import form_941, form_940, w2_w3, state_sui, tax_liability
from app.services import form_1099
from app import config

router = APIRouter(prefix="/api/tax-forms", tags=["tax-forms"])


def _company() -> dict:
    return {
        "name": config.COMPANY_NAME,
        "address": config.COMPANY_ADDRESS,
        "phone": config.COMPANY_PHONE,
        "email": config.COMPANY_EMAIL,
        "ein": config.EMPLOYER_EIN,
        "state": config.EMPLOYER_STATE,
    }


def _pdf(content: bytes, filename: str) -> Response:
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f"inline; filename={filename}"},
    )


def _check_quarter(quarter: int):
    if quarter not in (1, 2, 3, 4):
        raise HTTPException(status_code=400, detail="quarter must be 1-4")


# --- Form 941 — quarterly federal -----------------------------------------
@router.get("/941")
def get_941(
    year: int = Query(...), quarter: int = Query(...), db: Session = Depends(get_db)
):
    _check_quarter(quarter)
    return form_941.compute_941(db, year, quarter)


@router.get("/941/pdf")
def get_941_pdf(
    year: int = Query(...), quarter: int = Query(...), db: Session = Depends(get_db)
):
    _check_quarter(quarter)
    pdf = form_941.generate_941_pdf(db, year, quarter, _company())
    return _pdf(pdf, f"form941_{year}Q{quarter}.pdf")


# --- Form 940 — annual FUTA ------------------------------------------------
@router.get("/940")
def get_940(year: int = Query(...), db: Session = Depends(get_db)):
    return form_940.compute_940(db, year)


@router.get("/940/pdf")
def get_940_pdf(year: int = Query(...), db: Session = Depends(get_db)):
    pdf = form_940.generate_940_pdf(db, year, _company())
    return _pdf(pdf, f"form940_{year}.pdf")


# --- W-2 / W-3 -------------------------------------------------------------
@router.get("/w2")
def get_all_w2(year: int = Query(...), db: Session = Depends(get_db)):
    return {
        "year": year,
        "w3": w2_w3.compute_w3(db, year),
        "w2": w2_w3.compute_all_w2(db, year),
    }


@router.get("/w2/{employee_id}")
def get_w2(employee_id: int, year: int = Query(...), db: Session = Depends(get_db)):
    return w2_w3.compute_w2(db, year, employee_id)


@router.get("/w2/{employee_id}/pdf")
def get_w2_pdf(employee_id: int, year: int = Query(...), db: Session = Depends(get_db)):
    pdf = w2_w3.generate_w2_pdf(db, year, employee_id, _company())
    return _pdf(pdf, f"w2_{year}_emp{employee_id}.pdf")


# --- State unemployment ----------------------------------------------------
@router.get("/sui")
def get_sui(
    year: int = Query(...),
    quarter: int = Query(...),
    state: str = Query(default=None),
    db: Session = Depends(get_db),
):
    _check_quarter(quarter)
    return state_sui.compute_sui(db, year, quarter, state)


# --- Quarterly tax liability schedule --------------------------------------
@router.get("/liability")
def get_tax_liability(
    year: int = Query(...), quarter: int = Query(...), db: Session = Depends(get_db)
):
    """What payroll tax is owed for the quarter, and when it is due."""
    _check_quarter(quarter)
    return tax_liability.compute_tax_liability(db, year, quarter)


# --- 1099-NEC / 1096 -------------------------------------------------------
@router.get("/1099")
def get_1099(year: int = Query(...), db: Session = Depends(get_db)):
    return {
        "year": year,
        "transmittal": form_1099.compute_1096(db, year),
        "vendors": form_1099.compute_1099_data(db, year),
    }


@router.get("/1099/{vendor_id}/pdf")
def get_1099_pdf(vendor_id: int, year: int = Query(...), db: Session = Depends(get_db)):
    pdf = form_1099.generate_1099_nec_pdf(db, year, vendor_id, _company())
    return _pdf(pdf, f"1099nec_{year}_vendor{vendor_id}.pdf")


@router.get("/1096/pdf")
def get_1096_pdf(year: int = Query(...), db: Session = Depends(get_db)):
    pdf = form_1099.generate_1096_pdf(db, year, _company())
    return _pdf(pdf, f"form1096_{year}.pdf")
