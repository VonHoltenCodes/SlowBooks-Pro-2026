# NZ GST Domain Model

## Problem Statement

SlowBooks Pro 2026 still treats tax as a generic invoice-level rate, but New Zealand GST work needs stable domain codes before line-level GST storage, calculation, reports, and posting can be added. Without persistent GST codes, later implementation would either duplicate magic strings or require rework when line tables start referencing GST behavior.

## Scope

- Add a persistent `gst_codes` reference table.
- Seed New Zealand system GST codes: `GST15`, `ZERO`, `EXEMPT`, and `NO_GST`.
- Add a read-only API for listing active GST codes and fetching one code by code.
- Add an idempotent helper for test/dev databases that are created directly from SQLAlchemy metadata.
- Update localization planning docs to mark the GST code foundation complete.

## Non-Scope

- No invoice, estimate, bill, purchase order, credit memo, or recurring schema changes.
- No GST code foreign keys on line tables.
- No calculation, journal posting, GST return, or UI selector behavior.
- No editable GST code admin API.
- No `IMPORT` or `REVERSE_CHARGE` codes yet.

## Acceptance Criteria

- `gst_codes` stores code, name, description, rate, category, active/system flags, sort order, and timestamps.
- `GST15` has rate `0.1500` and category `taxable`.
- `ZERO`, `EXEMPT`, and `NO_GST` have rate `0.0000` with distinct categories.
- Default seeding is idempotent and does not duplicate rows.
- `GET /api/gst-codes` returns active codes in stable sort order.
- `GET /api/gst-codes/{code}` returns a matching code or raises 404.
- Existing tax rates, tax amounts, and posting behavior are unchanged.

## Affected Files And Modules

- `app/models/gst.py`
- `app/models/__init__.py`
- `app/schemas/gst.py`
- `app/routes/gst.py`
- `app/main.py`
- `alembic/versions/*_add_gst_codes.py`
- `docs/localization_summary.md`
- `tests/test_gst_codes.py`

## Test Plan

- Add failing Python tests for model defaults and default code seeding.
- Add failing Python tests proving seeding is idempotent.
- Add failing Python tests proving list and detail route behavior.
- Add failing Python tests proving unknown code returns 404.
- Verify all Python tests with:

```bash
.venv/bin/python -m unittest discover -s tests
```

- Verify syntax with:

```bash
.venv/bin/python -m py_compile app/models/gst.py app/schemas/gst.py app/routes/gst.py
```

- Verify whitespace with:

```bash
git diff --check
```

## Risks

- This is a reference-data foundation only. Treating it as GST calculation support would be premature until line storage and calculation services exist.
- API routes are intentionally read-only to avoid custom code drift before calculation/reporting rules are implemented.
