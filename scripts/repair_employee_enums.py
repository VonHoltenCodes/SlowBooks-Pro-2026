#!/usr/bin/env python3
"""
Repair employees whose pay_type or role hold values outside their enums.

Both columns are Enum(...) in the model but were accepted as bare strings by
EmployeeCreate/EmployeeUpdate until that was fixed, so an invalid value could
be written straight through. Reading such a row then raises LookupError, which
surfaces as HTTP 500 — and because the whole list is loaded in one query, a
single bad row makes GET /api/employees fail for the entire company.

Nothing in the API can repair these rows: they cannot be read, updated or
deleted through it. This script works in raw SQL for that reason, deliberately
bypassing the ORM that cannot load them.

Usage:
    python3 scripts/repair_employee_enums.py          # detect only (dry run)
    python3 scripts/repair_employee_enums.py --apply  # normalize bad values
    python3 scripts/repair_employee_enums.py --json   # machine-readable output

Repair policy:
    pay_type -> 'HOURLY'    (PayType.HOURLY, the model default)
    role     -> 'EMPLOYEE'  (EmployeeRole.EMPLOYEE, least privilege)

Both are conservative. pay_rate is left untouched, so a salaried employee
mis-repaired to hourly keeps their stored rate and is visible for correction
rather than silently re-rated. role falls back to the LEAST privileged value
so a repair can never widen someone's access.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text

from app.database import SessionLocal
from app.models.payroll import EmployeeRole, PayType

# SQLAlchemy's Enum() type persists the member NAME ("HOURLY"), not the value
# ("hourly"), and rejects anything else on read. Repairing to the value would
# leave the row just as unreadable as it was — verified by the regression test,
# which caught exactly that mistake in an earlier draft of this script.
COLUMNS = {
    "pay_type": (PayType, PayType.HOURLY.name),
    "role": (EmployeeRole, EmployeeRole.EMPLOYEE.name),
}


def scan(db):
    """Return rows whose enum columns hold values the enums do not define."""
    findings = []
    rows = db.execute(
        text("SELECT id, first_name, last_name, pay_type, role FROM employees")
    ).mappings()
    for row in rows:
        bad = {}
        for col, (enum_cls, _default) in COLUMNS.items():
            value = row[col]
            if value is None:
                continue
            # Names only: a row holding the lowercase *value* is equally
            # unreadable, so it is a finding too, not a false positive.
            valid = {m.name for m in enum_cls}
            if str(value) not in valid:
                bad[col] = value
        if bad:
            findings.append(
                {
                    "id": row["id"],
                    "name": f"{row['first_name'] or ''} {row['last_name'] or ''}".strip(),
                    "invalid": bad,
                }
            )
    return findings


def repair(db, findings):
    for item in findings:
        sets, params = [], {"id": item["id"]}
        for col in item["invalid"]:
            sets.append(f"{col} = :{col}")
            params[col] = COLUMNS[col][1]
        db.execute(
            text(f"UPDATE employees SET {', '.join(sets)} WHERE id = :id"), params
        )
    db.commit()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="write the repairs (default: dry run)"
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        findings = scan(db)

        if args.json:
            print(
                json.dumps(
                    {
                        "count": len(findings),
                        "employees": findings,
                        "applied": args.apply,
                    },
                    indent=2,
                )
            )
        elif not findings:
            print("No employees with invalid pay_type or role. Nothing to repair.")
        else:
            print(f"{len(findings)} employee row(s) hold values outside their enums:\n")
            for item in findings:
                for col, value in item["invalid"].items():
                    print(
                        f"  employee {item['id']} ({item['name'] or 'unnamed'}): "
                        f"{col} = {value!r} -> {COLUMNS[col][1]!r}"
                    )
            if not args.apply:
                print("\nDry run. Re-run with --apply to write these repairs.")

        if findings and args.apply:
            repair(db, findings)
            remaining = scan(db)
            if not args.json:
                print(
                    f"\nRepaired {len(findings)} row(s). Remaining invalid: {len(remaining)}"
                )
            return 0 if not remaining else 1
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
