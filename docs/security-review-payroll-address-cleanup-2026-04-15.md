# Security Review — Payroll Address Cleanup (2026-04-15)

## Scope Reviewed
- `app/schemas/payroll.py`
- `app/static/js/employees.js`
- `app/routes/employees.py`
- `tests/test_nz_payroll_data_model.py`
- `tests/test_employee_filing_export.py`
- `tests/js_nz_payroll_ui.test.js`

## Review Focus
- Whether exposing employee address fields changes payroll/private-data access boundaries
- Whether the slice keeps payroll filing output stable while allowing employee addresses to be captured and edited
- Whether the added UI/API fields preserve the existing NZ compatibility approach instead of introducing a risky schema change

## Findings
1. **Employee addresses now follow the same NZ-first compatibility model as other contacts**
   - The slice exposes existing `address1/address2/city/state/zip` fields rather than adding a migration or renaming storage fields.
   - UI labels use `Region` and `Postcode`, matching the NZ-facing approach already used elsewhere.

2. **No new auth boundary was introduced**
   - Employee CRUD remains behind the existing payroll/employee RBAC protections.
   - This slice exposes more employee profile data, but only through already-protected routes and UI.

3. **Employee filing exports remain format-stable**
   - Regression coverage confirms starter filing output shape is unchanged even when employee address fields are populated.
   - That reduces the risk of accidentally altering downstream filing workflows while cleaning up employee data entry.

## Residual Risks
- Payroll employee records now include more personally identifying address data; risk remains tied to the broader trusted-admin/private-app deployment model rather than this slice specifically.
- Deeper schema renames or expanded payroll-output use of employee addresses remain separate future changes and should be reviewed independently if attempted.

## Conclusion
- No new CRITICAL/HIGH issues identified in this slice.
- Residual risk is **LOW to MEDIUM**, driven mainly by the inherent sensitivity of payroll records rather than the compatibility-safe address exposure added here.
