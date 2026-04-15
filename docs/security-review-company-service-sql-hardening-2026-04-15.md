# Security Review — Company Service SQL Hardening (2026-04-15)

## Scope Reviewed
- `app/services/company_service.py`
- `tests/test_company_service_sql_hardening.py`
- `tests/test_company_database_bootstrap.py`

## Review Focus
- Whether user-controlled company database names can still alter PostgreSQL administrative SQL
- Whether invalid names are rejected before SQL execution, URL generation, or bootstrap work
- Whether valid multi-company creation behavior remains intact for supported names

## Findings
1. **Company database names now pass through one backend trust boundary**
   - `_validate_database_name()` enforces the same lowercase-letter/number/underscore contract the UI already advertises.
   - Invalid names now fail before database URL generation, `CREATE DATABASE`, `REVOKE CONNECT`, `DROP DATABASE`, or bootstrap execution.

2. **Administrative SQL no longer accepts arbitrary identifier text from user input**
   - Dynamic PostgreSQL DDL now uses only validated identifiers wrapped by `_quoted_database_name()`.
   - This removes the prior ability for quotes, semicolons, comments, or path-like input to flow into administrative SQL text.

3. **Regression coverage proves early rejection**
   - New tests verify malicious names do not reach `create_engine`/bootstrap paths and that helper accessors reject invalid identifiers.
   - Existing company bootstrap coverage still proves valid names continue through the normal create flow.

## Residual Risks
- The service still depends on elevated PostgreSQL permissions for company-database administration; compromise of an authorized admin account remains high impact by design.
- The branch still uses dynamic DDL for PostgreSQL database creation because parameter binding does not apply to identifiers; safety depends on the centralized validation remaining intact.

## Conclusion
- No new CRITICAL/HIGH issues identified in this slice.
- Residual risk is **LOW to MEDIUM**, tied to the inherent privilege level of company-database administration rather than uncontrolled SQL composition.
