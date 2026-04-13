# NZ Payroll Data Model Rebuild

## Summary
Replace the US employee payroll setup shape with NZ payroll setup fields and disable incorrect payroll processing until PAYE is implemented.

## Key Changes
- Replace SSN / filing status / allowances with IRD number, tax code, KiwiSaver fields, student loan, child support, ESCT rate, pay frequency, and start/end dates.
- Keep Payroll navigation visible but render a placeholder page instead of US withholding/pay-run UI.
- Return explicit NZ placeholder errors from payroll endpoints until PAYE and payslips are implemented.
- Add the Alembic migration and update README/localization docs.

## Test Plan
- Backend tests for employee CRUD with NZ fields and payroll endpoint disablement.
- Frontend tests for NZ employee form fields and payroll placeholder content.
- Verify with unit tests, JS tests, syntax checks, and `git diff --check`.

## Assumptions
- PAYE, KiwiSaver deductions, student loan calculations, ESCT calculations, and NZ payslips are separate follow-on slices.
- This branch may break from the previous US payroll API shape because `nz-localization` is the authoritative NZ product branch.
