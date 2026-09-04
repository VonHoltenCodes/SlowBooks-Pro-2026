# ============================================================================
# Pay Stub PDF — itemized employee earnings statement
# ----------------------------------------------------------------------------
# Renders a printable pay stub carrying every field California Labor Code 226
# requires: gross wages, hours worked (with the regular/OT/double-time split),
# each deduction itemized individually, net wages, pay-period dates, employee
# identification (name + last-4 SSN) and employer identification, plus
# current-period and year-to-date columns.
#
# Deduction line items are read from PayStub.detail_json (a JSON string of
# {label: amount}); when absent, the explicit stub columns are used instead.
# ============================================================================

import json
from decimal import Decimal

from app.services.accounting import _q
from app.services.pdf_service import _jinja_env, render_pdf

# detail_json keys that are deductions (withheld from the employee). Anything
# else in the blob is an employer-side or informational line we skip on the
# employee deduction list.
_DEDUCTION_KEYS = {
    "federal_income_tax": "Federal Income Tax",
    "social_security_employee": "Social Security",
    "medicare_employee": "Medicare",
    "state_income_tax": "State Income Tax",
    "state_other_employee": "State Other (SDI/PFML)",
    "pretax_deductions": "Pre-Tax Deductions",
    "posttax_deductions": "Post-Tax Deductions",
}

# Employer-side / informational keys excluded from the employee deduction list.
_EMPLOYER_KEYS = {
    "employer_social_security",
    "employer_medicare",
    "futa",
    "suta",
    "employer_ss_tax",
    "employer_medicare_tax",
    "state_other_employer",
}

# Keys that ADD to net pay (accountable-plan reimbursements). These would
# otherwise fall through into _deduction_lines() and visually subtract,
# even though the net-pay math in routes/payroll.py adds them in correctly.
_ADDITION_KEYS = {
    "reimbursements": "Reimbursements (non-taxable)",
}


def _humanize(key: str) -> str:
    """Turn a detail_json key into a human-readable label."""
    if key.startswith("benefit:"):
        return key.split(":", 1)[1]
    if key.startswith("garnishment:"):
        return "Garnishment " + key.split(":")[1].replace("_", " ").title()
    return _DEDUCTION_KEYS.get(key) or key.replace("_", " ").title()


def _is_employer_key(key: str) -> bool:
    return key in _EMPLOYER_KEYS or key.startswith("employer_benefit:")


def _deduction_lines(stub) -> list[dict]:
    """Itemize the employee-side deductions for the current period.

    Prefers PayStub.detail_json; falls back to the explicit stub columns when
    the JSON blob is empty, missing or unparseable.
    """
    lines: list[dict] = []
    raw = getattr(stub, "detail_json", None)
    parsed: dict | None = None
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                parsed = data
        except (ValueError, TypeError):
            parsed = None

    if parsed:
        # Benefit codes are itemized individually; the pre/post-tax totals
        # that also live in the blob would double-count them.
        has_codes = any(
            k.startswith("benefit:") or k.startswith("other_p") for k in parsed
        )
        for key, amount in parsed.items():
            if _is_employer_key(key) or key in _ADDITION_KEYS:
                continue
            if has_codes and key in ("pretax_deductions", "posttax_deductions"):
                continue
            value = _q(amount)
            if value == 0:
                continue
            lines.append({"label": _humanize(key), "amount": value})
        if lines:
            return lines

    # Fallback — itemize directly from the stub columns.
    fallback = [
        ("Federal Income Tax", stub.federal_tax),
        ("State Income Tax", stub.state_tax),
        ("State Other (SDI/PFML)", stub.state_other_employee),
        ("Social Security", stub.ss_tax),
        ("Medicare", stub.medicare_tax),
        ("Pre-Tax Deductions", stub.pretax_deductions),
        ("Post-Tax Deductions", stub.posttax_deductions),
    ]
    for label, amount in fallback:
        value = _q(amount)
        if value != 0:
            lines.append({"label": label, "amount": value})
    return lines


def _addition_lines(stub) -> list[dict]:
    """Non-taxable additions to net pay (e.g. accountable-plan reimbursements).

    Reads PayStub.reimbursements directly and supplements with any
    `_ADDITION_KEYS` entries that show up in detail_json. These items have
    already been added into stub.net_pay by routes/payroll.py — this list
    is for display so the stub itemizes WHY net is higher than gross-minus-
    deductions.
    """
    lines: list[dict] = []
    reimb = _q(getattr(stub, "reimbursements", None))
    if reimb != 0:
        lines.append({"label": _ADDITION_KEYS["reimbursements"], "amount": reimb})

    raw = getattr(stub, "detail_json", None)
    if raw:
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            data = None
        if isinstance(data, dict):
            for key, amount in data.items():
                if key not in _ADDITION_KEYS or key == "reimbursements":
                    continue
                value = _q(amount)
                if value == 0:
                    continue
                lines.append({"label": _ADDITION_KEYS[key], "amount": value})
    return lines


def generate_paystub_pdf(stub, employee, pay_run, company: dict, ytd: dict) -> bytes:
    """Render an itemized employee pay stub to a PDF.

    `ytd` is a caller-supplied dict of year-to-date totals (keys such as
    gross, federal, ss, medicare, state, net).
    """
    deductions = _deduction_lines(stub)
    total_deductions = sum((d["amount"] for d in deductions), Decimal("0"))
    additions = _addition_lines(stub)
    total_additions = sum((a["amount"] for a in additions), Decimal("0"))

    ctx = {
        "stub": stub,
        "employee": employee,
        "pay_run": pay_run,
        "company": company or {},
        "ytd": ytd or {},
        "deductions": deductions,
        "total_deductions": _q(total_deductions),
        "additions": additions,
        "total_additions": _q(total_additions),
        "gross_pay": _q(stub.gross_pay),
        "net_pay": _q(stub.net_pay),
        "regular_hours": _q(stub.regular_hours),
        "overtime_hours": _q(stub.overtime_hours),
        "doubletime_hours": _q(stub.doubletime_hours),
        "total_hours": _q(stub.hours),
    }
    template = _jinja_env.get_template("paystub_pdf.html")
    html_str = template.render(**ctx)
    return render_pdf(html_str)
