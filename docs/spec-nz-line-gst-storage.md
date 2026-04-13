# NZ Line GST Storage

## Problem Statement

SlowBooks Pro 2026 now has seeded GST codes, but transaction lines still cannot store GST intent. Later GST calculation, posting, and reporting work needs each line to carry a stable GST code plus a rate snapshot before calculation logic changes.

## Scope

- Add `gst_code` and `gst_rate` to invoice, estimate, bill, purchase order, credit memo, and recurring invoice lines.
- Default missing line GST metadata to `GST15` and `0.1500`.
- Validate supplied GST codes against the seeded `gst_codes` table.
- Copy GST metadata through line clone/conversion/generation paths.
- Keep all existing document total and posting behavior unchanged.

## Non-Scope

- No line-level GST amount storage.
- No calculation service changes.
- No frontend GST selectors.
- No item-based GST inference.
- No journal posting changes.
- No new GST codes.

## Acceptance Criteria

- All six line tables have `gst_code` and `gst_rate` columns.
- Line create/update schemas expose `gst_code` and `gst_rate` with `GST15` defaults.
- Document create/update routes persist code/rate snapshots from the matched `GstCode`.
- Invalid GST codes raise HTTP 400 on write.
- Estimate-to-invoice, PO-to-bill, invoice duplicate, recurring generation, and IIF import preserve or default line GST metadata.
- Existing subtotal, tax, total, and journal behavior remains unchanged.

## Affected Files And Modules

- `app/models/invoices.py`
- `app/models/estimates.py`
- `app/models/bills.py`
- `app/models/purchase_orders.py`
- `app/models/credit_memos.py`
- `app/models/recurring.py`
- `app/schemas/invoices.py`
- `app/schemas/estimates.py`
- `app/schemas/bills.py`
- `app/schemas/purchase_orders.py`
- `app/schemas/credit_memos.py`
- `app/schemas/recurring.py`
- `app/routes/invoices.py`
- `app/routes/estimates.py`
- `app/routes/bills.py`
- `app/routes/purchase_orders.py`
- `app/routes/credit_memos.py`
- `app/routes/recurring.py`
- `app/services/recurring_service.py`
- `app/services/iif_import.py`
- `alembic/versions/*_add_line_gst_fields.py`
- `tests/test_line_gst_storage.py`

## Test Plan

- Add failing tests for model and schema defaults.
- Add failing tests for all six create paths storing GST metadata.
- Add failing tests for invalid GST code validation.
- Add failing tests for estimate-to-invoice, PO-to-bill, invoice duplicate, and recurring generation copy paths.
- Verify all Python tests with:

```bash
.venv/bin/python -m unittest discover -s tests
```

- Verify syntax with:

```bash
.venv/bin/python -m py_compile app/models/invoices.py app/models/estimates.py app/models/bills.py app/models/purchase_orders.py app/models/credit_memos.py app/models/recurring.py app/routes/invoices.py app/routes/estimates.py app/routes/bills.py app/routes/purchase_orders.py app/routes/credit_memos.py app/routes/recurring.py app/services/recurring_service.py app/services/iif_import.py
```

- Verify whitespace with:

```bash
git diff --check
```

## Risks

- Line GST metadata will exist before calculations consume it. Until the GST calculation service lands, invoice-level tax fields remain behaviorally authoritative.
