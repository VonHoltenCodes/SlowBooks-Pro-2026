# New Zealand Localization Summary

This document captures the current localization audit for turning SlowBooks Pro 2026 into a New Zealand-localized version.

The `nz-localization` branch is now the authoritative SlowBooks NZ product branch. Treat it as a New Zealand-localized product fork, not as a light patch set over the US-shaped upstream. See `docs/nz-fork-policy.md` for branch roles and upstream-sync rules.

## Assessment

The app is strongly US-shaped in tax, payroll, addresses, reports, PDF/UI copy, seed data, and import/export behavior. The broad direction from the external review is accurate, but the code audit found several additional surfaces that need attention:

- Purchase-side and sales-side GST now post through a single `2200 GST` control account. The account balance represents GST owing to or from Inland Revenue, following the Xero-style single GST account model.
- Import/export paths are a major localization surface. CSV and IIF logic use State/ZIP conventions, QuickBooks-style address parsing, and "Sales Tax Payable" labels.
- GST calculation is now centralized for invoices, bills, credit memos, purchase orders, estimates, and generated recurring invoices.
- Document forms now use line-level GST codes for GST calculation. The legacy `default_tax_rate` setting still exists for compatibility, but it is no longer the GST design for NZ documents.
- Upstream added shared report period UI for several reports. Reuse that flow for GST returns instead of building a separate date picker.
- Posted invoice updates now reverse the prior journal entry and post a replacement when lines are changed. Invoice duplication, estimate-to-invoice conversion, and purchase-order-to-bill conversion now route through the normal posting paths so converted/duplicated documents carry balanced journal entries.
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

Document forms now load GST codes and use line-level GST logic for frontend previews and save payloads. Keep new GST UI work aligned with that path rather than reintroducing document-level tax-rate calculation.

### GST Posting

The app uses account `2200 GST` as the GST control account. Sales/output GST credits this account; purchase/input GST and credit memo reversals debit this account. Settlement/payment workflows remain separate work.

Posted invoice line edits reverse the original journal and post a replacement journal so account balances track recalculated line-level GST. Invoice duplication, estimate conversion, and purchase order conversion reuse normal invoice/bill creation paths so their journals are posted consistently. Bill voids now share the generic reversal helper and closing-date guard.

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

Schedule C storage and legacy code paths still exist, but the NZ branch now disables the active Schedule C routes and removes the Tax Reports navigation entry so US income-tax output is no longer exposed to end users.

The general reports UI now includes reusable period selection and custom date handling in `app/static/js/reports.js`. The Sales Tax report is now a GST Return report that produces GST101A box values and can generate a filled GST101A April 2023 PDF. Box 9 and Box 13 adjustment values are entered at report-generation time, and the selected GST accounting basis comes from Company Settings.

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

1. Keep NZ fork hygiene clean:
   Work on `nz-localization` as the canonical SlowBooks NZ branch. Treat `main` and any external upstream as reference sources only, cherry-pick generic fixes selectively, and do not restore US tax, payroll, reporting, address, or seed-data behavior without an explicit product decision. Re-check this plan for newly touched localized surfaces before each implementation slice.

2. Maintain the localization foundation:
   Settings for `country=NZ`, `tax_regime=NZ`, `currency=NZD`, `locale=en-NZ`, `timezone=Pacific/Auckland`, `ird_number`, `gst_number`, `gst_registered`, `gst_basis`, `gst_period`, and `prices_include_gst` now exist. Add tests when consuming these settings from formatting, GST, reports, imports, or payroll.

3. Centralize formatting:
   Shared Python and frontend formatting helpers are in place and wired into known PDF, email, report, transaction, dashboard, settings, and company-list date/currency surfaces. Keep using these helpers for new GST, payroll, address, CSV, IIF, and reporting work.

4. Refactor address fields without breaking data:
   Label/default remapping is in place for company, customer, vendor, PDF/email, CSV, and IIF surfaces while preserving existing database/API field names. Payroll employee address fields and any future schema renames remain separate work.

5. Create a GST domain model:
   GST code reference data is now modeled and seeded with `GST15`, `ZERO`, `EXEMPT`, and `NO_GST`, with a read-only API for downstream line-level GST work. Consider `IMPORT` or `REVERSE_CHARGE` later if needed.

6. Store GST per line:
   GST code and rate snapshots are stored on invoice, estimate, credit memo, bill, purchase order, and recurring invoice lines. Later calculation and posting slices now consume these line GST fields.

7. Build one GST calculation service:
   Shared GST calculation is now in place for invoices, estimates, bills, purchase orders, credit memos, and generated recurring invoices. Backend totals use line-level GST codes with exclusive/inclusive pricing support, including `3/23` GST-inclusive extraction for the standard 15% code. Frontend document forms now load GST codes, save per-line GST choices, and preview totals with the same line GST logic. Existing `tax_rate` fields remain compatibility fields rather than the authoritative GST design.

8. Rework journal posting:
   Journal posting now uses a Xero-style single `2200 GST` control account. Seed and migration logic rename legacy `Sales Tax Payable` to `GST`, invoices and generated recurring invoices credit GST, bills debit input GST, and credit memos debit GST reversals. GST settlement/reporting splits remain separate work.

9. Fix posting lifecycle behavior:
   Posted invoice line edits now reverse/repost journal entries. Invoice duplicates, estimate-to-invoice conversions, and purchase-order-to-bill conversions now post through normal create paths, and bill voids use the shared reversal helper with closing-date protection. Remaining lifecycle hardening should cover any future edit routes for bills or credit memos and decide how much non-line invoice metadata may change after posting.

10. Replace the sales tax report with a GST return report:
    The Reports UI now exposes a GST Return flow using the shared period selector. It calculates GST101A Boxes 5-15, supports invoice and payments basis from Settings, accepts Box 9 and Box 13 adjustments before generation, includes source drilldowns, and generates a filled `gst101a-2023.pdf`. The old `/api/reports/sales-tax` endpoint remains a compatibility alias.

11. Replace Schedule C:
    Schedule C routes/UI are now hidden or disabled for SlowBooks NZ. Future work should replace them with NZ income-tax outputs only after deciding whether the target is IR3 business summary, IR10-style financial statements, or accountant export.

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
