# New Zealand Localization Summary

This document captures the current localization audit for turning SlowBooks Pro 2026 into a New Zealand-localized version.

The `nz-localization` branch is the working branch. It has been synced with upstream changes through commit `99bbcc7`, so the plan below reflects the current branch state rather than the original audit-time checkout instructions.

## Assessment

The app is strongly US-shaped in tax, payroll, addresses, reports, PDF/UI copy, seed data, and import/export behavior. The broad direction from the external review is accurate, but the code audit found several additional surfaces that need attention:

- Purchase-side GST is more than a label change. Bills already debit account `2200` for tax, so the current single "Sales Tax Payable" account is being used as both collected-output tax and purchase-input tax.
- Import/export paths are a major localization surface. CSV and IIF logic use State/ZIP conventions, QuickBooks-style address parsing, and "Sales Tax Payable" labels.
- GST calculation is duplicated across invoices, bills, credit memos, purchase orders, estimates, and recurring invoices.
- Upstream now prefills `default_tax_rate` into invoice, estimate, purchase order, and recurring invoice forms. That is useful as a temporary bridge, but it must not become the GST design. NZ GST still needs line-level GST codes, inclusive pricing, and separate output/input GST treatment.
- Upstream added shared report period UI for several reports. Reuse that flow for GST returns instead of building a separate date picker.
- Document updates recalculate totals but do not consistently rebuild/reverse journal entries, which becomes riskier once GST posting rules are introduced.
- A small `tests/` directory now exists for settings localization. GST, reporting, and payroll changes still need focused regression tests before implementation.
- Schema changes need Alembic migrations under `alembic/versions`.

## Official NZ Anchors

Use official Inland Revenue and ACC material when implementing the localized behavior:

- Inland Revenue GST overview: <https://www.ird.govt.nz/gst>
- Inland Revenue charging GST: <https://www.ird.govt.nz/gst/charging-gst>
- Inland Revenue claiming GST: <https://www.ird.govt.nz/en/gst/claiming-gst>
- Inland Revenue payday filing paper information: <https://www.ird.govt.nz/employing-staff/payday-filing/filing-employment-information-by-paper>
- Inland Revenue PAYE file upload specification 2026: <https://www.ird.govt.nz/-/media/project/ir/home/documents/digital-service-providers/payday-filing-file-upload-specification-2026.pdf?modified=20250804033830>
- Inland Revenue KiwiSaver employee deduction rates: <https://www.ird.govt.nz/kiwisaver/kiwisaver-for-employers/contributions-and-deductions/update-employee-deduction-rate-for-kiwisaver>
- Inland Revenue KiwiSaver employer contributions: <https://www.ird.govt.nz/kiwisaver/kiwisaver-for-employers/contributions-and-deductions/employer-contributions-to-kiwisaver-and-complying-funds>
- Inland Revenue ACC levy rates: <https://www.ird.govt.nz/acclevy>

Key design facts from those sources:

- GST is charged at `15%`.
- For GST-inclusive prices, the GST component is calculated as `3/23` of the GST-inclusive price.
- GST payable/refundable is based on output tax less input tax.
- Payroll should align with current payday filing / Employment Information processes, not legacy US-style withholding.
- PAYE tax codes include codes such as `M`, `ME`, `S`, `SH`, `ST`, `SA`, and `SL` variants.
- KiwiSaver employee contribution rates are listed as `3.5%`, `4%`, `6%`, `8%`, or `10%`, with employer contributions generally at least `3%`.
- ACC earners' levy is part of PAYE calculations for ordinary PAYE wages; the app should still version rates/caps carefully.

## Confirmed Code Gaps

### Settings

Settings are stored as key-value rows and the API only accepts keys listed in `DEFAULT_SETTINGS`.

Relevant files:

- `app/models/settings.py`
- `app/routes/settings.py`
- `app/static/js/settings.js`

NZ fields have been added to defaults and surfaced in the settings UI. Keep expanding this foundation through tests when other areas begin consuming localization settings.

### Currency And Dates

Currency and date formatting now has a shared foundation:

- `app/services/formatting.py` provides Python currency and date helpers driven by company settings.
- PDF invoice, estimate, statement, and invoice email rendering use the shared Python formatting path.
- `app/static/js/utils.js` provides frontend currency and date helpers driven by `App.settings`.
- Existing report, transaction, dashboard, settings, and company-list UI callers use the shared frontend helper path for known currency/date display.

### GST Calculation

Tax is currently invoice-level rather than line-level. `Item.is_taxable` exists, but invoice total calculation ignores it.

Relevant files:

- `app/models/items.py`
- `app/models/invoices.py`
- `app/routes/invoices.py`
- `app/routes/estimates.py`
- `app/routes/bills.py`
- `app/routes/credit_memos.py`
- `app/routes/purchase_orders.py`
- `app/services/recurring_service.py`
- `app/static/js/invoices.js`
- `app/static/js/estimates.js`
- `app/static/js/purchase_orders.js`
- `app/static/js/recurring.js`

Upstream now reads `default_tax_rate` from settings in these frontend forms. The GST replacement must update both backend posting/calculation and frontend preview/save behavior.

### GST Posting

The app uses account `2200` as "Sales Tax Payable" for both sales tax collected and tax on bills.

Relevant files:

- `app/services/accounting.py`
- `app/routes/invoices.py`
- `app/routes/bills.py`
- `app/routes/credit_memos.py`
- `app/services/recurring_service.py`
- `app/seed/chart_of_accounts.py`

### Payroll

Payroll is US-specific in both storage and calculation:

- Employee uses `ssn_last_four`, filing status, allowances, state, and zip.
- Pay stubs store federal tax, state tax, Social Security, and Medicare.
- Payroll service hardcodes Social Security, Medicare, federal brackets, and a flat state tax.

Relevant files:

- `app/models/payroll.py`
- `app/schemas/payroll.py`
- `app/routes/payroll.py`
- `app/routes/employees.py`
- `app/services/payroll_service.py`
- `app/static/js/payroll.js`
- `app/static/js/employees.js`

### Addresses

Customer and vendor records default country to `US`; forms and templates still use State/ZIP labels.

Relevant files:

- `app/models/contacts.py`
- `app/schemas/contacts.py`
- `app/static/js/customers.js`
- `app/static/js/vendors.js`
- `app/static/js/settings.js`
- `app/templates/invoice_pdf.html`
- `app/templates/invoice_pdf_v2.html`
- `app/templates/estimate_pdf.html`
- `app/templates/statement_pdf.html`
- `app/templates/invoice_email.html`

Address labels and defaults now have a compatibility-preserving NZ foundation: customer/vendor create defaults use `NZ`, company/customer/vendor forms show `Region` and `Postcode`, document address lines use NZ city/region/postcode formatting, CSV export uses `Region`/`Postcode`, and CSV import accepts both the NZ headers and legacy `State`/`ZIP`. Database/API field names are still unchanged for compatibility.

### Tax Reporting

Schedule C is hardcoded in routes, model comments, service mappings, CSV headings, UI, and docs.

The general reports UI now includes reusable period selection and custom date handling in `app/static/js/reports.js`. The existing Sales Tax report should be replaced with a GST return while reusing that period selector.

Relevant files:

- `app/models/tax.py`
- `app/routes/tax.py`
- `app/services/tax_export.py`
- `app/static/js/tax.js`
- `README.md`

### Import And Export

CSV and IIF import/export encode US address assumptions and sales-tax labels.

Relevant files:

- `app/services/csv_import.py`
- `app/services/csv_export.py`
- `app/services/iif_import.py`
- `app/services/iif_export.py`
- `app/static/js/iif.js`

## Consolidated Todo

1. Keep branch hygiene clean:
   Work on `nz-localization`, sync from upstream before each implementation slice, and re-check this plan for newly touched tax/report/settings paths.

2. Maintain the localization foundation:
   Settings for `country=NZ`, `tax_regime=NZ`, `currency=NZD`, `locale=en-NZ`, `timezone=Pacific/Auckland`, `ird_number`, `gst_number`, `gst_registered`, `gst_basis`, `gst_period`, and `prices_include_gst` now exist. Add tests when consuming these settings from formatting, GST, reports, imports, or payroll.

3. Centralize formatting:
   Shared Python and frontend formatting helpers are in place and wired into known PDF, email, report, transaction, dashboard, settings, and company-list date/currency surfaces. Keep using these helpers for new GST, payroll, address, CSV, IIF, and reporting work.

4. Refactor address fields without breaking data:
   Label/default remapping is in place for company, customer, vendor, PDF/email, CSV, and IIF surfaces while preserving existing database/API field names. Payroll employee address fields and any future schema renames remain separate work.

5. Create a GST domain model:
   Add GST codes/rates such as `GST15`, `ZERO`, `EXEMPT`, and `NO_GST`. Consider `IMPORT` or `REVERSE_CHARGE` later if needed.

6. Store GST per line:
   Add GST code/rate to invoice lines, estimate lines, credit memo lines, bill lines, purchase order lines, and recurring invoice lines.

7. Build one GST calculation service:
   Support exclusive and inclusive pricing, `3/23` GST-inclusive extraction, rounding, taxable totals, zero-rated totals, exempt totals, output GST, and input GST. Replace the current `subtotal * tax_rate` logic in backend routes and frontend preview calculators. Do not solve GST by setting `default_tax_rate=15.0`; that cannot represent zero-rated/exempt lines or GST-inclusive prices.

8. Rework journal posting:
   Replace `get_sales_tax_account_id()` with GST-aware account helpers. Decide whether to use separate `GST Collected` and `GST Paid` accounts plus `GST Payable`, or one GST control account with reporting splits.

9. Fix posting lifecycle behavior:
   Ensure edits, voids, duplicates, and conversions either reverse/repost journal entries correctly or enforce immutable posted documents.

10. Replace the sales tax report with a GST return report:
    Include sales/output GST, purchases/input GST, net GST payable/refundable, GST period/basis, zero-rated/exempt supplies, and drilldowns to source transactions. Reuse the existing reports period selector/custom date UI instead of adding a second date-range pattern.

11. Replace Schedule C:
    Remove or hide Schedule C routes/UI for NZ mode. Replace later with NZ income tax outputs after deciding whether the target is IR3 business summary, IR10-style financial statements, or accountant export.

12. Rebuild payroll around NZ:
    Replace SSN/filing status/allowances with IRD number, tax code, KiwiSaver status/rate, student loan, child support, start/end dates, ESCT rate, pay frequency, and any required payday filing fields.

13. Implement PAYE using versioned official tables:
    Use IRD PAYE tables/specs by tax year instead of hardcoded constants. Model PAYE, student loan, KiwiSaver employee deduction, employer KiwiSaver, ESCT, child support, and payday filing fields.

14. Replace payroll outputs:
    Add NZ payslip labels and Employment Information / payday filing export. Avoid naming this "IRFile/EMS" until confirmed by current IRD requirements.

15. Seed NZ chart and demo data:
    Create NZ chart accounts for GST, PAYE, KiwiSaver, ESCT, wages, and any ACC-related expenses/liabilities as appropriate. Replace IRS Pub 583 seed/demo data with NZ examples.

16. Add Alembic migrations:
    Any model/schema changes need migrations under `alembic/versions`. Also ensure multi-company creation and seed flows use the NZ defaults and chart.

17. Update UI copy everywhere:
    Replace Sales Tax, Federal Tax, State Tax, SS, Medicare, EIN, ZIP, State, IRS, and Schedule C. Update README and screenshots/docs.

18. Add tests:
    Add focused tests for settings consumption, GST inclusive/exclusive math, line GST codes, frontend/backend calculation agreement, document posting, credit memo reversals, bill input GST, report period handling, PAYE examples, and formatting.

19. Decide multi-currency scope:
    For a pure NZ fork, start with single-currency `NZD` formatting. Full multi-currency is a separate accounting feature involving exchange rates, realized gains/losses, bank-account currencies, and reporting currency.
