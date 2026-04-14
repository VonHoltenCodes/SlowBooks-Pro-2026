# Xero Import Capability Specification

## Goal
Import Xero-exported accounting history into SlowBooks from report files, using GL as the source for journal reconstruction and Trial Balance / P&L / Balance Sheet as verification checkpoints.

## Required Behavior
- Accept a bundle of CSV files for Chart of Accounts, General Ledger, Trial Balance, Profit & Loss, and Balance Sheet.
- Detect file types by filename and parse common Xero-style column aliases.
- Dry-run must report missing files, parse errors, journal-balance issues, account-mapping issues, and verification mismatches.
- Import must create/update accounts from COA, create journal transactions from GL, and reject execution when dry-run verification fails.
- Verification must compare imported/simulated balances to Trial Balance and compare net totals to P&L / Balance Sheet totals.
- Add a protected UI workflow for upload, dry-run, import, and summary display.

## Constraints
- No new dependencies.
- CSV-only in this slice; no XLSX or live Xero API integration yet.
- Protect import routes/UI with the existing admin RBAC model.
- Preserve NZ-first accounting behavior and current report calculations.

## Verification
- Backend tests for dry-run, import, mismatch blocking, and imported-report equivalence.
- Frontend test for Xero import UI.
- Full Python/JS suites, syntax checks, and `git diff --check`.
