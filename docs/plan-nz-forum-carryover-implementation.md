# Plan: implement NZ-relevant upstream carryover features

## Goal
Implement the NZ-relevant carryover features identified from upstream commit `934244242d3a1a2802ba76de80f59f8a942c2c5e` without reintroducing US-specific sales-tax behavior.

## Scope
- Customer payment voiding with invoice-balance restoration and reversing journals
- Inline customer creation from invoice and estimate entry flows
- Manual journal entry workflow
- Deposit recording from Undeposited Funds / Receipt Clearing into a bank account
- Running-balance bank/check-register view
- Credit-card charge entry against NZ chart accounts
- Vendor default expense-account fallback for bills

## Implementation steps
1. Add the required data-model changes (payment void flag, deposit linkage, vendor default expense account) plus Alembic migration coverage.
2. Add backend routes/schemas/services for payment voiding, deposits, manual journals, check register, and credit-card charges using existing journal/system-account foundations.
3. Update invoice, estimate, payments, vendors, app shell, and navigation UI to expose inline customer creation and the new accounting/banking workflows.
4. Add targeted backend and frontend regression tests for the new flows and NZ account-role behavior.
5. Refresh the localization summary / todo state to reflect completed carryover features, run verification, and write a security review.

## Constraints
- Reuse the existing transaction journal engine, RBAC model, system-account role helpers, and NZ chart assumptions.
- Do not add a US sales-tax payment surface; GST settlement already covers the NZ tax-payment workflow.
- Keep account selection settings-aware instead of hard-coding US chart numbers wherever possible.
