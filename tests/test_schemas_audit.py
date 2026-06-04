"""
Schema audit as a unit test.

Catches the `date: date` field-name-shadows-type collision that
jake-378 fixed in invoices + estimates (commits 48cdb79, e12bbb1) and
that this branch fixed across 9 more schemas.

The collision:
    from datetime import date
    class Update(BaseModel):
        date: Optional[date] = None    # <-- pydantic 2.13 reads
                                        #     `Optional[date]` as
                                        #     `Optional[<the field>]`,
                                        #     which validates as None-only.

The fix (jake's pattern):
    from datetime import date as dt_date
    class Update(BaseModel):
        date: Optional[dt_date] = None

This test fails if any schema file imports `date` directly AND has a
field literally named `date`, since that combination has been verified
to bite at runtime.
"""

from __future__ import annotations

import re
from pathlib import Path

SCHEMAS_DIR = Path(__file__).resolve().parents[1] / "app" / "schemas"

_BARE_DATE_IMPORT = re.compile(
    r"^from datetime import (?:[^()\n]*\b)?date(?!\s+as\b)\b",
    re.MULTILINE,
)
_DATE_FIELD = re.compile(r"^\s+date:\s", re.MULTILINE)


def test_no_date_field_shadows_date_type():
    """Every schema with a `date` field must alias the `date` type on import.

    Pydantic 2.13 still has the collision (verified with a reproducer
    against `Optional[date]` Update models). The fix is to alias the
    import: `from datetime import date as dt_date`. This test enforces
    the rule across every schema module so the bug can't drift back in
    via a new schema file.
    """
    offenders = []
    for py in sorted(SCHEMAS_DIR.glob("*.py")):
        if py.name == "__init__.py":
            continue
        text = py.read_text(encoding="utf-8")
        has_bare_date_import = bool(_BARE_DATE_IMPORT.search(text))
        has_date_field = bool(_DATE_FIELD.search(text))
        if has_bare_date_import and has_date_field:
            offenders.append(
                f"  {py.name}: imports `date` without alias AND has a field "
                f"named `date` — use `from datetime import date as dt_date`"
            )
    assert not offenders, (
        "Pydantic schemas with the date-field-shadows-date-type collision:\n"
        + "\n".join(offenders)
    )
