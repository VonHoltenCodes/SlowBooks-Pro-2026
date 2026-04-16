# Plan: add chart-template loader buttons to Settings demo data

## Summary
Add Settings buttons that let an admin load either the Xero sample default chart of accounts or an MAS chart of accounts, with backend support that safely applies the selected template only when the ledger is still clean enough to replace its account structure.

## Key Changes
- Add chart-template loader actions under Settings → Demo Data for:
  - Xero sample default chart
  - MAS chart of accounts
- Add a protected settings API endpoint that accepts a template key and applies the selected chart template.
- Introduce reusable chart-template data and a loader service that clears/reseeds accounts and system-account role settings safely.
- Refuse to load a template when business/ledger activity already exists, so the action cannot corrupt a live ledger.

## Test Plan
- Backend tests for permissions, template selection, successful load on a clean ledger, and rejection when activity exists.
- Frontend settings test for the new buttons and API calls.
- Run targeted tests, full Python/JS suites as needed, and `git diff --check`.

## Constraints
- Keep the existing NZ/Xero chart as the current default/new-database seed.
- Treat MAS as an alternate template, not the new default.
- No schema migration should be required for a button-driven loader.
