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

Payroll employee setup now uses an NZ-focused field set for IRD number, tax code, KiwiSaver, student loan, child support, ESCT, pay frequency, payroll address fields, start/end dates, and a per-pay child support amount. The Payroll page now supports draft pay runs using versioned NZ PAYE rules, calculates PAYE/ACC/student loan/KiwiSaver/ESCT/child support values, posts processed runs into NZ payroll liability accounts, generates payslip PDFs for processed pay runs, exports per-run Employment Information CSV files for IRD upload, supports starter/leaver employee filing, and now records filing audit/history state with generated/filed/amended/superseded tracking plus changed-since-filing detection.

The platform now also has a reusable authentication/RBAC foundation with session login, bootstrap/login/logout/user-management UX, role-based memberships, permission overrides, and protected payroll/employee/admin routes. Payroll is the first enforced protected domain, and core admin surfaces now use the same RBAC model; broader rollout to the remaining business modules still remains follow-up work.

Runtime system-account selection now resolves from explicit settings-backed roles with legacy fallback for key posting/default-selection paths, and fresh seed/bootstrap flows populate those role mappings automatically. This allows the branch default chart to move away from the old QB contractor numbering without breaking posting/default-account behavior.

Relevant files:

- `app/models/payroll.py`
- `app/schemas/payroll.py`
- `app/routes/payroll.py`
- `app/routes/employees.py`
- `app/services/nz_payroll.py`
- `app/static/js/payroll.js`
- `app/static/js/employees.js`

### Addresses

Customer and vendor records previously defaulted to `US` and used State/ZIP labels; the NZ branch now presents Region/Postcode wording while preserving storage compatibility.

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

The general reports UI now includes reusable period selection and custom date handling in `app/static/js/reports.js`. The legacy sales-tax surface is now an NZ GST Return report that produces GST101A box values, can generate a filled GST101A April 2023 PDF, and now surfaces bank-confirmed GST settlement state by matching reconciled bank transactions to GST periods. Box 9 and Box 13 adjustment values are entered at report-generation time, and the selected GST accounting basis comes from Company Settings.

Relevant files:

- `app/models/tax.py`
- `app/routes/tax.py`
- `app/services/tax_export.py`
- `app/static/js/tax.js`
- `README.md`

### Import And Export

CSV export now uses NZ-facing headers where SlowBooks owns the format, and the QuickBooks IIF surfaces now keep QB2003 wire compatibility while exporting/importing against the current NZ GST/address/account assumptions instead of stale US-facing sales-tax presentation.

Relevant files:

- `app/services/csv_import.py`
- `app/services/csv_export.py`
- `app/services/iif_import.py`
- `app/services/iif_export.py`
- `app/static/js/iif.js`


### Xero Import

A file-based Xero import workspace now exists for CSV exports of Chart of Accounts, General Ledger, Trial Balance, Profit & Loss, and Balance Sheet. It supports dry-run verification, historic journal reconstruction from the General Ledger export, and verification against Xero summary reports before import.

Relevant files:

- `app/services/xero_import.py`
- `app/routes/xero_import.py`
- `app/static/js/xero_import.js`

## Remaining Todo

The NZ-relevant carryovers from upstream commit `934244242d3a1a2802ba76de80f59f8a942c2c5e` are now implemented on this branch: payment voids, inline customer creation from invoice/estimate forms, manual journals, deposit recording, running-balance check-register workflows, credit-card charges, and vendor default expense accounts. Browser print-preview, US sales-tax payment, and cheque-printing remain intentionally out of scope for the NZ product surface.

Upstream commit `80b4bc782954aba5cdb93503f817e0776dc652c1` adds a second feature bundle. For SlowBooks NZ, the relevant carryovers are native Trial Balance and Cash Flow reports, batch overdue-statement emailing, NZ-localized collection workflows, bank rules, budget-vs-actual reporting, secure document attachments, and customizable email templates. The explicit non-port is US-only `1099` tracking. Late-fee automation is only conditionally relevant because NZ policy/legal/product rules need to be decided before automating charges or journals. Existing overlap to preserve: per-document email already exists, GST settlement already replaces the mainline sales-tax-payment shape, and any future templating work must keep sandboxing/autoescaping rather than reintroducing unsafe Jinja rendering.

Recommended priority order for the new `80b4bc782954aba5cdb93503f817e0776dc652c1` carryovers:

- **Priority 1 — Native Trial Balance and Cash Flow reports:** highest accountant value, no new policy surface, and closes a core in-app reporting gap the NZ branch still has.
- **Priority 2 — Batch overdue statement delivery:** builds directly on the existing shared statement/email infrastructure with lower risk than new document types.
- **Priority 3 — Bank rules:** meaningful banking productivity win, but should follow the current reconciliation/import foundations rather than precede them.
- **Priority 4 — Budget vs Actual:** useful management reporting once the core accountant-facing report surface is stronger.
- **Priority 5 — NZ-localized collection workflows:** depends on the shared email/PDF path and should follow the simpler batch-statement workflow first.
- **Priority 6 — Late-fee decision then automation (if approved):** keep after collection workflows because it is policy/legal/product gated, not just an engineering task.
- **Priority 7 — Secure document attachments:** relevant, but intentionally late because file upload/download work is high-risk and needs explicit hardening.
- **Priority 8 — Customizable email templates:** also intentionally late because it needs sandboxed + autoescaped rendering and should come after the base overdue/collection workflows are proven.

1. Keep NZ fork hygiene clean:
   Continue treating `nz-localization` as the canonical SlowBooks NZ branch. Do not reintroduce US tax, payroll, reporting, address, or seed-data behavior without an explicit product decision.

2. Maintain NZ-first foundations as new slices land:
   Reuse the existing NZ settings, formatting helpers, GST model/calculation stack, system-account role mappings, and Alembic bootstrap path rather than creating branch-specific alternatives.

3. Decide and implement the NZ income-tax replacement surface:
   Schedule C is retired on the NZ branch, but the replacement output still needs a product decision: IR3 business summary, IR10-style financial statements, accountant export, or another NZ-specific output.

4. Extend the auth model to future multi-company context switching:
   The platform now enforces RBAC across payroll, admin, core business modules, logo uploads, and retires the dormant legacy tax API surface. Remaining auth follow-up is future multi-company context switching and any new module surfaces added later.

5. Keep expanding focused regression coverage:
   Continue adding targeted tests whenever localized behavior changes, especially around GST math/reporting, GST settlement matching, posting lifecycle integrity, payroll calculations/outputs, filing history state, and settings-driven behavior.

6. Decide multi-currency scope explicitly:
   The branch currently assumes single-currency `NZD` formatting. Full multi-currency support remains a separate accounting feature involving exchange rates, realized gains/losses, account currencies, and reporting currency.

11. Add native accountant-facing Trial Balance and Cash Flow reports:
   The NZ branch can already verify imported Xero Trial Balance / P&L / Balance Sheet files, but it still lacks first-class in-app Trial Balance and Cash Flow reporting from the live ledger. Implement these as NZ-safe accounting reports rather than copying US-facing report labels or assumptions.

12. Add batch overdue statement delivery on top of the shared email path:
   Reuse the existing SMTP/email-log/document-PDF infrastructure to email statements to all overdue customers in one controlled batch, with RBAC, failure reporting, and opt-out-safe recipient handling.

13. Decide and implement NZ collection workflows before automating reminders:
   Add 30/60/90-day collection workflows only after localizing the wording, escalation behavior, and legal/accounting assumptions for NZ customers. Reuse the shared PDF/email foundation rather than introducing a separate document-delivery stack.

14. Decide late-fee product policy before building fee automation:
   Upstream late-fee settings and auto-journaling are not a straight carryover. First decide whether SlowBooks NZ should support late fees at all, what NZ disclosure/grace/rate rules apply, and whether fees should be suggested, manually approved, or automatically posted.

15. Add secure bank rules for imported transaction categorization:
   Auto-categorization by payee/pattern is relevant to NZ bank-import workflows, but it should integrate with the existing import/reconciliation model, support deterministic priority ordering, and stay behind the current auth/audit expectations.

16. Add budget-vs-actual workflows for monthly management reporting:
   Spreadsheet-style budget entry and variance reporting are NZ-relevant and should be built against the current chart/system-account model, not a separate US reporting fork.

17. Add attachments only with explicit upload/download hardening:
   Document attachments are relevant, but this is a high-risk file-handling slice. Any implementation must include RBAC, path/filename validation, content-type/size controls, safe storage layout, and download protections comparable to the backup-path hardening work.

18. Add customizable email templates only with sandboxed rendering:
   Template customization is relevant because the branch already has shared document-email flows, but future implementation must use sandboxed rendering plus autoescaping and must not allow arbitrary template execution or unsafe HTML output.
