# Company Service SQL Hardening Specification

## Goal
Ensure user-controlled company database names cannot alter or escape the intended PostgreSQL administrative SQL in `company_service.py`.

## Required Behavior
- Company database names must be treated as untrusted input.
- Invalid names must be rejected before any SQL execution or bootstrap work.
- Backend validation must align with the UI contract for company database names.
- Valid company creation and cleanup flows must continue working for supported names.

## Constraints
- Preserve the current company-per-database architecture.
- Keep changes localized to the company service and focused tests/docs.
- No new dependencies.
- Prefer one shared validator for SQL and URL helper paths.

## Verification
- Backend tests proving malicious names are rejected before SQL/bootstrapping and valid names still work.
- Full Python suite, JS tests, syntax checks, and `git diff --check`.
