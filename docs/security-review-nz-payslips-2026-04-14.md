# Security Review — NZ Payslips (2026-04-14)

## Scope
Reviewed the payslip-output slice changes in:
- `app/routes/payroll.py`
- `app/services/pdf_service.py`
- `app/templates/payroll_payslip_pdf.html`
- `app/static/js/payroll.js`

## Checks performed
- Verified payslip output is restricted to processed pay runs only.
- Verified employee/run scoping is enforced on the backend before PDF generation.
- Reviewed the new template and UI actions for unsafe interpolation, file-path, or command-execution issues.
- Re-ran full repo tests, JS checks, `py_compile`, `node --check`, and `git diff --check`.

## Findings
### CRITICAL
- None found.

### HIGH
- None found.

### MEDIUM
1. **Payroll PII exposure risk remains unchanged at the app level**
   - Payslips add another employee-facing output over sensitive payroll data.
   - This slice does not introduce authentication/authorization, so the existing trusted-local/private deployment assumption remains the main residual risk.

### LOW
1. **Payslips are generated on demand only**
   - This avoids stored artifacts for now, but also means there is no later retrieval/audit trail for generated payslip files in this slice.
2. **Processed-run gating is the only publication control in this slice**
   - That is appropriate for the current single-user/local model, but multiuser RBAC will need stronger output permissions later.

## Positive controls
- Backend rejects payslips for draft runs.
- Backend rejects employee/run mismatches.
- No new dynamic file paths or shell execution were introduced.
- UI output continues to use escaped values for rendered payroll data before opening routes.
- PDF generation reuses the existing local template-based rendering stack.

## Overall assessment
- **No CRITICAL/HIGH regressions found for this slice.**
- **Residual risk remains MEDIUM** because payroll data and payslip output still live behind the repo's broader no-auth/local-trust model.
