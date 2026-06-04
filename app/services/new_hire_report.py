# ============================================================================
# State new-hire reporting — every US state requires employers to report a
# newly hired employee to a state directory within 20 days of the hire date
# (the data feeds child-support enforcement).
# ============================================================================

from datetime import date, timedelta

from app.models.payroll import Employee
from app.services.pdf_service import (
    _company_logo_data_uri,
    _jinja_env,
    _safe_url_fetcher,
)

# Statutory reporting window after the hire date.
NEW_HIRE_REPORT_DEADLINE_DAYS = 20


def compute_new_hire_report(db, employee_id: int, employer: dict) -> dict:
    """Assemble the data a state new-hire report requires for one employee."""
    emp = db.query(Employee).filter(Employee.id == employee_id).first()
    if not emp:
        raise ValueError(f"Employee {employee_id} not found")

    hire_date = emp.hire_date
    deadline = (
        hire_date + timedelta(days=NEW_HIRE_REPORT_DEADLINE_DAYS) if hire_date else None
    )
    overdue = bool(deadline and date.today() > deadline)

    return {
        "employee_id": emp.id,
        "employee_name": emp.full_name,
        "ssn_last_four": emp.ssn_last_four,
        "address": {
            "address1": emp.address1,
            "address2": emp.address2,
            "city": emp.city,
            "state": emp.state,
            "zip": emp.zip,
        },
        "work_state": emp.work_state or emp.state,
        "hire_date": hire_date.isoformat() if hire_date else None,
        "report_deadline": deadline.isoformat() if deadline else None,
        "overdue": overdue,
        "employer": {
            "name": employer.get("name", ""),
            "ein": employer.get("ein", ""),
            "address": employer.get("address", ""),
            "state": employer.get("state", ""),
        },
    }


def generate_new_hire_report_pdf(
    db, employee_id: int, employer: dict, company_settings: dict | None = None
) -> bytes:
    """Render the new-hire report as a filable PDF.

    `company_settings` is the same shape `pdf_service` uses elsewhere; passing
    it in lets us drop the employer's logo in the report header alongside the
    typed company name. Falls back gracefully when no logo is configured.
    """
    from weasyprint import HTML

    data = compute_new_hire_report(db, employee_id, employer)
    logo_data_uri = _company_logo_data_uri(company_settings or {})
    template = _jinja_env.get_template("new_hire_report.html")
    html_str = template.render(
        report=data, today=date.today(), company_logo_data_uri=logo_data_uri
    )
    return HTML(string=html_str, url_fetcher=_safe_url_fetcher).write_pdf()
