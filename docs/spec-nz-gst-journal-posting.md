# NZ GST Journal Posting

## Problem Statement

SlowBooks Pro 2026 now calculates GST from line-level GST codes, but posting still uses a legacy "Sales Tax Payable" helper and label. New Zealand GST needs a GST control account that can hold output GST credits and input GST debits so the net balance represents GST owing to or from Inland Revenue.

The supplied Xero-style chart of accounts uses one current-liability `GST` account for GST owing to or from IRD, so this slice follows that single-control-account model.

## Scope

- Rename the legacy `2200 Sales Tax Payable` seed account to `2200 GST`.
- Add a GST account helper that replaces active sales-tax helper usage in GST posting paths.
- Migrate existing account `2200` names from `Sales Tax Payable` to `GST`.
- Post invoice and generated recurring invoice GST credits to account `2200 GST`.
- Post bill input GST and credit memo GST reversals as debits to account `2200 GST`.
- Update touched posting comments and split descriptions from "Sales tax" to "GST".
- Update localization planning docs to mark the GST posting account slice complete.

## Non-Scope

- No separate GST Collected or GST Paid accounts.
- No GST settlement/payment workflow.
- No GST return report.
- No posting lifecycle changes for edited documents.
- No IIF import/export localization changes.

## Acceptance Criteria

- `get_gst_account_id(db)` returns account `2200` and renames a legacy `Sales Tax Payable` account to `GST`.
- Existing active GST posting paths no longer call `get_sales_tax_account_id()`.
- Sales documents credit account `2200 GST` for output GST.
- Bills debit account `2200 GST` for input GST.
- Credit memos debit account `2200 GST`.
- Generated recurring invoices credit account `2200 GST`.
- Journal entries remain balanced.

## Affected Files / Modules

- `app/services/accounting.py`
- `app/seed/chart_of_accounts.py`
- `app/routes/invoices.py`
- `app/routes/bills.py`
- `app/routes/credit_memos.py`
- `app/services/recurring_service.py`
- `alembic/versions/*_rename_gst_account.py`
- `tests/test_gst_posting_accounts.py`
- `tests/test_document_gst_calculations.py`
- `docs/localization_summary.md`

## Test Plan

- Add failing tests for the GST account helper and legacy rename behavior.
- Add or update posting tests for invoice, bill, credit memo, and generated recurring invoice GST split account/direction.
- Compile touched Python modules and migration.
- Run the full Python test suite and diff whitespace check.
