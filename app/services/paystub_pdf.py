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
    known = _DEDUCTION_KEYS.get(key)
    if known:
        return known
    if " " in key:
        # A state engine's own label ("OR income tax", "NY PFL"): its capitals
        # are state codes and acronyms, so only its lowercase words are
        # capitalized. title() printed "Or Income Tax" (2.18.0 gate, NEW-2).
        return " ".join(
            w[:1].upper() + w[1:] if w.islower() else w for w in key.split()
        )
    return key.replace("_", " ").title()


def _is_employer_key(key: str) -> bool:
    # The state engines itemize every line under a human label, the
    # employer's share included ("WA PFML (employer)", "WA L&I (employer)");
    # those are the company's cost, never the employee's deduction.
    return (
        key in _EMPLOYER_KEYS
        or key.startswith("employer_benefit:")
        or "employer" in key.lower()
    )


def _is_state_income_line(key: str) -> bool:
    """A state engine's own income-tax line ("OR income tax", "State income
    tax (generic)") — the same money as the generic ``state_income_tax``."""
    k = key.lower()
    return "income tax" in k and "local" not in k and key != "state_income_tax"


def _parsed_detail(stub) -> dict | None:
    raw = getattr(stub, "detail_json", None)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _deduction_items(stub) -> list[tuple[str, str, Decimal]]:
    """(key, label, amount) for every employee-side deduction on one stub.

    Prefers PayStub.detail_json; falls back to the explicit stub columns when
    the JSON blob is empty, missing or unparseable. The state's own income tax
    line replaces the generic ``state_income_tax`` total, which is the same
    money: both were printed, so an Oregon stub counted its income tax twice
    and Total Deductions came to $548.53 where $404.06 was withheld (2.18.0
    gate, macbase1 NEW-2)."""
    parsed = _parsed_detail(stub)
    items: list[tuple[str, str, Decimal]] = []
    if parsed:
        # Benefit codes are itemized individually; the pre/post-tax totals
        # that also live in the blob would double-count them.
        has_codes = any(
            k.startswith("benefit:") or k.startswith("other_p") for k in parsed
        )
        itemized_state = any(_is_state_income_line(k) for k in parsed)
        for key, amount in parsed.items():
            if _is_employer_key(key) or key in _ADDITION_KEYS:
                continue
            if key.endswith(":note"):
                continue
            if has_codes and key in ("pretax_deductions", "posttax_deductions"):
                continue
            if itemized_state and key == "state_income_tax":
                continue
            try:
                value = _q(amount)
            except Exception:
                continue  # a note or other text, not an amount
            if value == 0:
                continue
            items.append((key, _humanize(key), value))
        if items:
            return items

    # Fallback — itemize directly from the stub columns.
    fallback = [
        ("federal_income_tax", "Federal Income Tax", stub.federal_tax),
        ("state_income_tax", "State Income Tax", stub.state_tax),
        ("state_other_employee", "State Other (SDI/PFML)", stub.state_other_employee),
        ("social_security_employee", "Social Security", stub.ss_tax),
        ("medicare_employee", "Medicare", stub.medicare_tax),
        ("pretax_deductions", "Pre-Tax Deductions", stub.pretax_deductions),
        ("posttax_deductions", "Post-Tax Deductions", stub.posttax_deductions),
    ]
    for key, label, amount in fallback:
        value = _q(amount)
        if value != 0:
            items.append((key, label, value))
    return items


def _deduction_lines(stub, ytd_stubs=None) -> list[dict]:
    """The stub's deduction lines, each with its year-to-date: the same line
    summed over ``ytd_stubs`` (the employee's stubs this year up to this pay
    date). Without ytd_stubs a line's ytd is None."""
    ytd: dict[str, Decimal] = {}
    for other in ytd_stubs or ():
        for key, _label, amount in _deduction_items(other):
            ytd[key] = ytd.get(key, Decimal("0")) + amount
    return [
        {
            "key": key,
            "label": label,
            "amount": amount,
            "ytd": _q(ytd[key]) if key in ytd else None,
        }
        for key, label, amount in _deduction_items(stub)
    ]


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


def generate_paystub_pdf(
    stub, employee, pay_run, company: dict, ytd: dict, ytd_stubs=None
) -> bytes:
    """Render an itemized employee pay stub to a PDF.

    `ytd` is a caller-supplied dict of year-to-date totals (keys such as
    gross, net). `ytd_stubs` are the employee's stubs this year up to and
    including this one; each deduction line's YTD is that line summed over
    them, and Total Deductions YTD is every line's (the template used to
    guess a line's YTD from its label, and read a total nothing filled in).
    """
    deductions = _deduction_lines(stub, ytd_stubs)
    total_deductions = sum((d["amount"] for d in deductions), Decimal("0"))
    total_deductions_ytd = None
    if ytd_stubs:
        total_deductions_ytd = _q(
            sum(
                (a for other in ytd_stubs for _k, _l, a in _deduction_items(other)),
                Decimal("0"),
            )
        )
    additions = _addition_lines(stub)
    total_additions = sum((a["amount"] for a in additions), Decimal("0"))

    # A void run's stub says VOID, like a void deposit slip (#432). The run's
    # status may be the enum or its plain value depending on the caller.
    status = getattr(pay_run, "status", None)
    void = getattr(status, "value", status) == "void"

    ctx = {
        "stub": stub,
        "employee": employee,
        "pay_run": pay_run,
        "void": void,
        "company": company or {},
        "ytd": ytd or {},
        "deductions": deductions,
        "total_deductions": _q(total_deductions),
        "total_deductions_ytd": total_deductions_ytd,
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
