# NZ GST Calculation Service

## Problem Statement

SlowBooks Pro 2026 stores GST codes and line-level GST metadata, but document totals still use document-level `subtotal * tax_rate` calculations. That cannot represent standard-rated, zero-rated, exempt, and no-GST lines together, and it cannot calculate GST-inclusive prices using Inland Revenue's `3/23` method.

## Scope

- Add one shared backend GST calculation service for invoices, estimates, bills, purchase orders, credit memos, and generated recurring invoices.
- Support GST-exclusive and GST-inclusive pricing using the `prices_include_gst` setting.
- Calculate GST-exclusive subtotal, GST amount, GST-inclusive total, taxable totals, zero-rated totals, exempt totals, no-GST totals, output GST, and input GST.
- Replace backend `subtotal * tax_rate` document total calculations with line GST calculations.
- Update frontend document previews and save payloads to use per-line GST codes.
- Keep existing `tax_rate`, `subtotal`, `tax_amount`, and `total` API fields for compatibility.

## Non-Scope

- Do not add new database columns for calculated line GST amounts.
- Do not split or rename GST accounts; journal account rework remains a later slice.
- Do not build GST return reports.
- Do not remove legacy `tax_rate` fields yet.

## Acceptance Criteria

- Standard-rated exclusive lines calculate GST as `net * 15%`.
- Standard-rated inclusive lines calculate GST as `gross * 3/23`.
- Zero-rated, exempt, and no-GST lines produce no GST.
- Mixed-line documents use line GST codes, not the document `tax_rate`, for totals.
- Sales documents expose output GST totals; purchase documents expose input GST totals through the shared result.
- Backend journal entries remain balanced after the new total calculation.
- Frontend previews match backend examples for exclusive and inclusive pricing.

## Affected Files / Modules

- `app/services/gst_calculations.py`
- `app/routes/invoices.py`
- `app/routes/estimates.py`
- `app/routes/bills.py`
- `app/routes/purchase_orders.py`
- `app/routes/credit_memos.py`
- `app/services/recurring_service.py`
- `app/static/js/utils.js`
- `app/static/js/invoices.js`
- `app/static/js/estimates.js`
- `app/static/js/bills.js`
- `app/static/js/purchase_orders.js`
- `app/static/js/credit_memos.js`
- `app/static/js/recurring.js`
- `tests/test_gst_calculations.py`
- `tests/test_document_gst_calculations.py`
- `tests/js_gst_calculations.test.js`

## Test Plan

- Unit test exclusive, inclusive, zero-rated, exempt, no-GST, mixed-line, and rounding behavior in the backend service.
- Route-test invoice and bill totals to prove line GST overrides document `tax_rate`.
- Route-test balanced invoice and bill journal entries with inclusive GST.
- JavaScript-test frontend GST calculations and selector payload extraction.
- Run the full Python and JavaScript test set before completion.
