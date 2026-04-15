# Company Service SQL Hardening Slice

## Summary
Fix the CodeQL finding in `app/services/company_service.py` by constraining company database names before they are used in dynamic PostgreSQL `CREATE/DROP/REVOKE` statements.

## Key Changes
- Add regression tests first for malicious company database names and valid creation behavior.
- Centralize backend validation for company database names so service helpers share one trust boundary.
- Reject invalid names before URL generation, SQL execution, or bootstrap work.
- Keep valid company creation behavior unchanged for UI-supported database names.

## Test Plan
- Add failing backend tests for SQL-metacharacter/traversal-style names and helper rejection paths.
- Re-run targeted company-service tests, full Python suite, JS tests, syntax checks, and `git diff --check`.

## Defaults
- Accept only the UI-supported lowercase letters, digits, and underscores for company database names.
- Keep the existing separate-database architecture and bootstrap flow.
- No new dependencies.
