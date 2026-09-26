from fastapi import Depends, HTTPException, Query
from fastapi.responses import Response, JSONResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.routes.payroll._router import router
from app.models.payroll import Employee
from app.services.document_audit import (
    audit_footer_context,
    compute_doc_hash,
    record_doc_audit,
)
from app.services.settings_service import get_all_settings
from app.services.tax_forms.form_940 import compute_940, generate_940_pdf
from app.services.tax_forms.form_941 import compute_941, generate_941_pdf
from app.services.tax_forms.w2_w3 import (
    compute_w2,
    compute_w3,
    generate_w2_pdf,
    generate_w3_pdf,
)
from app import config

# --- Tier 3: Tax Form Generation -----------------------------------------------


@router.post("/forms/w2/{emp_id}", response_class=Response)
def generate_w2_form(
    emp_id: int,
    year: int = Query(...),
    db: Session = Depends(get_db),
):
    """Generate W-2 form PDF for an employee for the given year.

    Returns a PDF file with the employee's W-2 data: gross, federal, state,
    SS, Medicare, and other withholdings for the calendar year.
    """
    emp = db.query(Employee).filter(Employee.id == emp_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")

    # Thin wrapper over the authoritative compute_w2 service (same one the
    # /pdf endpoint uses). The service applies the Social Security wage-base
    # cap to box 3 and reports box 4/box 6 as the actual taxes withheld; the
    # JSON shape below maps its keys onto the legacy box_N field names.
    data = compute_w2(db, year, emp_id)
    w2_data = {
        "box_1": str(data["box1_federal_wages"]),
        "box_2": str(data["box2_federal_tax_withheld"]),
        "box_3": str(data["box3_ss_wages"]),
        "box_4": str(data["box4_ss_tax_withheld"]),
        "box_5": str(data["box5_medicare_wages"]),
        "box_6": str(data["box6_medicare_tax_withheld"]),
        "box_16": str(data["box16_state_wages"]),
        "box_17": str(data["box17_state_income_tax"]),
        "employee_ssn": (
            f"XXX-XX-{emp.ssn_last_four}" if emp.ssn_last_four else "XXX-XX-XXXX"
        ),
        "employee_name": f"{emp.first_name} {emp.last_name}",
        "employer_ein": config.EMPLOYER_EIN or "XX-XXXXXXX",
        "employer_name": config.COMPANY_NAME,
        "tax_year": str(year),
    }

    return JSONResponse(content=w2_data, status_code=200)


@router.post("/forms/w3/{year}", response_class=Response)
def generate_w3_form(
    year: int,
    db: Session = Depends(get_db),
):
    """Generate W-3 form (summary of all W-2s) for the given year.

    Returns a PDF file with aggregate W-2 data for all employees.
    """
    # Thin wrapper over compute_w3 — the same service the /pdf endpoint uses.
    # It aggregates every W-2 for the year (each built with the SS wage-base
    # cap applied) in a single pass.
    data = compute_w3(db, year)
    w3_data = {
        "box_1": str(data["box1_federal_wages"]),
        "box_2": str(data["box2_federal_tax_withheld"]),
        "box_3": str(data["box3_ss_wages"]),
        "box_4": str(data["box4_ss_tax_withheld"]),
        "box_5": str(data["box5_medicare_wages"]),
        "box_6": str(data["box6_medicare_tax_withheld"]),
        "box_16": str(data["box16_state_wages"]),
        "box_17": str(data["box17_state_income_tax"]),
        "number_of_w2s": str(data["num_w2"]),
        "employer_ein": config.EMPLOYER_EIN or "XX-XXXXXXX",
        "employer_name": config.COMPANY_NAME,
        "employer_address": config.COMPANY_ADDRESS or "",
        "tax_year": str(year),
    }

    return JSONResponse(content=w3_data, status_code=200)


@router.post("/forms/940/{year}", response_class=Response)
def generate_form_940(
    year: int,
    db: Session = Depends(get_db),
):
    """Generate Form 940 (FUTA) for the given year.

    Returns a PDF file with federal unemployment tax information.
    """
    # Thin wrapper over compute_940 — the same service the /pdf endpoint uses.
    # It applies the $7,000 per-employee FUTA wage base across the year's pay
    # stubs in a single pass and reports the actual withheld FUTA tax.
    data = compute_940(db, year)
    form_940_data = {
        "box_1": str(data["futa_taxable_wages"]),  # Wages subject to FUTA
        "box_2": str(data["total_futa_tax"]),  # FUTA tax for the year
        "total_payments": str(data["total_payments"]),
        "exempt_payments": str(data["exempt_payments"]),
        "employer_ein": config.EMPLOYER_EIN or "XX-XXXXXXX",
        "employer_name": config.COMPANY_NAME,
        "tax_year": str(year),
        "payment_status": "Not yet filed",
    }

    return JSONResponse(content=form_940_data, status_code=200)


@router.post("/forms/941/{year}/{quarter}", response_class=Response)
def generate_form_941(
    year: int,
    quarter: int,
    db: Session = Depends(get_db),
):
    """Generate Form 941 (quarterly FICA) for the given year and quarter.

    Returns a PDF file with quarterly federal payroll tax information.
    """
    if quarter not in (1, 2, 3, 4):
        raise HTTPException(status_code=400, detail="quarter must be 1-4")

    # Thin wrapper over compute_941 — the same service the /pdf endpoint uses.
    # It aggregates the quarter's PROCESSED pay stubs (single pass) into wage,
    # withholding and combined-FICA-tax totals.
    data = compute_941(db, year, quarter)
    form_941_data = {
        "quarter": str(quarter),
        "year": str(year),
        "box_1": str(data["total_wages"]),  # Total wages, tips, other comp
        "box_2": str(data["federal_income_tax_withheld"]),  # Federal income tax
        "box_3": str(data["social_security_wages"]),  # Social security wages
        "box_4": str(data["social_security_tax"]),  # Social security tax
        "box_5": str(data["medicare_wages"]),  # Medicare wages and tips
        "box_6": str(data["medicare_tax"]),  # Medicare tax
        "box_12": str(data["total_tax_liability"]),  # Total tax after adjustments
        "number_of_employees": str(data["num_employees"]),
        "employer_ein": config.EMPLOYER_EIN or "XX-XXXXXXX",
        "employer_name": config.COMPANY_NAME,
        "payment_status": "Not yet filed",
    }

    return JSONResponse(content=form_941_data, status_code=200)


# --- Tier 3: Tax Form PDFs --------------------------------------------------
#
# The /forms/* endpoints above return JSON — useful for future e-file
# integration and machine-readable consumers. These /pdf variants render
# the same data through WeasyPrint templates so the admin UI's "Generate"
# buttons produce printable forms instead of raw JSON.
#
# Each answers GET as well as POST. The Tax Forms page opens them the way
# invoices open (window.open on the URL), which the desktop shim turns into
# a fetch + native PDF viewer; the page used to POST, wrap the reply in a
# blob: URL and window.open that, which WKWebView silently ignores — the
# Mac app produced nothing for W-2, W-3, 940 or 941 (2.17.3, macbase1 F23).
# POST stays for callers that already use it.
#
# The PDFs are not pixel-exact replicas of the IRS-published forms — they
# show all the right data in a readable layout with a clear disclaimer.
# Match against the official form before filing.


def _company_for_pdf(db: Session) -> dict:
    """Shape the settings dict the tax-form templates expect."""
    settings = get_all_settings(db)
    return {
        "name": settings.get("company_name") or config.COMPANY_NAME,
        "address": settings.get("company_address1") or "",
        "city": settings.get("company_city") or "",
        "state": settings.get("company_state") or config.EMPLOYER_STATE,
        "zip": settings.get("company_zip") or "",
        "ein": settings.get("company_tax_id") or config.EMPLOYER_EIN,
    }


def _pdf_response(pdf_bytes: bytes, filename: str) -> Response:
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"inline; filename={filename}"},
    )


def _hash_and_audit(
    db: Session, doc_type: str, doc_key: str, company: dict, data: dict
) -> dict:
    """Hash the (company, data) pair, write the audit row, return the
    footer-context dict the templates expect."""
    content_hash = compute_doc_hash({"company": company, "data": data})
    audit = record_doc_audit(db, doc_type, doc_key, content_hash)
    return audit_footer_context(audit)


@router.get("/forms/w2/{emp_id}/pdf", response_class=Response)
@router.post("/forms/w2/{emp_id}/pdf", response_class=Response)
def generate_w2_form_pdf(
    emp_id: int,
    year: int = Query(...),
    db: Session = Depends(get_db),
):
    """W-2 PDF for one employee for the given calendar year."""
    if not db.query(Employee).filter(Employee.id == emp_id).first():
        raise HTTPException(status_code=404, detail="Employee not found")
    company = _company_for_pdf(db)
    audit = _hash_and_audit(
        db, "w2", f"emp{emp_id}-yr{year}", company, compute_w2(db, year, emp_id)
    )
    pdf = generate_w2_pdf(db, year, emp_id, company, audit=audit)
    return _pdf_response(pdf, f"w2_{emp_id}_{year}.pdf")


@router.get("/forms/w3/{year}/pdf", response_class=Response)
@router.post("/forms/w3/{year}/pdf", response_class=Response)
def generate_w3_form_pdf(year: int, db: Session = Depends(get_db)):
    """W-3 transmittal PDF — aggregate across every W-2 for the year."""
    company = _company_for_pdf(db)
    audit = _hash_and_audit(db, "w3", f"yr{year}", company, compute_w3(db, year))
    pdf = generate_w3_pdf(db, year, company, audit=audit)
    return _pdf_response(pdf, f"w3_{year}.pdf")


@router.get("/forms/940/{year}/pdf", response_class=Response)
@router.post("/forms/940/{year}/pdf", response_class=Response)
def generate_form_940_pdf(year: int, db: Session = Depends(get_db)):
    """Form 940 (FUTA) PDF for the given calendar year."""
    company = _company_for_pdf(db)
    audit = _hash_and_audit(db, "940", f"yr{year}", company, compute_940(db, year))
    pdf = generate_940_pdf(db, year, company, audit=audit)
    return _pdf_response(pdf, f"form_940_{year}.pdf")


@router.get("/forms/941/{year}/{quarter}/pdf", response_class=Response)
@router.post("/forms/941/{year}/{quarter}/pdf", response_class=Response)
def generate_form_941_pdf(year: int, quarter: int, db: Session = Depends(get_db)):
    """Form 941 (quarterly FICA) PDF for year + quarter."""
    if quarter not in (1, 2, 3, 4):
        raise HTTPException(status_code=400, detail="quarter must be 1-4")
    company = _company_for_pdf(db)
    audit = _hash_and_audit(
        db,
        "941",
        f"yr{year}-q{quarter}",
        company,
        compute_941(db, year, quarter),
    )
    pdf = generate_941_pdf(db, year, quarter, company, audit=audit)
    return _pdf_response(pdf, f"form_941_{year}_q{quarter}.pdf")
