# ============================================================================
# Form history — what a generated tax form was, and whether it still matches
# the books (issue #263).
#
# A document_audits row holds the hash a W-2, W-3, 940 or 941 carried in its
# footer. Check re-figures that form from today's books, hashes it the same
# way the form did (form_content_hash) and compares: the same hash means the
# pay runs behind the form are as they were; a different one means they
# changed after it was generated. A voided pay run in the form's period is
# said as well.
# ============================================================================

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.models.document_audit import DocumentAudit
from app.models.payroll import Employee, PayRun, PayRunStatus, PayStub
from app.services.document_audit import form_content_hash
from app.services.payroll_documents import employer_block
from app.services.tax_forms.form_940 import compute_940
from app.services.tax_forms.form_941 import compute_941
from app.services.tax_forms.w2_w3 import compute_w2, compute_w3

FORM_NAMES = {"w2": "W-2", "w3": "W-3", "940": "Form 940", "941": "Form 941"}

# the keys the forms write: emp42-yr2026 (W-2), yr2026 (W-3, 940), yr2026-q3 (941)
_W2_KEY = re.compile(r"^emp(\d+)-yr(\d{4})$")
_YEAR_KEY = re.compile(r"^yr(\d{4})$")
_QUARTER_KEY = re.compile(r"^yr(\d{4})-q([1-4])$")


def _parse(audit: DocumentAudit) -> Optional[dict]:
    """The form's coordinates from its key, or None for a key the forms
    don't write."""
    key = audit.doc_key or ""
    if audit.doc_type == "w2":
        m = _W2_KEY.match(key)
        return {"employee_id": int(m.group(1)), "year": int(m.group(2))} if m else None
    if audit.doc_type in ("w3", "940"):
        m = _YEAR_KEY.match(key)
        return {"year": int(m.group(1))} if m else None
    if audit.doc_type == "941":
        m = _QUARTER_KEY.match(key)
        return {"year": int(m.group(1)), "quarter": int(m.group(2))} if m else None
    return None


def _period(parsed: dict) -> tuple[date, date]:
    """The pay dates the form covers: a quarter for the 941, else the year."""
    year = parsed["year"]
    if "quarter" in parsed:
        q = parsed["quarter"]
        start = date(year, 3 * (q - 1) + 1, 1)
        after = date(year + 1, 1, 1) if q == 4 else date(year, 3 * q + 1, 1)
        return start, after - timedelta(days=1)
    return date(year, 1, 1), date(year, 12, 31)


def describe(db: Session, audit: DocumentAudit) -> tuple[str, str]:
    """("W-2", "Jonah Pike, 2026"), ("Form 941", "2026 Q3"), ("W-3", "2026")
    — the form and whom or when it is for. A W-2 whose employee is gone
    says so."""
    form = FORM_NAMES.get(audit.doc_type, audit.doc_type)
    parsed = _parse(audit)
    if not parsed:
        return form, audit.doc_key or ""
    if "employee_id" in parsed:
        emp = db.query(Employee).filter(Employee.id == parsed["employee_id"]).first()
        who = (
            emp.full_name
            if emp
            else f"employee #{parsed['employee_id']} (no longer on file)"
        )
        return form, f"{who}, {parsed['year']}"
    if "quarter" in parsed:
        return form, f"{parsed['year']} Q{parsed['quarter']}"
    return form, str(parsed["year"])


def _voided_runs(db: Session, parsed: dict) -> int:
    """Pay runs voided in the form's period — for a W-2, the employee's."""
    start, end = _period(parsed)
    q = db.query(PayRun.id).filter(
        PayRun.status == PayRunStatus.VOID,
        PayRun.pay_date >= start,
        PayRun.pay_date <= end,
    )
    if "employee_id" in parsed:
        q = q.join(PayStub, PayStub.pay_run_id == PayRun.id).filter(
            PayStub.employee_id == parsed["employee_id"]
        )
    return q.distinct().count()


def recheck(db: Session, audit: DocumentAudit) -> dict:
    """Re-figure the form from today's books and compare its hash with the
    one it was generated with. `matches` is None when it cannot be
    re-figured (the employee is gone, or the key isn't a form's)."""
    form, subject = describe(db, audit)
    parsed = _parse(audit)
    out = {
        "id": audit.id,
        "form": form,
        "subject": subject,
        "generated_hash": audit.content_hash,
        "current_hash": None,
        "matches": None,
        "voided_runs": 0,
        "message": "",
    }
    if not parsed:
        out["message"] = f"Cannot be re-figured: {form} is not a form this page knows."
        return out
    if "employee_id" in parsed:
        emp = db.query(Employee).filter(Employee.id == parsed["employee_id"]).first()
        if not emp:
            out["message"] = (
                f"Cannot be re-figured: the employee the {form} for "
                f"{parsed['year']} was for is no longer on file."
            )
            return out

    def figure(legacy: bool) -> dict:
        if "employee_id" in parsed:
            return compute_w2(db, parsed["year"], parsed["employee_id"], legacy=legacy)
        if audit.doc_type == "w3":
            return compute_w3(db, parsed["year"], legacy=legacy)
        if audit.doc_type == "940":
            return compute_940(db, parsed["year"], legacy=legacy)
        return compute_941(db, parsed["year"], parsed["quarter"], legacy=legacy)

    company = employer_block(db)
    current = form_content_hash(company, figure(False))
    matches = current == audit.content_hash
    # A form generated before 2.29.0 was figured the earlier way (each
    # paycheck's gross toward the wage bases, one state line, the 2026
    # Social Security base for every year). Figured that way from today's
    # books it still matches when nothing behind it changed; the sentence
    # then says today's figures differ, which may call for a corrected form.
    figured_then = False
    if not matches and form_content_hash(company, figure(True)) == audit.content_hash:
        matches = figured_then = True
    voided = _voided_runs(db, parsed)
    out.update(current_hash=current, matches=matches, voided_runs=voided)
    out["figured_before_2_29"] = figured_then
    # the row says when, in the reader's own time; the sentence says who
    by = f" by {audit.username}" if audit.username else ""
    if figured_then:
        out["message"] = (
            f"Matches the books: the pay runs behind the {form} for {subject} are "
            f"as they were when it was generated{by}. SlowBooks figures this form "
            "differently now (each paycheck's taxable wages, after Section 125 "
            "and the like, toward the wage bases; the year's own Social Security "
            "wage base; one state line per state), and re-figured today it comes "
            "out differently. If it was filed, check whether a corrected form is "
            "needed."
        )
    elif out["matches"]:
        out["message"] = (
            f"Matches the books: re-figured today, the {form} for {subject} comes "
            f"out the same as when it was generated{by}."
        )
    else:
        out["message"] = (
            f"No longer matches: re-figured today, the {form} for {subject} comes "
            f"out differently from when it was generated{by} — the pay runs "
            "behind it changed after that."
        )
    if voided:
        runs = "1 pay run" if voided == 1 else f"{voided} pay runs"
        verb = "was" if voided == 1 else "were"
        out["message"] += f" {runs} in its period {verb} voided."
    return out
