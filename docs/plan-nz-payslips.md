# NZ Payslip Output Slice

## Summary
Add NZ payslip PDF output for processed pay runs only, reusing the existing WeasyPrint/Jinja PDF stack and current processed payroll data.

## Key Changes
- Add a payroll payslip PDF endpoint for an employee within a processed pay run.
- Add a dedicated payslip PDF template with NZ payroll labels and processed run/stub/company data.
- Expose payslip actions in the Payroll UI for processed runs.
- Keep payday filing/export and app-wide RBAC/privacy hardening out of scope for this slice.

## Test Plan
- Backend tests for processed-run PDF generation, draft-run rejection, and wrong employee/run rejection.
- PDF formatting test for NZ labels and rendered values.
- Frontend test for payslip action visibility on processed runs.
- Full repo verification plus explicit payroll security review.

## Defaults
- Payslips are generated on demand and not stored separately.
- Draft runs do not expose payslip output.
