#!/usr/bin/env python3
"""Write docs/state-withholding.md from the state tables.

The tables in app/services/state_tax/tables.py are the record; this file
reads them through list_states() and the specs themselves and writes the
document, so the two cannot drift. Run it after any change to tables.py
(tests/test_state_withholding_doc.py checks the committed file matches):

    python3 scripts/gen_state_withholding_doc.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.state_tax import list_states  # noqa: E402
from app.services.state_tax.tables import DEDICATED, STATES  # noqa: E402

OUT = ROOT / "docs" / "state-withholding.md"

# The four dedicated engines carry their own figures; each module's header
# says where they came from.
ENGINE_FILES = {
    "CA": "app/services/state_tax/ca.py",
    "NY": "app/services/state_tax/ny.py",
    "OR": "app/services/state_tax/oregon.py",
    "WA": "app/services/state_tax/wa.py",
}

HEADER = """# State withholding — coverage and parameters

Every state and the District of Columbia resolves to a payroll engine. Washington, California,
New York and Oregon have dedicated engines; the other 47 are driven by the table in
`app/services/state_tax/tables.py` through `TableEngine` (annualized percentage method:
wages − standard deduction − exemptions → flat rate or brackets → ÷ pay periods + extra).

**Each row's figures were set from the state's own withholding publication named in its
Source column** (the 2026 formula or percentage-method tables, read 2026-10-31), simplified
to that structure: a state that gives a per-exemption *credit* is modelled as an exemption
amount that comes to the same tax at the state's main rate, a weekly cap is modelled as an
annual one, and a phase-out or a two-earner table is not modelled unless the row's notes
say so. The notes in each row are the simplifications. Rates change every January and
sometimes mid-year: update `tables.py`, then run `scripts/gen_state_withholding_doc.py`,
which writes this file. **Verify against the publication before filing.**

Employee inputs the engines read (Employees → form): **State W-4 allowances**,
**extra state withholding** per period, an **elected rate** (Arizona A-4), and a flat
**local tax rate** in percent where the state requires one (Indiana and Maryland counties,
Ohio cities and school districts, Pennsylvania municipalities, Michigan cities). Local
jurisdictions are not modelled individually. The pay run also passes headcounts: Paid
Leave Oregon's employer share is charged from 25 active employees (in or out of state) and
Washington PFML's from 50 active employees working in Washington.

The `GET /api/payroll/states` catalog returns the same information.

"""

FOOTER = """
Married / head-of-household schedules are in `tables.py` (married defaults to doubled thresholds where the state does that).
"""


def money(v) -> str:
    v = float(v)
    return f"{v:,.2f}".rstrip("0").rstrip(".") if v != int(v) else f"{int(v):,}"


def pct(rate) -> str:
    v = float(rate) * 100
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return f"{s}%"


def deductions(spec) -> str:
    parts = []
    if spec.std_deduction:
        parts.append("std ded " + per_status(spec.std_deduction))
    if spec.base_exemption:
        parts.append("base exemption " + per_status(spec.base_exemption))
    if spec.exemption:
        parts.append(f"{money(spec.exemption)} per allowance")
    return "; ".join(parts) or "none"


def per_status(d: dict) -> str:
    s, m = d.get("single"), d.get("married")
    h = d.get("head_of_household")
    out = f"{money(s)} / {money(m)}"
    if h is not None and h != s:
        out += f" / {money(h)} HoH"
    return out


def item(i, side: str) -> str:
    label = i.label
    if side == "employer" and "employer" not in label.lower():
        label += " (employer)"
    s = f"{label} {pct(i.rate)}"
    if i.wage_base is not None:
        s += f" to {money(i.wage_base)}"
    return s


def other_items(spec) -> str:
    items = [item(i, "employee") for i in spec.employee_items]
    items += [item(i, "employer") for i in spec.employer_items]
    return "; ".join(items) or "—"


def cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def main() -> int:
    rows = []
    for entry in list_states():
        code = entry["code"]
        if code in STATES:
            spec = STATES[code]
            method = entry["summary"]
            if spec.notes:
                method += " " + spec.notes
            rows.append(
                "| **{}** {} | {} | {} | {} | {} | {} | {} |".format(
                    code,
                    spec.name,
                    cell(method),
                    cell(deductions(spec)),
                    cell(other_items(spec)),
                    money(spec.suta_wage_base),
                    spec.year,
                    cell(spec.source),
                )
            )
        else:
            name, base, summary, year, source = DEDICATED[code]
            rows.append(
                "| **{}** {} | {} | see `{}` | see `{}` | {} | {} | {} |".format(
                    code,
                    name,
                    cell(summary),
                    ENGINE_FILES[code],
                    ENGINE_FILES[code],
                    money(base),
                    year,
                    cell(source),
                )
            )

    brackets = []
    for code in sorted(STATES):
        spec = STATES[code]
        if spec.method != "brackets":
            continue
        sched = "; ".join(
            f"{pct(r)} from {money(lo)}" for lo, r in spec.brackets["single"]
        )
        brackets.append(f"- **{code}**: {sched}")

    doc = (
        HEADER
        + "| State | Method | Deductions / exemptions | Other items (employee / employer) | SUTA base | Year | Source |\n"
        + "|---|---|---|---|---|---|---|\n"
        + "\n".join(rows)
        + "\n\n## Bracket schedules (single filer, annual, marginal)\n\n"
        + "\n".join(brackets)
        + "\n"
        + FOOTER
    )
    if "--check" in sys.argv:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current.replace("\r\n", "\n") != doc:
            print(f"{OUT.relative_to(ROOT)} is out of date; run {Path(__file__).name}")
            return 1
        print("up to date")
        return 0
    OUT.write_text(doc, encoding="utf-8", newline="\n")
    print(f"wrote {OUT.relative_to(ROOT)} ({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
