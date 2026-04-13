# NZ Address Labels And Defaults

## Problem Statement

SlowBooks Pro 2026 still exposes US address terminology and defaults in contact, company, document, and import/export surfaces. The app stores NZ localization settings, but customer/vendor/company addresses still present `State`, `ZIP`, and `US` defaults. This blocks clean NZ-facing documents before deeper GST, payroll, and import/export work.

## Scope

- Keep existing database and API field names for compatibility.
- Present address fields as `Region` and `Postcode` in company, customer, and vendor forms.
- Default new customer and vendor countries to `NZ`.
- Render document address lines without US comma formatting.
- Export customer/vendor CSV files with `Region` and `Postcode` headers.
- Import customer/vendor CSV files from both new `Region`/`Postcode` headers and legacy `State`/`ZIP` headers.
- Keep QuickBooks IIF `ADDR*` wire fields, but remove US-specific helper naming/comments where touched.
- Update the localization summary to mark this address label/default slice complete.

## Non-Scope

- No database column renames.
- No API JSON field renames.
- No Alembic migrations.
- No payroll employee address changes.
- No GST, tax posting, report, or calculation changes.
- No full international address engine.

## Acceptance Criteria

- `Customer` and `Vendor` model defaults use `NZ` for country fields.
- `CustomerCreate` and `VendorCreate` schema defaults use `NZ`.
- Company, customer, and vendor forms show `Region` and `Postcode`, not `State` and `ZIP`.
- New customer/vendor forms include existing country API fields defaulted to `NZ`.
- Invoice, estimate, statement, and invoice email address lines render as city/region/postcode without a comma.
- Customer/vendor CSV export headers use `Region` and `Postcode`.
- Customer/vendor CSV import accepts `Region`/`Postcode` and legacy `State`/`ZIP`.
- Existing stored records and API clients remain compatible with `state`/`zip`-named fields.

## Affected Files And Modules

- `app/models/contacts.py`
- `app/schemas/contacts.py`
- `app/static/js/settings.js`
- `app/static/js/customers.js`
- `app/static/js/vendors.js`
- `app/templates/invoice_pdf.html`
- `app/templates/invoice_pdf_v2.html`
- `app/templates/estimate_pdf.html`
- `app/templates/statement_pdf.html`
- `app/templates/invoice_email.html`
- `app/services/csv_export.py`
- `app/services/csv_import.py`
- `app/services/iif_export.py`
- `app/services/iif_import.py`
- `docs/localization_summary.md`
- `tests/test_nz_address_labels.py`
- `tests/js_address_labels.test.js`

## Test Plan

- Add failing Python tests for contact model and schema country defaults.
- Add failing Python tests for CSV export headers and CSV import compatibility.
- Add failing Python tests for rendered invoice, estimate, statement, and email address lines.
- Add failing Node tests for settings, customer, and vendor form labels and hidden country defaults.
- Verify all Python tests with:

```bash
.venv/bin/python -m unittest discover -s tests
```

- Verify frontend address tests with:

```bash
node tests/js_address_labels.test.js
```

- Verify JavaScript syntax with:

```bash
node --check app/static/js/settings.js
node --check app/static/js/customers.js
node --check app/static/js/vendors.js
```

- Verify whitespace with:

```bash
git diff --check
```

## Risks

- CSV headers are a user-facing interface. Export moves to NZ labels, but import must remain backward compatible with legacy State/ZIP files.
- Database/API field names stay US-shaped for this slice. Later schema cleanup should be planned separately if needed.
