# Xero Import Capability Slice

## Summary
Add a file-based Xero import MVP that ingests exported CSVs for Chart of Accounts, General Ledger, Trial Balance, Profit & Loss, and Balance Sheet, runs a dry-run verification pass, and imports historic ledger data into SlowBooks.

## Key Changes
- Add a Xero import parser/normalizer for the five required CSV exports.
- Add dry-run validation and report verification against Trial Balance, P&L, and Balance Sheet.
- Add import execution that creates/updates accounts and reconstructs journal history from the General Ledger export.
- Add a protected Xero import admin UI under Interop with upload, dry-run, and import actions.
- Add docs/security review and regression tests.

## Test Plan
- Add failing parser/dry-run/import tests first.
- Add a frontend test for the Xero import UI rendering and actions.
- Re-run full Python/JS verification, syntax checks, and `git diff --check`.

## Defaults
- Phase 1 is CSV file import only (no live Xero API/OAuth).
- Require the five core files: COA, GL, Trial Balance, P&L, Balance Sheet.
- Historic journal import is the primary mode; opening-balance-only fallback is out of scope for this slice.
